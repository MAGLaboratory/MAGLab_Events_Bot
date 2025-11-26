"""Support for synoptic image generation and loading."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from maglab_events_bot.config import Settings, get_settings
from maglab_events_bot.services.synoptic import generate_synoptic_image
from maglab_events_bot.utils.http import build_session

logger = logging.getLogger(__name__)


def _synoptic_is_stale(target: Path, max_age_minutes: int) -> bool:
    """Return True if the cached synoptic image is older than the allowed window."""
    try:
        modified = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
    except FileNotFoundError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    return modified < cutoff


def _load_synoptic_image_bytes(settings: Settings, target: Path) -> Optional[bytes]:
    needs_refresh = _synoptic_is_stale(target, settings.synoptic_max_age_minutes)

    if needs_refresh:
        session = build_session(timeout=settings.synoptic_http_timeout_seconds)
        generated = generate_synoptic_image(
            str(settings.hal_url),
            "maglab-synoptic-view",
            target,
            session=session,
        )
        if generated is None:
            if target.exists():
                logger.warning(
                    "Synoptic image stale but regeneration failed; serving cached version"
                )
            else:
                logger.warning("Synoptic image could not be generated")
                return None
    try:
        return target.read_bytes()
    except FileNotFoundError:
        logger.warning("Synoptic image file missing at %s", target)
        return None


def get_synoptic_image_bytes(output_path: Optional[Path] = None) -> Optional[bytes]:
    """Synchronous helper retained for backwards compatibility."""
    settings = get_settings()
    target = output_path or settings.synoptic_output_path
    return _load_synoptic_image_bytes(settings, target)


async def get_synoptic_image_bytes_async(output_path: Optional[Path] = None) -> Optional[bytes]:
    """Fetch or generate synoptic bytes without blocking the event loop."""
    settings = get_settings()
    target = output_path or settings.synoptic_output_path
    return await asyncio.to_thread(_load_synoptic_image_bytes, settings, target)
