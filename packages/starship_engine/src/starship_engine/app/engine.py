from __future__ import annotations

import asyncio
import math
import signal
from collections import deque
from dataclasses import asdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from starship_shared.enums import FactKind
from starship_shared.options import compute_iv_0dte_band
from starship_shared.schemas import (
    CandleBarV1,
    EngineFactV1,
    MarketRef,
    OptionSnapshotV1,
)
from starship_shared.sentinel import MarketSnapshot

from starship_engine.app.state import EngineState
from starship_engine.apps.data_persist.store import CandleStore, OptionsStore
from starship_engine.apps.exit_planner.targets import compute_exit_score
from starship_engine.apps.heston.logic import HestonGateResult, evaluate_heston_gate
from starship_engine.apps.stream.stream import StreamConfig, run_stream
from starship_engine.apps.stream.symbols import SPX_INDEX_SYMBOL
from starship_engine.apps.trend_risk.logic import compute_trend_risk
from starship_engine.condor import pick_condor_20delta_20wide
from starship_engine.core.utils import (
    append_reason,
    cap_exit_plan_for_trend,
    dt_from_ms,
    in_signal_window,
    is_near_live,
    is_today,
    ok_mark,
    utc_iso,
)
from starship_engine.domain.decision import decide, evaluate_early
from starship_engine.domain.types import BarFeatures
from starship_engine.indicators import (
    CandleBar,
    CandleBuffer,
    annualized_realized_vol,
    atr,
    close_near_ema,
    ema,
    log_returns,
    vov_proxy_from_returns,
)

TZ_CST = ZoneInfo("America/Chicago")


