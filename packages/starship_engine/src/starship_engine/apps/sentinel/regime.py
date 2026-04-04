# src/bot/regime.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from starship_engine.apps.heston.logic import HestonGateParams, HestonGateResult


@dataclass(frozen=True)
class Regime:
    state: str  # "SKIP" | "CAUTION" | "OK"
    reason: str


def classify_regime(
    *,
    gate: HestonGateResult,
    iv_disp: Optional[float],
    params: HestonGateParams,
    vrp_buffer: float = 0.10,
    disp_caution: float = 0.03,
) -> Regime:
    """
    Convert hard gate output into a trader-friendly regime:
      - SKIP = hard no
      - CAUTION = passes hard gate but borderline
      - OK = clean pass
    """
    if not gate.ok:
        return Regime("SKIP", gate.reason)

    # Borderline VRP
    if gate.vrp == gate.vrp and gate.vrp < (params.vrp_min + vrp_buffer):
        return Regime("CAUTION", "VRP borderline")

    # MR slightly positive (still <= mr_max)
    if gate.mr == gate.mr and gate.mr > 0.0:
        return Regime("CAUTION", "MR slightly positive")

    # VoV elevated (if using vov_max)
    if params.vov_max is not None and gate.vov == gate.vov:
        if gate.vov > 0.75 * params.vov_max:
            return Regime("CAUTION", "VoV elevated")

    # IV dispersion wide
    if iv_disp is not None and iv_disp > disp_caution:
        return Regime("CAUTION", "IV dispersion wide")

    return Regime("OK", "OK")
