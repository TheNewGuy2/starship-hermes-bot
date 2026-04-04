from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.context_overnight.logic import (
    ContextPriors,
    compute_context_priors,
    overnight_window_utc,
)
from starship_engine.apps.exit_planner.planner import build_exit_plan
from starship_engine.apps.exit_planner.types import ExitPlan, ExitPlannerConfig
from starship_engine.apps.heston.logic import HestonGateParams, HestonGateResult
from starship_engine.condor import Condor
from starship_engine.domain.types import BarFeatures
from starship_engine.indicators import CandleBar

log = get_logger("starship_engine.stream_health")


@dataclass
class StatsHandler:
    bars: int = 0
    touch: int = 0
    gate_ok: int = 0
    data_ready: int = 0
    condor_built: int = 0
    slack_sent: int = 0
    blocked_touch: int = 0
    blocked_gate: int = 0
    blocked_data: int = 0
    blocked_slack: int = 0
    blocked_dry_run: int = 0
    blocked_time: int = 0

    def on_candle(
        self,
        *,
        features,
        gate,
        data_ready: bool,
        **_,
    ) -> None:
        self.bars += 1
        if features.touch:
            self.touch += 1
        if gate.ok:
            self.gate_ok += 1
        if data_ready:
            self.data_ready += 1

    def on_event(self, *, name: str, payload: dict[str, object]) -> None:
        if name == "condor_built":
            self.condor_built += 1
        elif name == "slack_sent":
            sent = bool(payload.get("sent", False))
            if sent:
                self.slack_sent += 1
            else:
                self.blocked_slack += 1
        elif name == "blocked":
            reason = str(payload.get("reason", ""))
            if reason == "dry_run":
                self.blocked_dry_run += 1
            elif reason == "no_touch":
                self.blocked_touch += 1
            elif reason == "gate_fail":
                self.blocked_gate += 1
            elif reason in ("no_data", "data_not_ready"):
                self.blocked_data += 1
            elif reason == "slack":
                self.blocked_slack += 1
            elif reason == "time":
                self.blocked_time += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "bars": self.bars,
            "touch": self.touch,
            "gate_ok": self.gate_ok,
            "data_ready": self.data_ready,
            "condor_built": self.condor_built,
            "slack_sent": self.slack_sent,
            "blocked_touch": self.blocked_touch,
            "blocked_gate": self.blocked_gate,
            "blocked_data": self.blocked_data,
            "blocked_slack": self.blocked_slack,
            "blocked_dry_run": self.blocked_dry_run,
            "blocked_time": self.blocked_time,
        }


@dataclass
class IdleCheckResult:
    idle_seconds: float
    warned: bool
    slack_sent: bool
    warned_no_candle: bool