class Engine:
    def __init__(self, deps: Any) -> None:
        self.deps = deps
        maxlen = max(600, deps.runtime.backfill_bars + 50)
        buf = CandleBuffer(maxlen=maxlen)
        self.state = EngineState(
            buf=buf,
            stop_event=asyncio.Event(),
            stats=deps.stats,
            state_log_handler=deps.state_log_handler,
            context_handler=deps.context_handler,
            stream_health=deps.stream_health,
            early_config=deps.early_config,
            trend_risk_vov_series=deque(maxlen=deps.exit_planner_cfg.vov_window),
            exit_planner=deps.exit_planner,
            context_enabled=deps.context_enabled,
            context_symbol=deps.runtime.context_symbol,
            exec_symbol=deps.runtime.exec_symbol,
            signal_mode=deps.runtime.signal_mode,
            ignore_time_window=deps.runtime.ignore_time_window,
            dry_run=deps.runtime.dry_run,
            warmup_bars=deps.runtime.warmup_bars,
            backfill_bars=deps.runtime.backfill_bars,
            candle_minutes_back_override=deps.runtime.candle_minutes_back_override,
            backfill_prev_session_open=deps.runtime.backfill_prev_session_open,
            context_exchange=deps.runtime.context_exchange,
            context_code=deps.runtime.context_code,
            engine_run_id=deps.engine_run_id,
        )
        self.candle_store = None
        self.options_store = None
        if getattr(deps.engine_settings, "market_persist", None):
            mp = deps.engine_settings.market_persist
            if mp.candles.enabled:
                self.candle_store = CandleStore(
                    out_dir=Path(mp.candles.out_dir),
                    prefix=mp.candles.file_prefix,
                )
            if mp.options.enabled:
                self.options_store = OptionsStore(
                    out_dir=Path(mp.options.out_dir),
                    prefix=mp.options.file_prefix,
                )
        if self.deps.stream_health:
            self.deps.stream_health.on_idle = self._on_stream_idle

        if self.deps.trend_risk_cfg.policy not in (
            "veto_or_tier",
            "tier_only",
            "warn_only",
        ):
            self.deps.log.warning(
                "Unknown TREND_RISK_POLICY=%s; defaulting to warn_only",
                self.deps.trend_risk_cfg.policy,
            )
            self.deps.trend_risk_cfg.policy = "warn_only"

    def _emit_event(self, name: str, payload: dict[str, object]) -> None:
        if self.state.stream_health:
            self.state.stream_health.on_event(name=name, payload=payload)
        if self.state.stats:
            self.state.stats.on_event(name=name, payload=payload)
        if self.state.state_log_handler:
            self.state.state_log_handler.on_event(name=name, payload=payload)
        if self.state.context_handler:
            self.state.context_handler.on_event(name=name, payload=payload)

    def _emit_candle(
        self,
        *,
        bar_index: int,
        features: BarFeatures,
        gate: HestonGateResult,
        data_ready: bool,
        exit_plan: Optional[object],
        shadow: Optional[dict[str, object]],
    ) -> None:
        if self.state.stats:
            self.state.stats.on_candle(
                bar_index=bar_index,
                features=features,
                gate=gate,
                data_ready=data_ready,
                context_priors=self.state.latest_context_priors,
                signal_mode=self.state.signal_mode,
                exit_plan=exit_plan,
                shadow=shadow,
            )
        if self.state.state_log_handler:
            self.state.state_log_handler.on_candle(
                bar_index=bar_index,
                features=features,
                gate=gate,
                data_ready=data_ready,
                context_priors=self.state.latest_context_priors,
                signal_mode=self.state.signal_mode,
                exit_plan=exit_plan,
                shadow=shadow,
            )

    def _emit_signal(
        self,
        *,
        bar_index: int,
        features: BarFeatures,
        gate: HestonGateResult,
        condor: Any,
        exit_plan: Optional[object],
        exit_plan_row: Optional[object],
        slack_ready: bool,
        signal_ctx: dict[str, object],
        exit_planner_enabled: bool,
        exit_planner_prefix: str | None,
        shadow: Optional[dict[str, object]],
    ) -> None:
        if self.state.state_log_handler:
            self.state.state_log_handler.on_signal(
                bar_index=bar_index,
                features=features,
                gate=gate,
                condor=condor,
                exit_plan=exit_plan,
                exit_plan_row=exit_plan_row,
                slack_ready=slack_ready,
                signal_mode=self.state.signal_mode,
                context_priors=self.state.latest_context_priors,
                signal_ctx=signal_ctx,
                exit_planner_enabled=exit_planner_enabled,
                exit_planner_prefix=exit_planner_prefix,
                shadow=shadow,
            )

    def _on_stream_idle(self, idle_seconds: float, warned_no_candle: bool) -> None:
        self.publish_engine_event(
            "stream_idle",
            {
                "idle_seconds": float(idle_seconds),
                "warned_no_candle": bool(warned_no_candle),
            },
        )

    def mark_stream_event(self, kind: str) -> None:
        self._emit_event("stream_event", {"kind": kind})

    def _backfill_gap_stats(self, times: list[int]) -> tuple[int, int]:
        if len(times) < 2:
            return 0, 0
        expected_ms = 5 * 60 * 1000
        gap_count = 0
        max_gap_ms = 0
        for prev, cur in zip(times, times[1:]):
            delta = cur - prev
            if delta > expected_ms * 1.2:
                gap_count += 1
                if delta > max_gap_ms:
                    max_gap_ms = delta
        return gap_count, max_gap_ms

    def _bars_since_session_open(self, t_ms: int) -> int:
        dt = dt_from_ms(t_ms, TZ_CST)
        open_dt = datetime.combine(dt.date(), time(8, 30), tzinfo=TZ_CST)
        if dt < open_dt:
            return 0
        minutes = int((dt - open_dt).total_seconds() // 60)
        return max(1, (minutes // 5) + 1)

    def _backfill_target(self) -> int:
        warmup = max(0, self.state.warmup_bars or 0)
        backfill = max(0, self.state.backfill_bars or 0)
        desired = warmup
        if backfill:
            desired = min(warmup or backfill, backfill)
        return max(2, desired)

    def _seed_backfill(self, bars: list[CandleBar], current: CandleBar) -> None:
        if not bars:
            return
        by_time: dict[int, CandleBar] = {}
        for b in bars:
            if b.time_ms is None:
                continue
            by_time[int(b.time_ms)] = b
        if not by_time:
            return
        times = sorted(by_time.keys())
        current_t = int(current.time_ms) if current.time_ms is not None else None
        if current_t is not None and current_t in by_time:
            times = [t for t in times if t != current_t]
        seeded = [by_time[t] for t in times]
        gap_count, max_gap_ms = self._backfill_gap_stats(times)
        if gap_count:
            self.deps.log.warning(
                "[BACKFILL] detected %d gap(s); max_gap=%.1f min",
                gap_count,
                max_gap_ms / 60000.0,
            )
        for b in seeded:
            self.state.buf.append(b)
        if seeded:
            self.state.last_closed_t = seeded[-1].time_ms
            self.deps.log.info(
                "[BACKFILL] seeded=%d oldest=%s newest=%s",
                len(seeded),
                dt_from_ms(seeded[0].time_ms, TZ_CST).strftime("%Y-%m-%d %H:%M"),
                dt_from_ms(seeded[-1].time_ms, TZ_CST).strftime("%Y-%m-%d %H:%M"),
            )

    def compute_features_and_gate(self, bar: CandleBar):
        log = self.deps.log
        trend_risk_cfg = self.deps.trend_risk_cfg
        exit_planner_cfg = self.deps.exit_planner_cfg
        gate_params = self.deps.gate_params
        context_handler = self.state.context_handler
        closes = self.state.buf.closes()
        highs = self.state.buf.highs()
        lows = self.state.buf.lows()

        ema21_arr = ema(closes, 21)
        atr5_arr = atr(highs, lows, closes, 5)

        ema21 = float(ema21_arr[-1]) if len(ema21_arr) else float("nan")
        atr5v = float(atr5_arr[-1]) if len(atr5_arr) else float("nan")
        trend_atr = atr5v
        if (
            trend_risk_cfg.enabled
            and trend_risk_cfg.stretch_atr_len != 5
            and len(closes) >= trend_risk_cfg.stretch_atr_len + 1
        ):
            atr_trend_arr = atr(
                highs, lows, closes, int(trend_risk_cfg.stretch_atr_len)
            )
            if len(atr_trend_arr):
                trend_atr = float(atr_trend_arr[-1])

        ema_touch = False
        if ema21 == ema21 and atr5v == atr5v:
            ema_touch = close_near_ema(bar.close, ema21, atr_value=atr5v, atr_mult=0.15)
        if not self.deps.runtime.require_ema_touch:
            ema_touch = True

        rv30 = rv60 = rv90 = float("nan")
        mr = vov = float("nan")

        max_abs_5m_return = float("nan")
        if len(closes) >= 19:
            rets_30 = log_returns(closes[-7:])
            rets_60 = log_returns(closes[-13:])
            rets_90 = log_returns(closes[-19:])

            rv30 = annualized_realized_vol(rets_30)
            rv60 = annualized_realized_vol(rets_60)
            rv90 = annualized_realized_vol(rets_90)

            mr = rv30 - rv90
            vov = vov_proxy_from_returns(rets_60)
            if rets_60.size:
                max_abs_5m_return = float(
                    max(abs((math.exp(r) - 1.0)) for r in rets_60)
                )

        mid = self.state.latest_spx_mid
        if mid is None:
            last_bar_mid = self.state.buf.last()
            if last_bar_mid and last_bar_mid.close > 0:
                mid = last_bar_mid.close

        iv_atm = None
        iv_disp = None
        nC = nP = parse_fail = 0
        if mid is not None:
            iv_atm, iv_disp, nC, nP, parse_fail = compute_iv_0dte_band(
                greeks_by_symbol=self.state.greeks_by_symbol,
                spot=mid,
            )

        log.info(
            "[IV] iv0dte=%s disp=%s nC=%d nP=%d parse_fail=%d spot=%s",
            (f"{iv_atm:.4f}" if iv_atm is not None else "None"),
            (f"{iv_disp:.4f}" if iv_disp is not None else "None"),
            nC,
            nP,
            parse_fail,
            (f"{mid:.2f}" if mid is not None else "None"),
        )

        dt = dt_from_ms(bar.time_ms or 0, TZ_CST)

        self.state.latest_context_priors = context_handler.compute_priors()
        gate_params_eval = context_handler.adjust_gate_params(gate_params)

        trend_result = None
        if trend_risk_cfg.enabled:
            trend_result = compute_trend_risk(
                highs=highs,
                lows=lows,
                closes=closes,
                ema21=ema21,
                atr_value=trend_atr,
                rv30=rv30,
                rv60=rv60,
                cfg=trend_risk_cfg,
            )

        features = BarFeatures(
            dt=dt,
            bar=bar,
            ema21=ema21,
            atr5=atr5v,
            rv30=rv30,
            rv60=rv60,
            rv90=rv90,
            vov=vov,
            iv_atm=iv_atm,
            iv_disp=iv_disp,
            n_calls=nC,
            n_puts=nP,
            parse_fail=parse_fail,
            latest_spx_mid=self.state.latest_spx_mid,
            greeks_symbols=len(self.state.greeks_by_symbol),
            touch=ema_touch,
            trend=trend_result,
        )

        if self.state.context_enabled and self.state.latest_context_priors:
            log.info(
                "[CONTEXT] regime=%s rv60=%s vov=%s range_pct=%s score=%.2f",
                (self.state.latest_context_priors.regime or "n/a"),
                (
                    f"{self.state.latest_context_priors.overnight_rv60:.6f}"
                    if isinstance(
                        self.state.latest_context_priors.overnight_rv60, (int, float)
                    )
                    else "n/a"
                ),
                (
                    f"{self.state.latest_context_priors.overnight_vov:.6f}"
                    if isinstance(
                        self.state.latest_context_priors.overnight_vov, (int, float)
                    )
                    else "n/a"
                ),
                (
                    f"{self.state.latest_context_priors.overnight_range_pct:.4f}"
                    if isinstance(
                        self.state.latest_context_priors.overnight_range_pct,
                        (int, float),
                    )
                    else "n/a"
                ),
                self.state.latest_context_priors.score,
            )

        gate = evaluate_heston_gate(
            enabled=gate_params_eval.enabled,
            iv_atm=features.iv_atm,
            rv30=features.rv30,
            rv60=features.rv60,
            rv90=features.rv90,
            vov=features.vov,
            params=gate_params_eval,
        )
        self.state.last_features = features
        self.state.last_gate = gate
        if gate.vov == gate.vov:
            self.state.trend_risk_vov_series.append(float(gate.vov))
        if self.state.exit_planner:
            self.state.exit_planner.update_vov(gate.vov)

        if trend_risk_cfg.enabled and trend_result is not None:
            trend_policy = trend_risk_cfg.policy
            trend_reason = None
            if trend_result.risk_tier == "block":
                trend_reason = "trend_risk_block"
                if trend_policy == "veto_or_tier":
                    gate = HestonGateResult(
                        False,
                        append_reason(gate.reason, trend_reason),
                        gate.vrp,
                        gate.mr,
                        gate.vov,
                    )
                else:
                    gate = HestonGateResult(
                        gate.ok,
                        append_reason(gate.reason, trend_reason),
                        gate.vrp,
                        gate.mr,
                        gate.vov,
                    )
            elif trend_result.risk_tier == "caution":
                trend_reason = "trend_risk_caution"
                if trend_policy == "veto_or_tier":
                    score_result = compute_exit_score(
                        features=features,
                        gate=gate,
                        vov_series=list(self.state.trend_risk_vov_series),
                        vov_stable_threshold=exit_planner_cfg.vov_stable_threshold,
                        vov_window=exit_planner_cfg.vov_window,
                    )
                    is_tier_a = score_result.score >= exit_planner_cfg.tier_a_min_score
                    if not is_tier_a:
                        gate = HestonGateResult(
                            False,
                            append_reason(gate.reason, trend_reason),
                            gate.vrp,
                            gate.mr,
                            gate.vov,
                        )
                    else:
                        gate = HestonGateResult(
                            gate.ok,
                            append_reason(gate.reason, trend_reason),
                            gate.vrp,
                            gate.mr,
                            gate.vov,
                        )
                else:
                    gate = HestonGateResult(
                        gate.ok,
                        append_reason(gate.reason, trend_reason),
                        gate.vrp,
                        gate.mr,
                        gate.vov,
                    )

        vrp_val = gate.vrp if gate.vrp == gate.vrp else float("nan")
        mr_val = gate.mr if gate.mr == gate.mr else float("nan")
        vov_val = gate.vov if gate.vov == gate.vov else float("nan")

        iv_ok = (iv_atm is not None) and (nC >= 3) and (nP >= 3)
        rv_ok = (rv60 == rv60) and (rv60 > 0)
        greeks_ok = len(self.state.greeks_by_symbol) >= 200
        data_ready = iv_ok and rv_ok and greeks_ok

        slack_ready = True
        day_ok = True

        return (
            features,
            gate,
            data_ready,
            slack_ready,
            day_ok,
            iv_ok,
            rv_ok,
            greeks_ok,
            iv_atm,
            rv60,
            vrp_val,
            mr_val,
            vov_val,
            nC,
            nP,
            parse_fail,
            dt,
            gate_params_eval,
            gate_params_eval.rv60_min,
            max_abs_5m_return,
        )

    def _build_snapshot(
        self, features: BarFeatures, gate: HestonGateResult, rv60_min: float | None
    ) -> MarketSnapshot:
        iv_atm = features.iv_atm
        rv60 = features.rv60
        rv30 = features.rv30
        rv90 = features.rv90

        vrp = float("nan")
        if (
            isinstance(iv_atm, (int, float))
            and iv_atm > 0
            and rv60 == rv60
            and rv60 > 0
        ):
            vrp = iv_atm / rv60

        mr = float("nan")
        if rv30 == rv30 and rv90 == rv90:
            mr = rv30 - rv90

        extra: dict[str, object] = {
            "gate_ok": bool(gate.ok),
            "rv60_min": float(rv60_min) if rv60_min is not None else None,
            "data_ready": bool(
                isinstance(iv_atm, (int, float))
                and iv_atm > 0
                and rv60 == rv60
                and rv60 > 0
                and features.greeks_symbols >= 200
            ),
        }
        if features.trend is not None:
            extra["trend"] = features.trend.as_dict()

        snapshot = MarketSnapshot(
            ts=features.dt,
            symbol=SPX_INDEX_SYMBOL,
            timeframe="5m",
            iv_atm=iv_atm,
            rv30=rv30,
            rv60=rv60,
            rv90=rv90,
            vrp=vrp,
            vov=features.vov,
            mr=mr,
            adx=None,
            extra=extra,
        )
        self.state.last_snapshot = snapshot
        return snapshot

    def _add_metric(self, metrics: dict[str, float], key: str, value: object) -> None:
        if not isinstance(value, (int, float)):
            return
        if value != value:
            return
        metrics[key] = float(value)

    def publish_fact(
        self,
        *,
        kind: FactKind,
        metrics: dict[str, float],
        state: dict[str, object],
        events: list[dict[str, object]] | None = None,
        strategy: str | None = None,
        signal: dict[str, object] | None = None,
    ) -> None:
        publisher = self.deps.publisher
        if not publisher:
            return
        self.state.publish_seq += 1
        fact = EngineFactV1(
            kind=kind,
            run_id=self.state.engine_run_id,
            seq=self.state.publish_seq,
            ts=utc_iso(),
            market=MarketRef(
                underlier=self.state.exec_symbol, session="RTH", timeframe="5m"
            ),
            metrics=metrics,
            state=state,
            events=events or [],
            strategy=strategy,
            signal=signal,
        )
        try:
            publisher.publish(fact)
        except Exception as exc:
            if isinstance(exc, requests.exceptions.ConnectionError):
                self.deps.log.warning(
                    "[PUBLISH] web server not running; skipping publish"
                )
                return
            self.deps.log.exception("[PUBLISH] failed to publish fact")

    def publish_engine_event(self, name: str, payload: dict[str, object]) -> None:
        self.publish_fact(
            kind=FactKind.ENGINE_EVENT,
            metrics={},
            state={"event": name},
            events=[{"name": name, "payload": payload}],
        )

    def _serialize_sentinel_event(self, event) -> dict[str, object]:
        payload = asdict(event)
        ts = payload.get("ts")
        if isinstance(ts, datetime):
            payload["ts"] = ts.isoformat()
        snap = payload.get("snapshot")
        if isinstance(snap, dict):
            snap_ts = snap.get("ts")
            if isinstance(snap_ts, datetime):
                snap["ts"] = snap_ts.isoformat()
        return payload

    def _maybe_publish_sentinel_events(
        self,
        *,
        features: BarFeatures,
        gate: HestonGateResult,
        iv_ok: bool,
        rv_ok: bool,
        rv60_min: float | None,
    ) -> None:
        sentinel = self.deps.sentinel
        if not sentinel:
            return
        if not (iv_ok and rv_ok):
            return
        snapshot = self._build_snapshot(features, gate, rv60_min)
        events = sentinel.on_candle_close(snapshot, now=features.dt)
        if not events:
            return
        policy_payload = (
            self.deps.trade_policy.model_dump() if self.deps.trade_policy else None
        )
        self.publish_fact(
            kind=FactKind.ENGINE_EVENT,
            metrics={},
            state={"source": "sentinel", "policy": policy_payload},
            events=[self._serialize_sentinel_event(e) for e in events],
        )

    def process_bar_and_print(self, bar: CandleBar) -> None:
        log = self.deps.log
        trend_risk_cfg = self.deps.trend_risk_cfg
        exit_planner_cfg = self.deps.exit_planner_cfg
        gate_params = self.deps.gate_params
        trade_policy = self.deps.trade_policy

        (
            features,
            gate,
            data_ready,
            slack_ready,
            day_ok,
            iv_ok,
            rv_ok,
            greeks_ok,
            iv_atm,
            rv60,
            vrp_val,
            mr_val,
            vov_val,
            _nC,
            _nP,
            _parse_fail,
            dt,
            gate_params_eval,
            rv60_min,
            max_abs_5m_return,
        ) = self.compute_features_and_gate(bar)

        log.info(
            "BAR %s close=%.2f ema21=%.2f touch=%s | gate=%s vrp=%.2f mr=%.4f vov=%.6f | iv_atm=%s rv60=%s",
            dt.strftime("%H:%M"),
            bar.close,
            (features.ema21 if features.ema21 == features.ema21 else float("nan")),
            features.touch,
            gate.ok,
            (vrp_val if vrp_val == vrp_val else float("nan")),
            (mr_val if mr_val == mr_val else float("nan")),
            (vov_val if vov_val == vov_val else float("nan")),
            (f"{iv_atm:.4f}" if iv_atm is not None else "None"),
            (f"{rv60:.6f}" if rv60 == rv60 else "NaN"),
        )

        log.info(
            "TRACE %s | touch=%s gate=%s data=%s(iv=%s rv=%s gr=%s) | dry=%s | slack=%s day=%s | reason=%s",
            dt.strftime("%H:%M"),
            ok_mark(features.touch),
            ok_mark(gate.ok),
            ok_mark(data_ready),
            ok_mark(iv_ok),
            ok_mark(rv_ok),
            ok_mark(greeks_ok),
            self.state.dry_run,
            ok_mark(slack_ready),
            ok_mark(day_ok),
            gate.reason,
        )

        metrics: dict[str, float] = {}
        self._add_metric(metrics, "close", bar.close)
        self._add_metric(metrics, "ema21", features.ema21)
        self._add_metric(metrics, "atr5", features.atr5)
        self._add_metric(metrics, "iv_atm", iv_atm)
        self._add_metric(metrics, "rv30", features.rv30)
        self._add_metric(metrics, "rv60", features.rv60)
        self._add_metric(metrics, "rv90", features.rv90)
        self._add_metric(metrics, "vrp", vrp_val)
        self._add_metric(metrics, "mr", mr_val)
        self._add_metric(metrics, "vov", vov_val)
        self._add_metric(metrics, "latest_spx_mid", features.latest_spx_mid)
        if self.state.latest_context_priors:
            self._add_metric(
                metrics, "context_score", self.state.latest_context_priors.score
            )

        shadow_guard_active = False
        shadow_rv60_cap = None
        shadow_vrp = None
        shadow_gate_ok = None
        if (
            isinstance(max_abs_5m_return, (int, float))
            and max_abs_5m_return == max_abs_5m_return
        ):
            rv60_val = features.rv60
            if (
                isinstance(rv60_val, (int, float))
                and rv60_val == rv60_val
                and rv60_val > 0
            ):
                if max_abs_5m_return < 0.002 and rv60_val > 0.30:
                    shadow_guard_active = True
                    shadow_rv60_cap = max_abs_5m_return * math.sqrt(252 * 78)
                    rv60_guarded = min(rv60_val, shadow_rv60_cap)
                    if isinstance(iv_atm, (int, float)) and rv60_guarded > 0:
                        shadow_vrp = iv_atm / rv60_guarded
                    if gate_params_eval.enabled:
                        vrp_min = gate_params_eval.vrp_min or 0.0
                        rv60_min_req = gate_params_eval.rv60_min or 0.0
                        mr_max_req = gate_params_eval.mr_max or 0.0
                        vov_max_req = gate_params_eval.vov_max
                        mr_ok = mr_val == mr_val and mr_val <= mr_max_req
                        vrp_ok = shadow_vrp is not None and shadow_vrp >= vrp_min
                        rv_ok_shadow = rv60_guarded >= rv60_min_req
                        vov_ok = True
                        if vov_max_req is not None and vov_val == vov_val:
                            vov_ok = vov_val <= vov_max_req
                        shadow_gate_ok = bool(
                            vrp_ok and rv_ok_shadow and mr_ok and vov_ok
                        )
                    else:
                        shadow_gate_ok = True

        state = {
            "gate_ok": bool(gate.ok),
            "gate_reason": gate.reason,
            "signal_mode": self.state.signal_mode,
            "context_regime": (
                self.state.latest_context_priors.regime
                if self.state.latest_context_priors
                else None
            ),
            "data_ready": bool(data_ready),
            "touch": bool(features.touch),
            "greeks_symbols": int(features.greeks_symbols),
            "slack_ready": bool(slack_ready),
            "day_ok": bool(day_ok),
            "max_abs_5m_return": (
                float(max_abs_5m_return)
                if max_abs_5m_return == max_abs_5m_return
                else None
            ),
            "shadow_guard_active": shadow_guard_active,
            "shadow_rv60_cap": shadow_rv60_cap,
            "shadow_vrp": shadow_vrp,
            "shadow_gate_ok": shadow_gate_ok,
        }
        self.publish_fact(
            kind=FactKind.MARKET_FACT,
            metrics=metrics,
            state=state,
        )

        if self.state.warmup_done:
            self._maybe_publish_sentinel_events(
                features=features,
                gate=gate,
                iv_ok=iv_ok,
                rv_ok=rv_ok,
                rv60_min=rv60_min,
            )

        exit_plan_row = None
        if self.state.exit_planner:
            spot = features.latest_spx_mid or features.bar.close
            plan = self.state.exit_planner.build_plan(
                features=features,
                gate=gate,
                greeks_by_symbol=self.state.greeks_by_symbol,
                condor=None,
                spot=spot,
            )
            if (
                trend_risk_cfg.enabled
                and trend_risk_cfg.policy == "tier_only"
                and features.trend is not None
                and features.trend.risk_tier in ("caution", "block")
            ):
                plan = cap_exit_plan_for_trend(plan, exit_planner_cfg)
            exit_plan_row = asdict(plan)

        next_bar_index = self.state.stats.bars + 1
        self._emit_candle(
            bar_index=next_bar_index,
            features=features,
            gate=gate,
            data_ready=data_ready,
            exit_plan=exit_plan_row,
            shadow={
                "max_abs_5m_return": (
                    float(max_abs_5m_return)
                    if max_abs_5m_return == max_abs_5m_return
                    else None
                ),
                "shadow_guard_active": shadow_guard_active,
                "shadow_rv60_cap": shadow_rv60_cap,
                "shadow_vrp": shadow_vrp,
                "shadow_gate_ok": shadow_gate_ok,
            },
        )

        decision = decide(
            features=features,
            gate=gate,
            data_ready=data_ready,
            slack_ready=slack_ready,
            day_ok=day_ok,
            dry_run=self.state.dry_run,
        )
        self.state.last_decision = decision

        if not decision.ok:
            if decision.reason == "dry_run":
                self._emit_event("blocked", {"reason": "dry_run"})
                if features.touch and gate.ok and data_ready:
                    log.info(
                        "[DRY_RUN] Signal conditions met (would build condor + alert)"
                    )
                return
            if decision.reason == "no_touch":
                self._emit_event("blocked", {"reason": "no_touch"})
                return
            if decision.reason == "gate_fail":
                self._emit_event("blocked", {"reason": "gate_fail"})
                return
            if decision.reason == "data_not_ready":
                self._emit_event("blocked", {"reason": "data_not_ready"})
                return

        condor = pick_condor_20delta_20wide(
            self.state.greeks_by_symbol,
            target_abs_delta=0.20,
            wing_width=20.0,
            delta_neutral=True,
            neutral_tol=0.03,
        )

        if condor is None:
            log.warning("[CONDOR] build failed (not enough greeks/strikes)")
            return

        self._emit_event("condor_built", {})
        log.info(
            "[CONDOR] SP %.0f / LP %.0f || SC %.0f / LC %.0f | netΔ=%.3f widths(P=%.0f,C=%.0f)",
            condor.short_put.strike,
            condor.long_put.strike,
            condor.short_call.strike,
            condor.long_call.strike,
            condor.net_short_delta,
            condor.width_put,
            condor.width_call,
        )

        ok = False
        exit_plan_signal = None
        exit_plan_signal_row = None
        if self.state.exit_planner:
            spot = features.latest_spx_mid or features.bar.close
            plan = self.state.exit_planner.build_plan(
                features=features,
                gate=gate,
                greeks_by_symbol=self.state.greeks_by_symbol,
                condor=condor,
                spot=spot,
            )
            if (
                trend_risk_cfg.enabled
                and trend_risk_cfg.policy == "tier_only"
                and features.trend is not None
                and features.trend.risk_tier in ("caution", "block")
            ):
                plan = cap_exit_plan_for_trend(plan, exit_planner_cfg)
            exit_plan_signal = plan
            exit_plan_signal_row = asdict(plan)
        signal_ctx = {"slack_sent": False}
        self._emit_signal(
            bar_index=next_bar_index,
            features=features,
            gate=gate,
            condor=condor,
            exit_plan=exit_plan_signal,
            exit_plan_row=exit_plan_signal_row,
            slack_ready=True,
            signal_ctx=signal_ctx,
            exit_planner_enabled=exit_planner_cfg.slack_enabled,
            exit_planner_prefix=exit_planner_cfg.slack_prefix,
            shadow={
                "max_abs_5m_return": (
                    float(max_abs_5m_return)
                    if max_abs_5m_return == max_abs_5m_return
                    else None
                ),
                "shadow_guard_active": shadow_guard_active,
                "shadow_rv60_cap": shadow_rv60_cap,
                "shadow_vrp": shadow_vrp,
                "shadow_gate_ok": shadow_gate_ok,
            },
        )
        ok = True
        signal_ctx["slack_sent"] = True

        signal_payload = {
            "short_put": condor.short_put.strike,
            "long_put": condor.long_put.strike,
            "short_call": condor.short_call.strike,
            "long_call": condor.long_call.strike,
            "net_short_delta": condor.net_short_delta,
            "width_put": condor.width_put,
            "width_call": condor.width_call,
            "slack_sent": bool(ok),
            "exit_plan": exit_plan_signal_row,
            "touch": bool(features.touch),
            "shadow_guard_active": shadow_guard_active,
            "shadow_vrp": shadow_vrp,
            "shadow_gate_ok": shadow_gate_ok,
        }
        if features.trend is not None:
            signal_payload["trend"] = features.trend.as_dict()
        if trade_policy:
            signal_payload["policy"] = trade_policy.model_dump()
            signal_payload["policy_summary"] = trade_policy.summary_line()
            signal_payload["policy_flat_by"] = (
                f"Flat by: {trade_policy.exit_all_by_cst} CST (no new entries after {trade_policy.no_new_entries_after_cst})"
            )
        signal_metrics: dict[str, float] = {}
        self._add_metric(signal_metrics, "close", bar.close)
        self._add_metric(signal_metrics, "ema21", features.ema21)
        self._add_metric(signal_metrics, "iv_atm", iv_atm)
        self._add_metric(signal_metrics, "rv30", features.rv30)
        self._add_metric(signal_metrics, "rv60", features.rv60)
        self._add_metric(signal_metrics, "rv90", features.rv90)
        self._add_metric(signal_metrics, "vrp", vrp_val)
        self._add_metric(signal_metrics, "mr", mr_val)
        self._add_metric(signal_metrics, "vov", vov_val)
        self._add_metric(signal_metrics, "latest_spx_mid", features.latest_spx_mid)
        self._add_metric(signal_metrics, "net_short_delta", condor.net_short_delta)
        self.publish_fact(
            kind=FactKind.STRATEGY_SIGNAL,
            metrics=signal_metrics,
            state={
                "gate_ok": bool(gate.ok),
                "gate_reason": gate.reason,
                "signal_mode": self.state.signal_mode,
                "slack_sent": bool(ok),
                "ts_cst": features.dt.strftime("%Y-%m-%d %H:%M CST"),
                "max_abs_5m_return": (
                    float(max_abs_5m_return)
                    if max_abs_5m_return == max_abs_5m_return
                    else None
                ),
                "shadow_guard_active": shadow_guard_active,
                "shadow_rv60_cap": shadow_rv60_cap,
                "shadow_vrp": shadow_vrp,
                "shadow_gate_ok": shadow_gate_ok,
            },
            strategy="condor",
            signal=signal_payload,
        )

    def on_quote(self, q) -> None:
        log = self.deps.log
        self.mark_stream_event("quote")

        bid = getattr(q, "bid_price", None)
        ask = getattr(q, "ask_price", None)
        last = getattr(q, "last_price", None)

        if not self.state.seen_quote:
            self.state.seen_quote = True
            log.info(
                "[STREAM] FIRST quote sym=%s bid=%s ask=%s last=%s",
                getattr(q, "event_symbol", None),
                bid,
                ask,
                last,
            )

        if (
            isinstance(bid, (int, float))
            and isinstance(ask, (int, float))
            and bid > 0
            and ask > 0
        ):
            self.state.latest_spx_mid = (bid + ask) / 2.0
            log.debug(
                "Quote SPX mid=%.2f (bid=%.2f ask=%.2f)",
                self.state.latest_spx_mid,
                bid,
                ask,
            )
            return

        if (
            self.state.latest_spx_mid is None
            and isinstance(last, (int, float))
            and last > 0
        ):
            self.state.latest_spx_mid = float(last)
            log.debug(
                "Quote fallback mid=%.2f (from last price)",
                self.state.latest_spx_mid,
            )

    def on_greeks(self, g) -> None:
        log = self.deps.log
        self.mark_stream_event("greeks")

        if not self.state.seen_greeks:
            self.state.seen_greeks = True
            log.info(
                "[STREAM] FIRST greeks sym=%s delta=%s vol=%s",
                getattr(g, "event_symbol", None),
                getattr(g, "delta", None),
                getattr(g, "volatility", None),
            )
            log.info("[STREAM] FIRST greeks obj=%r", g)
        self.state.greeks_by_symbol[g.event_symbol] = g

    def on_context_candle(self, c) -> None:
        if not self.state.context_enabled:
            return

        self.mark_stream_event("context_candle")

        t = getattr(c, "time", None)
        o = getattr(c, "open", None)
        h = getattr(c, "high", None)
        l = getattr(c, "low", None)
        cl = getattr(c, "close", None)

        if not isinstance(t, int):
            return

        try:
            o_f, h_f, l_f, cl_f = (float(x) for x in (o, h, l, cl))
        except Exception:
            return
        if cl_f <= 0:
            return

        bar = CandleBar(open=o_f, high=h_f, low=l_f, close=cl_f, time_ms=t)
        self._emit_event("context_candle", {"bar": bar})

    def on_candle(self, c) -> None:
        log = self.deps.log
        self.mark_stream_event("candle")

        t = getattr(c, "time", None)
        o = getattr(c, "open", None)
        h = getattr(c, "high", None)
        l = getattr(c, "low", None)
        cl = getattr(c, "close", None)

        if not isinstance(t, int):
            return

        try:
            o_f, h_f, l_f, cl_f = (float(x) for x in (o, h, l, cl))
        except Exception:
            return
        if cl_f <= 0:
            return

        if not self.state.seen_valid_candle:
            self.state.seen_valid_candle = True
            self._emit_event("valid_candle", {"time_ms": t})
            log.info(
                "[STREAM] FIRST valid candle sym=%s t=%s close=%.2f",
                getattr(c, "event_symbol", None),
                t,
                cl_f,
            )

        bar = CandleBar(open=o_f, high=h_f, low=l_f, close=cl_f, time_ms=t)

        if self.state.backfill_mode:
            self.state.backfill_by_time[t] = bar

            target = self._backfill_target()
            if (
                is_near_live(t, minutes=20)
                and self.state.backfill_wait_deadline_ms is None
            ):
                wait_minutes = 10
                self.state.backfill_wait_deadline_ms = t + wait_minutes * 60 * 1000
                bars_since_open = self._bars_since_session_open(t)
                if bars_since_open:
                    target = min(target, bars_since_open)
                log.info(
                    "[BACKFILL] near-live seen; waiting for %d bars or until %s",
                    target,
                    dt_from_ms(self.state.backfill_wait_deadline_ms, TZ_CST).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                )
                return

            enough_backfill = len(self.state.backfill_by_time) >= target
            deadline_reached = (
                self.state.backfill_wait_deadline_ms is not None
                and t >= self.state.backfill_wait_deadline_ms
            )

            if enough_backfill or deadline_reached:
                times = sorted(self.state.backfill_by_time.keys())
                gap_count, max_gap_ms = self._backfill_gap_stats(times)
                if gap_count and self.state.backfill_retries_left > 0:
                    if self.state.backfill_retry_deadline_ms is None:
                        retry_minutes = 10
                        self.state.backfill_retry_deadline_ms = (
                            t + retry_minutes * 60 * 1000
                        )
                        self.state.backfill_retries_left -= 1
                        log.warning(
                            "[BACKFILL] gaps detected; retrying until %s",
                            dt_from_ms(
                                self.state.backfill_retry_deadline_ms, TZ_CST
                            ).strftime("%Y-%m-%d %H:%M"),
                        )
                    if t < (self.state.backfill_retry_deadline_ms or 0):
                        return
                latest_bar = max(
                    self.state.backfill_by_time.values(),
                    key=lambda b: b.time_ms or 0,
                )
                self.state.backfill_mode = False
                self._seed_backfill(
                    list(self.state.backfill_by_time.values()), current=latest_bar
                )
                log.info(
                    "[BACKFILL] complete: unique_bars=%d buf=%d",
                    len(self.state.backfill_by_time),
                    len(self.state.buf),
                )
                self.state.backfill_by_time.clear()
                self.state.backfill_wait_deadline_ms = None
                self.state.current_forming = latest_bar
                self.state.current_forming_t = latest_bar.time_ms
            return

        if self.state.last_closed_t is not None and t <= self.state.last_closed_t:
            log.warning(
                "[STREAM] ignoring stale candle t=%s <= last_closed=%s",
                t,
                self.state.last_closed_t,
            )
            return

        if self.state.current_forming is None:
            self.state.current_forming = bar
            self.state.current_forming_t = t
            return

        if (
            self.state.current_forming_t is not None
            and t < self.state.current_forming_t
        ):
            log.warning(
                "[STREAM] ignoring out-of-order candle t=%s < current=%s",
                t,
                self.state.current_forming_t,
            )
            return

        if t == self.state.current_forming_t:
            self.state.same_bar_updates += 1
            self.state.current_forming = CandleBar(
                open=o_f,
                high=h_f,
                low=l_f,
                close=cl_f,
                time_ms=t,
            )
            return

        finished = self.state.current_forming
        self.state.buf.append(finished)
        self.state.accepted_bars += 1
        self.state.same_bar_updates = 0
        if finished.time_ms is not None:
            self.state.last_closed_t = finished.time_ms

        dt_finished = dt_from_ms(finished.time_ms, TZ_CST)
        log.info(
            "[CANDLE] CLOSED bar #%d time=%s o=%.2f h=%.2f l=%.2f c=%.2f buf=%d",
            self.state.accepted_bars,
            dt_finished.strftime("%Y-%m-%d %H:%M"),
            finished.open,
            finished.high,
            finished.low,
            finished.close,
            len(self.state.buf),
        )

        if self.candle_store:
            try:
                candle_row = CandleBarV1(
                    ts=dt_finished.astimezone(timezone.utc),
                    symbol=self.state.exec_symbol,
                    timeframe="5m",
                    o=finished.open,
                    h=finished.high,
                    l=finished.low,
                    c=finished.close,
                    v=0,
                )
                self.candle_store.write_candle(candle_row)
            except Exception:
                log.exception("[PERSIST] candle write failed")

        if self.options_store:
            try:
                spot = float(self.state.latest_spx_mid or finished.close)
                iv_med, iv_disp, n_calls, n_puts, parse_fail = compute_iv_0dte_band(
                    greeks_by_symbol=self.state.greeks_by_symbol,
                    spot=spot,
                )
                if iv_med is not None:
                    snap = OptionSnapshotV1(
                        ts=dt_finished.astimezone(timezone.utc),
                        symbol=self.state.exec_symbol,
                        timeframe="5m",
                        dte=getattr(
                            self.deps.engine_settings.market_persist.options, "dte", 0
                        ),
                        spot=spot,
                        iv_atm=float(iv_med),
                        iv_atm_method="band_median",
                        meta={
                            "source": "engine",
                            "iv_disp": (
                                float(iv_disp) if iv_disp is not None else None
                            ),
                            "n_calls": int(n_calls),
                            "n_puts": int(n_puts),
                            "parse_fail": int(parse_fail),
                        },
                    )
                    self.options_store.write_option_snapshot(snap)
            except Exception:
                log.exception("[PERSIST] options snapshot write failed")

        self.state.current_forming = bar
        self.state.current_forming_t = t

        if not self.state.warmup_done:
            if len(self.state.buf) >= self.state.warmup_bars:
                self.state.warmup_done = True
                log.info("[WARMUP] done. closed_5m_bars=%d", len(self.state.buf))
                self._emit_event("warmup_complete", {"bars": len(self.state.buf)})
                self.publish_engine_event(
                    "warmup_complete", {"bars": len(self.state.buf)}
                )
            else:
                log.info(
                    "[WARMUP] progress closed_5m_bars=%d/%d",
                    len(self.state.buf),
                    self.state.warmup_bars,
                )
                if self.state.early_config.enabled or self.deps.sentinel:
                    (
                        features,
                        gate,
                        data_ready,
                        _slack_ready,
                        _day_ok,
                        _iv_ok,
                        _rv_ok,
                        _greeks_ok,
                        _iv_atm,
                        _rv60,
                        _vrp_val,
                        _mr_val,
                        _vov_val,
                        _nC,
                        _nP,
                        _parse_fail,
                        _dt,
                        _gate_params_eval,
                        _rv60_min,
                        _max_abs_5m_return,
                    ) = self.compute_features_and_gate(finished)
                    self._maybe_publish_sentinel_events(
                        features=features,
                        gate=gate,
                        iv_ok=_iv_ok,
                        rv_ok=_rv_ok,
                        rv60_min=_rv60_min,
                    )
                    if self.state.early_config.enabled:
                        early_decision = evaluate_early(
                            features=features,
                            base_params=self.deps.gate_params,
                            config=self.state.early_config,
                            context_priors=self.state.latest_context_priors,
                            bars_count=len(self.state.buf),
                            warmup_done=False,
                            data_ready=data_ready,
                        )
                        log.info(
                            "[EARLY] shadow ok=%s reason=%s bars=%d",
                            early_decision.ok,
                            early_decision.reason,
                            len(self.state.buf),
                        )
                        self._emit_event(
                            "early_decision",
                            {
                                "bar_index": len(self.state.buf),
                                "features": features,
                                "gate": gate,
                                "signal_mode": self.state.signal_mode,
                                "context_priors": self.state.latest_context_priors,
                                "early_decision": early_decision,
                            },
                        )
            return

        if not self.state.ignore_time_window:
            if not is_today(finished.time_ms, TZ_CST):
                return
            if not in_signal_window(finished.time_ms, TZ_CST):
                self._emit_event("blocked", {"reason": "time"})
                return

        self.process_bar_and_print(finished)

    async def run_all(self) -> None:
        log = self.deps.log
        cfg = StreamConfig()
        cfg.candle_symbol = self.state.exec_symbol
        cfg.quote_symbol = self.state.exec_symbol
        if self.state.context_enabled:
            cfg.context_candle_symbol = self.state.context_symbol
            cfg.context_extended_trading_hours = True
        if self.state.candle_minutes_back_override > 0:
            cfg.candle_minutes_back = self.state.candle_minutes_back_override
        else:
            cfg.candle_minutes_back = max(
                cfg.candle_minutes_back, self.state.backfill_bars * 5
            )
        cfg.backfill_prev_session_open = self.state.backfill_prev_session_open
        log.info(
            "[STREAM] Starting stream (interval=%s, minutes_back=%s, ETH=%s)",
            cfg.candle_interval,
            cfg.candle_minutes_back,
            cfg.extended_trading_hours,
        )

        self._emit_event(
            "startup",
            {
                "dry_run": self.state.dry_run,
                "warmup_bars": self.state.warmup_bars,
                "ignore_time_window": self.state.ignore_time_window,
            },
        )
        self.publish_engine_event(
            "startup",
            {
                "dry_run": self.state.dry_run,
                "warmup_bars": self.state.warmup_bars,
                "ignore_time_window": self.state.ignore_time_window,
            },
        )

        async with asyncio.TaskGroup() as tg:
            stream_task = tg.create_task(
                run_stream(
                    self.deps.session,
                    cfg,
                    on_candle=self.on_candle,
                    on_context_candle=self.on_context_candle,
                    on_quote=self.on_quote,
                    on_greeks=self.on_greeks,
                )
            )
            tg.create_task(
                self.state.stream_health.watchdog(stop_event=self.state.stop_event)
            )

            async def shutdown_watcher() -> None:
                await self.state.stop_event.wait()
                log.info("[SHUTDOWN] stop_event set -> cancelling stream task")
                stream_task.cancel()
                raise asyncio.CancelledError

            tg.create_task(shutdown_watcher())

    async def runner_entry(self) -> None:
        loop = asyncio.get_running_loop()

        def _request_stop(signame: str) -> None:
            self.deps.log.info("[SHUTDOWN] requested via %s", signame)
            self.state.exit_reason = "graceful"
            self.state.stop_event.set()

        try:
            loop.add_signal_handler(signal.SIGINT, _request_stop, "SIGINT")
            loop.add_signal_handler(signal.SIGTERM, _request_stop, "SIGTERM")
        except NotImplementedError:
            pass

        await self.run_all()

    def run(self) -> None:
        log = self.deps.log
        if not self.deps.engine_settings.stream.enabled:
            log.warning("STREAM_ENABLED=false; exiting before stream start")
            return
        try:
            asyncio.run(self.runner_entry())
            if self.state.exit_reason == "unknown":
                self.state.exit_reason = "graceful"
        except KeyboardInterrupt:
            log.info("[SHUTDOWN] KeyboardInterrupt")
            if self.state.exit_reason == "unknown":
                self.state.exit_reason = "graceful"
        except asyncio.CancelledError:
            log.info("[SHUTDOWN] Cancelled")
            if self.state.exit_reason == "unknown":
                self.state.exit_reason = "cancelled"
        except Exception as e:
            self.state.exit_reason = "crash"
            self.state.exit_error = repr(e)
            log.exception("[CRASH] Unhandled exception")

            self._emit_event("crash", {"exit_error": self.state.exit_error})
            self.publish_engine_event("crash", {"exit_error": self.state.exit_error})

            raise
        finally:
            try:
                self._emit_event(
                    "shutdown",
                    {
                        "exit_reason": self.state.exit_reason,
                        "exit_error": self.state.exit_error,
                        "stats": self.state.stats.as_dict(),
                    },
                )
                self.publish_engine_event(
                    "shutdown",
                    {
                        "exit_reason": self.state.exit_reason,
                        "exit_error": self.state.exit_error,
                        "stats": self.state.stats.as_dict(),
                    },
                )
            except Exception:
                pass

            log.info(
                "FINAL SUMMARY bars=%d touch=%d gate_ok=%d data_ready=%d condor=%d slack_sent=%d | "
                "blocked(touch=%d gate=%d data=%d slack=%d dry=%d time=%d)",
                self.state.stats.bars,
                self.state.stats.touch,
                self.state.stats.gate_ok,
                self.state.stats.data_ready,
                self.state.stats.condor_built,
                self.state.stats.slack_sent,
                self.state.stats.blocked_touch,
                self.state.stats.blocked_gate,
                self.state.stats.blocked_data,
                self.state.stats.blocked_slack,
                self.state.stats.blocked_dry_run,
                self.state.stats.blocked_time,
            )
