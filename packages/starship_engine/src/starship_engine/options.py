# src/bot/options.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re


@dataclass(frozen=True)
class ParsedOption:
    event_symbol: str
    right: str   # "C" or "P"
    strike: float


# Supports:
#   "AAPL 220617P00150000"  (OCC style with space padding)
#   "SPY 221118C00400000"
#   "SPXW 220520C04025000"
#   "AAPL220617P00150000"   (no spaces)
#   ".SPXW260109C06950000"  (leading dot)
#
# Groups:
#   1: root (1-6 chars, may include /)
#   2: yymmdd
#   3: right (C/P)
#   4: strike digits (typically 8, sometimes other lengths from some feeds)
_OCC_OPT_RE = re.compile(r"^\.*\s*([A-Z0-9/]{1,6})\s*(\d{6})([CP])(\d+)\s*$")


def parse_spxw_event_symbol(event_symbol: str) -> Optional[ParsedOption]:
    """
    Parse tasty/OCC option symbols (general).

    Examples:
      AAPL 220617P00150000  -> 150.0
      SPY 221118C00400000   -> 400.0
      SPXW 220520C04025000  -> 4025.0
      .SPXW260109C06950000  -> 6950.0

    Strike decoding:
      OCC/tasty: 8 digits = strike * 1000.
      Some feeds: may emit 7+ digits also meaning strike * 1000.
      Short forms like ...C2800 are also tolerated.
    """
    s = (event_symbol or "").strip()

    m = _OCC_OPT_RE.match(s)
    if not m:
        return None

    # root = m.group(1)   # not used today but could be later
    # yymmdd = m.group(2) # not used today but could be later
    right = m.group(3)
    strike_str = m.group(4)

    try:
        strike_int = int(strike_str)
    except ValueError:
        return None

    # OCC/tasty uses 8 digits for strike*1000
    if len(strike_str) == 8:
        strike = strike_int / 1000.0
    # tolerate dxFeed-ish strike*1000 formats (often 7+ digits)
    elif len(strike_str) >= 7:
        strike = strike_int / 1000.0
    else:
        strike = float(strike_int)

    if strike <= 0 or strike > 200000:
        return None

    return ParsedOption(event_symbol=s, right=right, strike=strike)
