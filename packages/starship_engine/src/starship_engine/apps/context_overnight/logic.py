# src/bot/context.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from starship_engine.indicators import (
    CandleBar,
    annualized_realized_vol,
    log_returns,
    vov_proxy_from_returns,
)

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ContextPriors:
    overnight_rv60: Optional[float]
    overnight_vov: Optional[float]
    overnight_range_pct: Optional[float]
    regime: Optional[str]  # "calm" | "mixed" | "hot"
    score: float


def overnight_window_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Overnight window is prior day 16:00 ET through today 09:30 ET.
    """
    now_ny = (now or datetime.now(timezone.utc)).astimezone(NY)
    end_ny = datetime.combine(now_ny.date(), time(9, 30), tzinfo=NY)
    start_ny = datetime.combine(
        now_ny.date() - timedelta(days=1), time(16, 0), tzinfo=NY
    )
    return start_ny.astimezone(timezone.utc), end_ny.astimezone(timezone.utc)


def compute_context_priors(
    bars: Iterable[CandleBar],
    *,
    bars_60m: int = 12,
) -> ContextPriors:
    """
    Compute simple overnight priors from ES candles.
    """
    bars_list = [b for b in bars if b.close > 0]
    if not bars_list:
        return ContextPriors(None, None, None, None, 0.0)

    highs = [b.high for b in bars_list]
    lows = [b.low for b in bars_list]
    opens = [b.open for b in bars_list]
    closes = [b.close for b in bars_list]

    base = opens[0] if opens and opens[0] > 0 else (closes[-1] if closes else 0)
    range_pct = None
    if base and base > 0:
        range_pct = (max(highs) - min(lows)) / base

    rv60 = vov = None
    if len(closes) >= (bars_60m + 1):
        rets_60 = log_returns(closes[-(bars_60m + 1) :])
        rv60 = annualized_realized_vol(rets_60)
        vov = vov_proxy_from_returns(rets_60)

    regime = None
    score = 0.0
    if range_pct is not None and rv60 is not None and vov is not None:
        calm = (range_pct < 0.006) and (rv60 < 0.12) and (vov < 0.004)
        hot = (range_pct > 0.012) or (rv60 > 0.20) or (vov > 0.008)
        if calm:
            regime = "calm"
            score = 1.0
        elif hot:
            regime = "hot"
            score = -1.0
        else:
            regime = "mixed"
            score = 0.0

    return ContextPriors(
        overnight_rv60=rv60,
        overnight_vov=vov,
        overnight_range_pct=range_pct,
        regime=regime,
        score=score,
    )
