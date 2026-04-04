from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.heston.logic import HestonGateParams
from starship_engine.apps.heston.settings import HestonSettings


class HestonApp:
    name = "heston"
    settings = HestonSettings()
    logger = get_logger(__name__)


app = HestonApp()
name = app.name


def to_params(settings: HestonSettings) -> HestonGateParams:
    return HestonGateParams(
        enabled=settings.enabled,
        vrp_min=settings.vrp_min,
        vrp_max=settings.vrp_max,
        mr_max=settings.mr_max,
        rv60_min=settings.rv60_min,
        vov_max=settings.vov_max,
    )
