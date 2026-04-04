from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.sentinel.settings import SentinelSettings


class SentinelApp:
    name = "sentinel"
    settings = SentinelSettings()
    logger = get_logger(__name__)


app = SentinelApp()
name = app.name
