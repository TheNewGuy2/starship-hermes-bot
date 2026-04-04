# Shared sentinel domain types.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class MarketSnapshot:
    ts: datetime
    symbol: str
    timeframe: str
    iv_atm: Optional[float]
    rv30: float
    rv60: float
    rv90: float
    vrp: float
    vov: float
    mr: float
    adx: Optional[float] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketState:
    regime_label: str
    score: int
    flags: list[str]
    reasons: list[str]


class SentinelEventType:
    REGIME_CHANGE = "REGIME_CHANGE"
    SCORE_CROSS = "SCORE_CROSS"
    VRP_BREAK = "VRP_BREAK"
    VOV_SPIKE = "VOV_SPIKE"
    MR_BREAK = "MR_BREAK"
    HEARTBEAT = "HEARTBEAT"


@dataclass(frozen=True)
class SentinelEvent:
    type: str
    ts: datetime
    snapshot: MarketSnapshot
    state: MarketState
    changes: list[str] = field(default_factory=list)
    severity: str = "info"
