# src/bot/logging.py
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler


def setup_logging(
    app_name: str = "starship-alpha-bot",
    level_name: str | None = None,
    log_file: str | None = None,
    log_daily: bool | None = None,
    log_daily_backup_count: int | None = None,
) -> None:
    """
    Configures root logging once. Use get_logger(__name__) in each module.

    Env:
      BOT_LOG_LEVEL=INFO|DEBUG|...
      BOT_LOG_FILE=logs/starship_engine.log
    """
    if level_name is None:
        level_name = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
    else:
        level_name = level_name.upper()
    level = getattr(logging, level_name, logging.INFO)

    if log_file is None:
        log_file = os.getenv("BOT_LOG_FILE", "logs/starship_engine.log").strip()
    else:
        log_file = log_file.strip()
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid double handlers if re-imported
    if root.handlers:
        return

    # Console
    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Rotating file (size-based) or daily log file
    if log_daily is None:
        daily_logs = os.getenv("BOT_LOG_DAILY", "false").strip().lower() in (
            "1",
            "true",
            "yes",
            "y",
            "on",
        )
    else:
        daily_logs = bool(log_daily)
    if daily_logs:
        if log_daily_backup_count is None:
            backup_count = int(
                os.getenv("BOT_LOG_DAILY_BACKUP_COUNT", "14").strip() or "14"
            )
        else:
            backup_count = int(log_daily_backup_count)
        fh = TimedRotatingFileHandler(
            log_file, when="midnight", interval=1, backupCount=backup_count, utc=False
        )
        fh.suffix = "%Y-%m-%d"
    else:
        fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5)
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Make noisy third-party libs quieter
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("tastytrade").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """
    Module logger helper.
    Example: log = get_logger(__name__)
    """
    return logging.getLogger(name)
