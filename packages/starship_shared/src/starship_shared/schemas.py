from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from starship_shared.enums import FactKind


class MarketRef(BaseModel):
    underlier: str
    session: str = "RTH"
    timeframe: str = "15m"


class EngineFactV1(BaseModel):
    schema_version: int = 1
    kind: FactKind = FactKind.MARKET_FACT

    run_id: str
    seq: int
    ts: str  # ISO8601 UTC

    market: MarketRef
    metrics: dict[str, float] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)

    strategy: Optional[str] = None
    signal: Optional[dict[str, Any]] = None


class CandleBarV1(BaseModel):
    schema_version: int = 1
    ts: datetime  # UTC candle close
    symbol: str
    timeframe: str
    o: float
    h: float
    l: float
    c: float
    v: int = 0


class OptionSnapshotV1(BaseModel):
    schema_version: int = 1
    ts: datetime  # UTC candle close
    symbol: str
    timeframe: str
    dte: int
    spot: float
    iv_atm: float
    iv_atm_method: Optional[str] = None
    quote_ts: Optional[datetime] = None
    quality: Optional[dict[str, Any]] = None
    extras: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None
