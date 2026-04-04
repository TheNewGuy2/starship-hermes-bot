# src/bot/sentinel.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from starship_shared.sentinel import (
    MarketSnapshot,
    MarketState,
    SentinelEvent,
    SentinelEventType,
)

from starship_engine.apps.sentinel.market_hours import in_rth
from starship_engine.apps.sentinel.state import StateParams, derive_market_state


@dataclass(frozen=True)
class SentinelParams:
    score_thresholds: list[int]
    hysteresis: int
    vrp_min: float
    mr_max: float
    vov_caution: float
    vov_hostile: float
    vov_hysteresis: float
    cooldown_regime_change_seconds: int
    cooldown_score_cross_seconds: int
    cooldown_vrp_seconds: int
    cooldown_vov_seconds: int
    cooldown_mr_seconds: int


class SlackSentinel:
    def __init__(
        self,
        *,
        params: SentinelParams,
        state_params: StateParams,
        tz: ZoneInfo,
        enabled: bool,
        rth_only: bool,
        heartbeat_enabled: bool,
        heartbeat_minutes: int,
        heartbeat_rth_only: bool,
    ) -> None:
        self.params = params
        self.state_params = state_params
        self.tz = tz
        self.enabled = enabled
        self.rth_only = rth_only
        self.heartbeat_enabled = heartbeat_enabled
        self.heartbeat_minutes = max(1, heartbeat_minutes)
        self.heartbeat_rth_only = heartbeat_rth_only

        self.prev_state: Optional[MarketState] = None
        self.prev_snapshot: Optional[MarketSnapshot] = None
        self.last_sent_by_key: dict[str, float] = {}
        self.score_armed: dict[int, bool] = {t: True for t in params.score_thresholds}
        self.vov_armed: dict[str, bool] = {
            "caution": True,
            "hostile": True,
        }
        self.last_heartbeat_window: Optional[tuple[datetime.date, int]] = None

    def on_candle_close(
        self,
        snapshot: MarketSnapshot,
        now: Optional[datetime] = None,
    ) -> list[SentinelEvent]:
        now = now or snapshot.ts
        state = derive_market_state(snapshot, self.state_params)
        events = self.detect_events(snapshot, state, now)
        events = self._apply_cooldown(events, now)
        self.prev_state = state
        self.prev_snapshot = snapshot
        return events

    def detect_events(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
        now: datetime,
    ) -> list[SentinelEvent]:
        events: list[SentinelEvent] = []
        events.extend(self._detect_regime_change(snapshot, state))
        events.extend(self._detect_score_cross(snapshot, state))
        events.extend(self._detect_vrp_break(snapshot, state))
        events.extend(self._detect_vov_spike(snapshot, state))
        events.extend(self._detect_mr_break(snapshot, state))
        hb = self._detect_heartbeat(snapshot, state, now)
        if hb:
            events.append(hb)
        return events

    def _detect_regime_change(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
    ) -> list[SentinelEvent]:
        if not self.prev_state:
            return []
        if self.prev_state.regime_label == state.regime_label:
            return []

        old = self.prev_state.regime_label
        new = state.regime_label
        severity = "warn"
        if (old == "Short-Vol Friendly" and new == "Hostile") or (
            old == "Caution" and new == "Hostile"
        ):
            severity = "critical"

        change = f"{old} -> {new} | Score {self.prev_state.score} -> {state.score}"
        return [
            SentinelEvent(
                type=SentinelEventType.REGIME_CHANGE,
                ts=snapshot.ts,
                snapshot=snapshot,
                state=state,
                changes=[change],
                severity=severity,
            )
        ]

    def _detect_score_cross(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
    ) -> list[SentinelEvent]:
        events: list[SentinelEvent] = []
        for threshold in sorted(self.params.score_thresholds, reverse=True):
            armed = self.score_armed.get(threshold, True)
            if armed and state.score < threshold:
                self.score_armed[threshold] = False
                severity = "warn" if threshold >= 70 else "critical"
                prev_score = self.prev_state.score if self.prev_state else state.score
                change = (
                    f"Score crossed below {threshold} ({prev_score} -> {state.score})"
                )
                events.append(
                    SentinelEvent(
                        type=SentinelEventType.SCORE_CROSS,
                        ts=snapshot.ts,
                        snapshot=snapshot,
                        state=state,
                        changes=[change],
                        severity=severity,
                    )
                )
            elif not armed and state.score >= (threshold + self.params.hysteresis):
                self.score_armed[threshold] = True
        return events

    def _detect_vrp_break(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
    ) -> list[SentinelEvent]:
        prev = self.prev_snapshot
        if prev is not None and prev.vrp < self.params.vrp_min:
            return []
        if snapshot.vrp >= self.params.vrp_min:
            return []

        severity = "warn"
        if snapshot.vrp < (self.params.vrp_min - 0.15):
            severity = "critical"

        return [
            SentinelEvent(
                type=SentinelEventType.VRP_BREAK,
                ts=snapshot.ts,
                snapshot=snapshot,
                state=state,
                changes=[f"VRP < {self.params.vrp_min:.2f}"],
                severity=severity,
            )
        ]

    def _detect_vov_spike(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
    ) -> list[SentinelEvent]:
        events: list[SentinelEvent] = []
        vov = snapshot.vov

        if self.vov_armed.get("caution", True) and vov > self.params.vov_caution:
            self.vov_armed["caution"] = False
            events.append(
                SentinelEvent(
                    type=SentinelEventType.VOV_SPIKE,
                    ts=snapshot.ts,
                    snapshot=snapshot,
                    state=state,
                    changes=[f"VoV > {self.params.vov_caution:.2f} (caution)"],
                    severity="warn",
                )
            )
        elif not self.vov_armed.get("caution", True) and vov <= (
            self.params.vov_caution - self.params.vov_hysteresis
        ):
            self.vov_armed["caution"] = True

        if self.vov_armed.get("hostile", True) and vov > self.params.vov_hostile:
            self.vov_armed["hostile"] = False
            events.append(
                SentinelEvent(
                    type=SentinelEventType.VOV_SPIKE,
                    ts=snapshot.ts,
                    snapshot=snapshot,
                    state=state,
                    changes=[f"VoV > {self.params.vov_hostile:.2f} (hostile)"],
                    severity="critical",
                )
            )
        elif not self.vov_armed.get("hostile", True) and vov <= (
            self.params.vov_hostile - self.params.vov_hysteresis
        ):
            self.vov_armed["hostile"] = True

        return events

    def _detect_mr_break(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
    ) -> list[SentinelEvent]:
        prev = self.prev_snapshot
        if prev is not None and prev.mr > self.params.mr_max:
            return []
        if snapshot.mr <= self.params.mr_max:
            return []

        return [
            SentinelEvent(
                type=SentinelEventType.MR_BREAK,
                ts=snapshot.ts,
                snapshot=snapshot,
                state=state,
                changes=[f"MR > {self.params.mr_max:.2f}"],
                severity="warn",
            )
        ]

    def _detect_heartbeat(
        self,
        snapshot: MarketSnapshot,
        state: MarketState,
        now: datetime,
    ) -> Optional[SentinelEvent]:
        if not self.heartbeat_enabled:
            return None
        if self.heartbeat_rth_only and not in_rth(now, self.tz):
            return None

        local = now.astimezone(self.tz)
        window_index = (local.hour * 60 + local.minute) // self.heartbeat_minutes
        key = (local.date(), window_index)
        if self.last_heartbeat_window == key:
            return None
        self.last_heartbeat_window = key

        return SentinelEvent(
            type=SentinelEventType.HEARTBEAT,
            ts=snapshot.ts,
            snapshot=snapshot,
            state=state,
            changes=[],
            severity="info",
        )

    def _cooldown_for_event(self, event: SentinelEvent) -> int:
        if event.type == SentinelEventType.REGIME_CHANGE:
            return self.params.cooldown_regime_change_seconds
        if event.type == SentinelEventType.SCORE_CROSS:
            return self.params.cooldown_score_cross_seconds
        if event.type == SentinelEventType.VRP_BREAK:
            return self.params.cooldown_vrp_seconds
        if event.type == SentinelEventType.VOV_SPIKE:
            return self.params.cooldown_vov_seconds
        if event.type == SentinelEventType.MR_BREAK:
            return self.params.cooldown_mr_seconds
        return 0

    def _event_key(self, event: SentinelEvent) -> str:
        if event.type == SentinelEventType.SCORE_CROSS:
            if event.changes:
                match = re.search(r"\b(\d+)\b", event.changes[0])
                if match:
                    return f"SCORE_CROSS_{match.group(1)}"
            return "SCORE_CROSS"
        if event.type == SentinelEventType.VOV_SPIKE:
            change = event.changes[0] if event.changes else ""
            if "hostile" in change:
                return "VOV_SPIKE_HOSTILE"
            return "VOV_SPIKE_CAUTION"
        if event.type == SentinelEventType.VRP_BREAK:
            return "VRP_BREAK"
        if event.type == SentinelEventType.MR_BREAK:
            return "MR_BREAK"
        if event.type == SentinelEventType.HEARTBEAT:
            return "HEARTBEAT"
        return event.type

    def _apply_cooldown(
        self, events: list[SentinelEvent], now: datetime
    ) -> list[SentinelEvent]:
        if not self.enabled:
            return []
        if self.rth_only and not in_rth(now, self.tz):
            return []

        emitted: list[SentinelEvent] = []
        now_ts = now.timestamp()
        for event in events:
            key = self._event_key(event)
            cooldown = self._cooldown_for_event(event)
            last_ts = self.last_sent_by_key.get(key)
            if last_ts is not None and cooldown > 0:
                if now_ts - last_ts < cooldown:
                    continue
            self.last_sent_by_key[key] = now_ts
            emitted.append(event)
        return emitted