class StreamHealthHandler:
    def __init__(
        self,
        *,
        idle_warn_seconds: int,
        idle_slack_cooldown: int,
        on_idle: Callable[[float, bool], None] | None = None,
    ) -> None:
        self.idle_warn_seconds = idle_warn_seconds
        self.idle_slack_cooldown = idle_slack_cooldown
        self.on_idle = on_idle
        self.last_any_stream_event_ts: float = 0.0
        self.last_stream_idle_alert_ts: float = 0.0
        self.seen_valid_candle: bool = False

    def mark_event(self, kind: str) -> None:
        loop = asyncio.get_running_loop()
        self.last_any_stream_event_ts = loop.time()
        log.debug("[LIVENESS] event=%s", kind)

    def mark_valid_candle(self) -> None:
        self.seen_valid_candle = True

    def on_event(self, *, name: str, payload: dict[str, object]) -> None:
        if name == "stream_event":
            kind = payload.get("kind")
            if isinstance(kind, str):
                self.mark_event(kind)
        elif name == "valid_candle":
            self.mark_valid_candle()

    def check_idle(self, *, now: float) -> IdleCheckResult:
        if self.last_any_stream_event_ts == 0.0:
            self.last_any_stream_event_ts = now
        idle = now - self.last_any_stream_event_ts
        warned = False
        slack_sent = False
        warned_no_candle = False

        if idle >= self.idle_warn_seconds:
            warned = True
            log.warning("[WATCHDOG] No stream events for %.1fs", idle)

        if not self.seen_valid_candle and idle >= max(120, self.idle_warn_seconds):
            warned_no_candle = True
            log.warning(
                "[WATCHDOG] No valid candle seen yet (permissions/symbol/market?)."
            )

        if warned and self.on_idle:
            self.on_idle(idle, warned_no_candle)

        return IdleCheckResult(
            idle_seconds=idle,
            warned=warned,
            slack_sent=slack_sent,
            warned_no_candle=warned_no_candle,
        )

    async def watchdog(self, *, stop_event: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        if self.last_any_stream_event_ts == 0.0:
            self.last_any_stream_event_ts = loop.time()

        while not stop_event.is_set():
            await asyncio.sleep(10)
            now = loop.time()
            self.check_idle(now=now)


@dataclass
class EsContextHandler:
    enabled: bool
    _candles_by_time: dict[int, CandleBar]
    latest_priors: Optional[ContextPriors] = None

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._candles_by_time = {}
        self.latest_priors = None

    def on_context_candle(self, bar: CandleBar) -> None:
        if not self.enabled:
            return
        if bar.time_ms is None:
            return
        self._candles_by_time[int(bar.time_ms)] = bar

    def on_event(self, *, name: str, payload: dict[str, object]) -> None:
        if name != "context_candle":
            return
        bar = payload.get("bar")
        if isinstance(bar, CandleBar):
            self.on_context_candle(bar)

    def compute_priors(self) -> Optional[ContextPriors]:
        if not self.enabled:
            self.latest_priors = None
            return None

        start_utc, end_utc = overnight_window_utc()
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(end_utc.timestamp() * 1000)
        prune_before_ms = int((start_utc - timedelta(hours=6)).timestamp() * 1000)
        if len(self._candles_by_time) > 2000:
            for k in list(self._candles_by_time):
                if k < prune_before_ms:
                    del self._candles_by_time[k]

        bars = [b for t, b in self._candles_by_time.items() if start_ms <= t < end_ms]
        bars.sort(key=lambda b: b.time_ms or 0)
        self.latest_priors = compute_context_priors(bars)
        return self.latest_priors

    def adjust_gate_params(self, base_params: HestonGateParams) -> HestonGateParams:
        if not self.enabled or not self.latest_priors or not self.latest_priors.regime:
            return base_params

        if self.latest_priors.regime == "calm":
            return HestonGateParams(
                enabled=base_params.enabled,
                vrp_min=max(0.5, base_params.vrp_min - 0.10),
                vrp_max=base_params.vrp_max,
                mr_max=base_params.mr_max + 0.02,
                rv60_min=max(0.01, base_params.rv60_min - 0.01),
                vov_max=base_params.vov_max,
            )

        if self.latest_priors.regime == "hot":
            return HestonGateParams(
                enabled=base_params.enabled,
                vrp_min=base_params.vrp_min + 0.10,
                vrp_max=base_params.vrp_max,
                mr_max=max(0.0, base_params.mr_max - 0.02),
                rv60_min=base_params.rv60_min + 0.01,
                vov_max=base_params.vov_max,
            )

        return base_params


@dataclass
class ExitPlannerHandler:
    cfg: ExitPlannerConfig
    vov_series: deque[float]

    def update_vov(self, vov: float) -> None:
        if vov == vov:
            self.vov_series.append(float(vov))

    def build_plan(
        self,
        *,
        features: BarFeatures,
        gate: HestonGateResult,
        greeks_by_symbol: Optional[dict[str, object]],
        condor: Optional[Condor],
        spot: Optional[float],
    ) -> ExitPlan:
        vov_vals = list(self.vov_series)
        return build_exit_plan(
            features=features,
            gate=gate,
            greeks_by_symbol=greeks_by_symbol,
            condor=condor,
            spot=spot,
            vov_series=vov_vals,
            cfg=self.cfg,
        )
