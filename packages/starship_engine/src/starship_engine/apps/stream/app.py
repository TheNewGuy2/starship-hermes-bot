from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.stream.settings import StreamSettings


class StreamApp:
    name = "stream"
    settings = StreamSettings()
    logger = get_logger(__name__)


app = StreamApp()
name = app.name
