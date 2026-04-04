# src/bot/heston.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HestonGateParams:
    enabled: bool = True

    # IV richness threshold
    vrp_min: float = 1.25
    # Optional VRP ceiling to guard against tiny RV60 or extreme skew
    vrp_max: Optional[float] = None

    # Mean reversion threshold: want RV30 - RV90 <= mr_max (small positive tolerance)
    mr_max: float = 0.05

    # Require RV60 above this floor to avoid VRP blowups on tiny realized
    rv60_min: float = 0.05

    # Optional VoV cap (set None to disable)
    vov_max: Optional[float] = None


@dataclass(frozen=True)
class HestonGateResult:
    ok: bool
    reason: str
    vrp: float
    mr: float
    vov: float


def evaluate_heston_gate(
    *,
    enabled: bool,
    iv_atm: Optional[float],
    rv30: float,
    rv60: float,
    rv90: float,
    vov: float,
    params: HestonGateParams | None = None,
) -> HestonGateResult:
    """
    Returns pass/fail + reason for a Heston-style 0DTE 'sell premium' regime gate.

    Why this gate exists (design intent):
    - VRP filter (IV / RV) keeps us from selling premium when implied isn’t rich to realized.
    - Front/back vol slope guard (rv30 - rv90 <= mr_max) avoids entries into rising/unstable front vol.
    - Optional VoV cap lets us skip high vol-of-vol sessions (whipsaw/fast tape).
    - RV60 floor prevents bogus VRP passes when realized is near-zero.
    """
    p = params or HestonGateParams(enabled=enabled)
    if not p.enabled:
        return HestonGateResult(
            True, "disabled", float("nan"), float("nan"), float("nan")
        )

    # Check availability
    if iv_atm is None or iv_atm <= 0:
        return HestonGateResult(
            False, "IV_ATM unavailable", float("nan"), float("nan"), float("nan")
        )
    if not (rv60 == rv60 and rv60 >= p.rv60_min):
        return HestonGateResult(
            False,
            f"RV60 unavailable/low (< {p.rv60_min})",
            float("nan"),
            float("nan"),
            float("nan"),
        )

    vrp = iv_atm / rv60
    mr = rv30 - rv90  # mandatory MR check below
    # vov passed in directly

    # VRP gate
    if vrp < p.vrp_min:
        return HestonGateResult(False, f"VRP too low (< {p.vrp_min})", vrp, mr, vov)
    if p.vrp_max is not None and vrp > p.vrp_max:
        return HestonGateResult(False, f"VRP too high (> {p.vrp_max})", vrp, mr, vov)

    # MR gate (mandatory; require finite rv30/rv90)
    if not (rv30 == rv30 and rv90 == rv90):
        return HestonGateResult(False, "MR unavailable", vrp, float("nan"), vov)
    if mr > p.mr_max:
        return HestonGateResult(False, f"MR positive (> {p.mr_max})", vrp, mr, vov)

    # VoV gate (optional)
    if p.vov_max is not None and vov == vov:
        if vov > p.vov_max:
            return HestonGateResult(False, "VoV spike", vrp, mr, vov)

    return HestonGateResult(True, "OK", vrp, mr, vov)
