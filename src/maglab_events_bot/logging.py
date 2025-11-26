"""Logging configuration utilities."""

from __future__ import annotations

import logging
import logging.config
import os
from datetime import datetime, timezone
from json import dumps
from pathlib import Path
from typing import Optional

DEFAULT_LOG_PATH = Path("logs/maglab_events_bot.log")

STRUCTURED_EXCLUDE_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "asctime",
    "message",
}


class StructuredJsonFormatter(logging.Formatter):
    """Output logs as JSON with extras folded into a single object."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        message = record.getMessage()
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in STRUCTURED_EXCLUDE_KEYS
        }

        payload = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        if extras:
            payload["extra"] = extras

        return dumps(payload, default=str)


def configure_logging(
    log_path: Optional[Path] = None,
    level: int = logging.INFO,
    structured: Optional[bool] = None,
) -> None:
    """Configure logging with human-readable console and optional structured file output."""
    target_path = log_path or DEFAULT_LOG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Allow operators to enable JSON logs via LOG_STRUCTURED=true|1|yes (default is text for readability)
    if structured is None:
        env_value = os.getenv("LOG_STRUCTURED", "false").lower()
        structured = env_value in {"1", "true", "yes"}

    file_formatter = "structured" if structured else "standard"

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            },
            "structured": {
                "()": StructuredJsonFormatter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": level,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": file_formatter,
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
