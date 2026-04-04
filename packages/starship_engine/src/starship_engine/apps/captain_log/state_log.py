# src/bot/pipeline/state_log.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from starship_engine.apps.captain_log.state import StateLogger
from starship_engine.apps.context_overnight.logic import ContextPriors
from starship_engine.apps.heston.logic import HestonGateResult
from starship_engine.domain.types import BarFeatures


@dataclass
class StateLogHandler:
    logger: Optional[StateLogger]
    every_bars: int

    def on_candle(
        self,
        *,
        bar_index: int,
        features: BarFeatures,
        gate: HestonGateResult,
        context_priors: Optional[ContextPriors],
        signal_mode: str | None = None,
        exit_plan: Optional[object] = None,
        shadow: Optional[dict[str, object]] = None,
        **_,
    ) -> None:
        self.maybe_write(
            bar_index=bar_index,
            features=features,
            gate=gate,
            signal_mode=signal_mode or "",
            context_priors=context_priors,
            early_decision=None,
            slack_sent=False,
            exit_plan=exit_plan,
            shadow=shadow,
        )

    def on_signal(
        self,
        *,
        bar_index: int,
        features: BarFeatures,
        gate: HestonGateResult,
        context_priors: Optional[ContextPriors],
        signal_mode: str | None = None,
        exit_plan: Optional[object] = None,
        exit_plan_row: Optional[object] = None,
        slack_ready: bool,
        signal_ctx: Optional[dict[str, object]] = None,
        shadow: Optional[dict[str, object]] = None,
        **_,
    ) -> None:
        slack_sent = bool(signal_ctx.get("slack_sent")) if signal_ctx else False
        self.maybe_write(
            bar_index=bar_index,
            features=features,
            gate=gate,
            signal_mode=signal_mode or "",
            context_priors=context_priors,
            early_decision=None,
            slack_sent=slack_sent,
            exit_plan=exit_plan_row if exit_plan_row is not None else exit_plan,
            shadow=shadow,
        )

    def on_event(self, *, name: str, payload: dict[str, object]) -> None:
        if name != "early_decision":
            return
        self.maybe_write(
            bar_index=int(payload.get("bar_index", 0)),
            features=payload.get("features"),
            gate=payload.get("gate"),
            signal_mode=str(payload.get("signal_mode", "")),
            context_priors=payload.get("context_priors"),
            early_decision=payload.get("early_decision"),
            slack_sent=False,
        )

    def maybe_write(
        self,
        *,
        bar_index: int,
        features: BarFeatures,
        gate: HestonGateResult,
        signal_mode: str,
        context_priors: Optional[ContextPriors],
        early_decision: Optional[object] = None,
        slack_sent: Optional[bool] = None,
        exit_plan: Optional[object] = None,
        shadow: Optional[dict[str, object]] = None,
    ) -> None:
        if not self.logger:
            return
        if bar_index % max(1, self.every_bars) != 0:
            return

        row = {
            "ts_cst": features.dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": features.bar.open,
            "high": features.bar.high,
            "low": features.bar.low,
            "close": features.bar.close,
            "ema21": (features.ema21 if features.ema21 == features.ema21 else None),
            "atr5": (features.atr5 if features.atr5 == features.atr5 else None),
            "touch": bool(features.touch),
            "iv_atm": features.iv_atm,
            "rv30": (features.rv30 if features.rv30 == features.rv30 else None),
            "rv60": (features.rv60 if features.rv60 == features.rv60 else None),
            "rv90": (features.rv90 if features.rv90 == features.rv90 else None),
            "vrp": (gate.vrp if gate.vrp == gate.vrp else None),
            "mr": (gate.mr if gate.mr == gate.mr else None),
            "vov": (gate.vov if gate.vov == gate.vov else None),
            "gate_ok": bool(gate.ok),
            "gate_reason": gate.reason,
            "greeks_symbols": features.greeks_symbols,
            "latest_spx_mid": features.latest_spx_mid,
            "signal_mode": signal_mode,
            "context_regime": (context_priors.regime if context_priors else None),
            "context_score": (context_priors.score if context_priors else None),
            "overnight_rv60": (
                context_priors.overnight_rv60 if context_priors else None
            ),
            "overnight_vov": (context_priors.overnight_vov if context_priors else None),
            "overnight_range_pct": (
                context_priors.overnight_range_pct if context_priors else None
            ),
            "early_ok": (
                bool(getattr(early_decision, "ok"))
                if early_decision is not None
                else None
            ),
            "early_reason": (
                getattr(early_decision, "reason", None)
                if early_decision is not None
                else None
            ),
            "slack_sent": slack_sent,
            "exit_plan": exit_plan,
        }
        if features.trend is not None:
            row["trend"] = features.trend.as_dict()
        if isinstance(shadow, dict):
            row.update(shadow)

        self.logger.write(row)
