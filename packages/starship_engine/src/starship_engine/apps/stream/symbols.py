# src/bot/symbols.py
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

TZ_NY = ZoneInfo("America/New_York")

SPX_INDEX_SYMBOL = "SPX"
SPX_CHAIN_SYMBOL = "SPX"

def today_ny() -> date:
    from datetime import datetime
    return datetime.now(TZ_NY).date()
