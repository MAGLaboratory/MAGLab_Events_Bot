"""Support for synoptic image generation and loading."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from maglab_events_bot.config import Settings, get_settings
from maglab_events_bot.services.synoptic import generate_synoptic_image

logger = logging.getLogger(__name__)


def _load_synoptic_image_bytes(settings: Settings, target: Path) -> Optional[bytes]:
    if not target.exists():
        generated = generate_synoptic_image(
            str(settings.hal_url),
            "maglab-synoptic-view",
            target,
        )
        if generated is None:
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
