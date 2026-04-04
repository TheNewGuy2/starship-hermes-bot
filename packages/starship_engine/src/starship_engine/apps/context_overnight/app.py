from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.context_overnight.logic import (
    ContextPriors,
    compute_context_priors,
)
from starship_engine.apps.context_overnight.settings import ContextOvernightSettings


class ContextOvernightApp:
    name = "context_overnight"
    settings = ContextOvernightSettings()
    logger = get_logger(__name__)


app = ContextOvernightApp()
name = app.name


__all__ = [
    "ContextPriors",
    "compute_context_priors",
]
