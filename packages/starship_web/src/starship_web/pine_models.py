from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: object) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _as_int(value: object, default: int) -> int:
    if value in (None, "", "null"):
        return default
    try:
        return int(float(value))  # type: ignore[arg-type]
    except Exception:
        return default


class PineAlertEvent(BaseModel):
    schema_version: int = 1
    event_id: str
    strategy_name: str
    event_type: Literal["ENTRY", "MANAGE", "CLOSE"]
    position_key: str
    underlier: str
    option_root: str | None = None
    bar_time: str | None = None
    expiry_year: int | None = None
    expiry_month: int | None = None
    expiry_day: int | None = None
    quantity: int = 1
    short_put: float | None = None
    long_put: float | None = None
    short_call: float | None = None
    long_call: float | None = None
    target_entry_credit: float | None = None
    target_close_debit: float | None = None
    note: str | None = None
    aux: dict[str, object] = Field(default_factory=dict)
    raw_payload: dict[str, object] = Field(default_factory=dict)


class PineTradeState(BaseModel):
    schema_version: int = 1
    state_id: str
    strategy_name: str
    position_key: str
    underlier: str
    option_root: str | None = None
    status: str = "idle"
    active: bool = False
    quantity: int = 1
    entry_ticket_id: str | None = None
    close_ticket_id: str | None = None
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    opened_at: str | None = None
    closed_at: str | None = None
    last_event_id: str | None = None
    last_event_type: str | None = None
    last_event_at: str | None = None
    current_strikes: dict[str, float | None] = Field(default_factory=dict)
    latest_aux: dict[str, object] = Field(default_factory=dict)
    latest_live_mark: dict[str, object] = Field(default_factory=dict)
    latest_pine_payload: dict[str, object] = Field(default_factory=dict)
    processed_event_ids: list[str] = Field(default_factory=list)
    event_log: list[dict[str, object]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def parse_pine_alert_payload(payload: dict[str, object]) -> PineAlertEvent:
    strategy_name = (
        _as_str(payload.get("strategy_name"))
        or _as_str(payload.get("strategy"))
        or _as_str(payload.get("tag"))
        or "pine_strategy"
    )
    raw_event_type = (
        _as_str(payload.get("event_type"))
        or _as_str(payload.get("event"))
        or _as_str(payload.get("action"))
        or "MANAGE"
    ).upper()
    event_type = {"EXIT": "CLOSE"}.get(raw_event_type, raw_event_type)
    if event_type not in {"ENTRY", "MANAGE", "CLOSE"}:
        raise ValueError(f"Unsupported Pine event_type: {raw_event_type}")

    underlier = (
        _as_str(payload.get("underlier"))
        or _as_str(payload.get("symbol"))
        or _as_str(payload.get("ticker"))
        or "SPX"
    ).upper()
    option_root = (
        _as_str(payload.get("option_root"))
        or _as_str(payload.get("root"))
        or _as_str(payload.get("broker_root"))
    )
    bar_time = (
        _as_str(payload.get("bar_time"))
        or _as_str(payload.get("timestamp"))
        or _as_str(payload.get("ts"))
        or _as_str(payload.get("time"))
    )
    position_key = (
        _as_str(payload.get("position_key"))
        or _as_str(payload.get("trade_id"))
        or _as_str(payload.get("signal_key"))
        or f"{strategy_name}:{underlier}"
    )

    strikes = {
        "short_put": _as_float(payload.get("short_put")),
        "long_put": _as_float(payload.get("long_put")),
        "short_call": _as_float(payload.get("short_call")),
        "long_call": _as_float(payload.get("long_call")),
    }

    event_id = (
        _as_str(payload.get("event_id"))
        or _as_str(payload.get("signal_id"))
        or _as_str(payload.get("alert_id"))
        or "|".join(
            [
                strategy_name,
                event_type,
                position_key,
                bar_time or "",
                str(strikes["short_put"] or ""),
                str(strikes["short_call"] or ""),
            ]
        )
    )

    aux = payload.get("aux")
    aux_dict = dict(aux) if isinstance(aux, dict) else {}
    for key in (
        "vix1d",
        "vix",
        "vix9d",
        "vvix",
        "skew",
        "add",
        "vx1",
        "vx2",
        "ivrv",
        "pop_between",
        "regime",
        "surface_edge",
        "surface_stress",
        "surface_skew",
        "vol_comp_ratio",
        "zTrigger",
        "zShape",
        "zTrend",
        "zVWAPRecenter",
        "zBody",
        "zShortRisk",
    ):
        if key in payload and key not in aux_dict:
            aux_dict[key] = payload[key]

    return PineAlertEvent(
        event_id=event_id,
        strategy_name=strategy_name,
        event_type=event_type,  # type: ignore[arg-type]
        position_key=position_key,
        underlier=underlier,
        option_root=(option_root.upper() if option_root else None),
        bar_time=bar_time,
        expiry_year=_as_int(payload.get("expiry_year"), 0) or None,
        expiry_month=_as_int(payload.get("expiry_month"), 0) or None,
        expiry_day=_as_int(payload.get("expiry_day"), 0) or None,
        quantity=max(1, _as_int(payload.get("quantity") or payload.get("contracts") or payload.get("qty"), 1)),
        short_put=strikes["short_put"],
        long_put=strikes["long_put"],
        short_call=strikes["short_call"],
        long_call=strikes["long_call"],
        target_entry_credit=_as_float(payload.get("target_entry_credit") or payload.get("entry_credit")),
        target_close_debit=_as_float(payload.get("target_close_debit") or payload.get("close_debit")),
        note=_as_str(payload.get("note") or payload.get("message")),
        aux=aux_dict,
        raw_payload=dict(payload),
    )
