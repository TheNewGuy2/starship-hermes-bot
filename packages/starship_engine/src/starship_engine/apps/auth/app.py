from __future__ import annotations

from starship_engine.apps.auth.settings import AuthSettings
from starship_engine.apps.captain_log.logging import get_logger


class AuthApp:
    name = "auth"
    settings = AuthSettings()
    logger = get_logger(__name__)


app = AuthApp()
name = app.name
