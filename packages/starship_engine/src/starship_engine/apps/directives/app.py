from __future__ import annotations

from pydantic import BaseModel

from starship_engine.apps.captain_log.logging import get_logger


class DirectivesSettings(BaseModel):
    enabled: bool = True


class DirectivesApp:
    name = "directives"
    settings = DirectivesSettings()
    logger = get_logger(__name__)


app = DirectivesApp()
name = app.name
