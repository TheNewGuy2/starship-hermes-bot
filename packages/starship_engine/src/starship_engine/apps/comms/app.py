from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.comms.settings import CommsSettings


class CommsApp:
    name = "comms"
    settings = CommsSettings()
    logger = get_logger(__name__)


app = CommsApp()
name = app.name
