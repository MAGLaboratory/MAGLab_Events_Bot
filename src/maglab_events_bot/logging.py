"""Logging configuration utilities."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Optional

DEFAULT_LOG_PATH = Path("logs/maglab_events_bot.log")


def configure_logging(log_path: Optional[Path] = None, level: int = logging.INFO) -> None:
    """Configure application-wide logging with console and rotating file handlers."""
    target_path = log_path or DEFAULT_LOG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": level,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "level": level,
                "filename": str(target_path),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
            },
        },
        "root": {
            "handlers": ["console", "file"],
            "level": level,
        },
    }

    logging.config.dictConfig(logging_config)
