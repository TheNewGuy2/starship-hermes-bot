from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ParsedOption:
    event_symbol: str
    right: str  # "C" or "P"
    strike: float


_OCC_OPT_RE = re.compile(r"^\.*\s*([A-Z0-9/]{1,6})\s*(\d{6})([CP])(\d+)\s*$")


def parse_spxw_event_symbol(event_symbol: str) -> Optional[ParsedOption]:
    """
    Parse tasty/OCC option symbols (general).
    Supports:
      "AAPL 220617P00150000"
      "SPY 221118C00400000"
      "SPXW 220520C04025000"
      "AAPL220617P00150000"
      ".SPXW260109C06950000"
    """
    s = (event_symbol or "").strip()
    m = _OCC_OPT_RE.match(s)
    if not m:
        return None
    right = m.group(3)
    strike_str = m.group(4)
    try:
        strike_int = int(strike_str)
    except ValueError:
        return None

    if len(strike_str) == 8:
        strike = strike_int / 1000.0
    elif len(strike_str) >= 7:
        strike = strike_int / 1000.0
    else:
        strike = float(strike_int)

    if strike <= 0 or strike > 200000:
        return None

    return ParsedOption(event_symbol=s, right=right, strike=strike)
