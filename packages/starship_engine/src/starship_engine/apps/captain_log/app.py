from __future__ import annotations

from pathlib import Path

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.captain_log.settings import CaptainLogSettings
from starship_engine.apps.captain_log.state import StateLogConfig, StateLogger
from starship_engine.apps.captain_log.state_log import StateLogHandler


class CaptainLogApp:
    name = "captain_log"
    settings = CaptainLogSettings()
    logger = get_logger(__name__)


app = CaptainLogApp()
name = app.name


def build_state_log_handler(settings: CaptainLogSettings) -> StateLogHandler:
    fmt = settings.fmt.strip().lower()
    if fmt not in ("jsonl", "csv"):
        fmt = "jsonl"

    logger = None
    if settings.enabled:
        logger = StateLogger(
            StateLogConfig(
                enabled=True,
                out_dir=Path(settings.out_dir).expanduser(),
                fmt=fmt,
            ),
            filename_base="spx0dte_state",
        )

    return StateLogHandler(logger, settings.every_bars)
