"""Support for rendering and loading the Grafana-backed synoptic image."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Mapping, Optional

from maglab_events_bot.config import get_settings
from maglab_events_bot.services.grafana import GrafanaSample
from maglab_events_bot.services.synoptic import generate_synoptic_image

logger = logging.getLogger(__name__)


def _render_synoptic_image_bytes(
    samples: Mapping[str, GrafanaSample],
    target: Path,
) -> Optional[bytes]:
    generated = generate_synoptic_image(samples, target)
    if generated is None:
        if target.exists():
            logger.warning("Synoptic regeneration failed; serving cached version")
        else:
            logger.warning("Synoptic image could not be generated")
            return None
    try:
        return target.read_bytes()
    except FileNotFoundError:
        logger.warning("Synoptic image file missing at %s", target)
        return None


def get_synoptic_image_bytes(
    samples: Optional[Mapping[str, GrafanaSample]] = None,
    output_path: Optional[Path] = None,
) -> Optional[bytes]:
    """Render the current snapshot and return the resulting PNG bytes."""

    settings = get_settings()
    target = output_path or settings.synoptic_output_path
    if samples is None:
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None
    return _render_synoptic_image_bytes(samples, target)


async def get_synoptic_image_bytes_async(
    samples: Optional[Mapping[str, GrafanaSample]] = None,
    output_path: Optional[Path] = None,
) -> Optional[bytes]:
    """Render synoptic bytes without blocking the event loop."""

    settings = get_settings()
    target = output_path or settings.synoptic_output_path
    if samples is None:
        return await asyncio.to_thread(get_synoptic_image_bytes, None, target)
    return await asyncio.to_thread(_render_synoptic_image_bytes, samples, target)
