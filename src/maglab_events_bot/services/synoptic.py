"""Services for generating the MAGLab synoptic status image."""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Iterable, Tuple

import requests
from bs4 import BeautifulSoup
from PIL import Image

from maglab_events_bot.utils.http import build_session

logger = logging.getLogger(__name__)

# Configure Cairo on Windows if needed
if platform.system() == "Windows":
    os.environ.setdefault(
        "PATH",
        os.environ.get("PATH", "") + r";C:\\Program Files\\UniConvertor-2.0rc5\\dlls",
    )

import cairosvg  # noqa: E402  pylint: disable=wrong-import-position

DEFAULT_CROP_BOX = (180, 72, 1000, 540)
DEFAULT_SIZE = (880, 352)


def scrape_svg(url: str, svg_id: str, session: requests.Session | None = None) -> str | None:
    session = session or build_session()
    try:
        response = session.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "lxml")
        svg_element = soup.find("svg", {"id": svg_id})
        if svg_element:
            return str(svg_element)
        logger.error("SVG with id '%s' not found at %s", svg_id, url)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Error scraping SVG from %s: %s", url, exc)
    return None


def ensure_emoji_font(svg_content: str) -> str:
    return svg_content.replace(
        "font-family:DejaVu Sans, sans-serif;",
        "font-family:DejaVu Sans, Noto Emoji, sans-serif;",
    )


def _wrap_svg(svg_content: str, width: int = 1000, height: int = 1000) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">\n'
        f"{svg_content}</svg>"
    )


def save_scaled_png(
    svg_content: str,
    output_path: Path,
    crop_box: Tuple[int, int, int, int] = DEFAULT_CROP_BOX,
    target_size: Tuple[int, int] = DEFAULT_SIZE,
) -> Path:
    temporary_png = output_path.with_suffix(".tmp.png")
    try:
        svg_with_size = ensure_emoji_font(_wrap_svg(svg_content))
        cairosvg.svg2png(bytestring=svg_with_size.encode("utf-8"), write_to=str(temporary_png))

        with Image.open(temporary_png) as img:
            cropped_img = img.crop(crop_box)
            resized_img = cropped_img.resize(target_size)
            resized_img.save(output_path)
        return output_path
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to save scaled synoptic PNG: %s", exc)
        raise
    finally:
        if temporary_png.exists():
            temporary_png.unlink(missing_ok=True)


def generate_synoptic_image(
    url: str,
    svg_id: str,
    output_path: Path,
    crop_box: Tuple[int, int, int, int] = DEFAULT_CROP_BOX,
    target_size: Tuple[int, int] = DEFAULT_SIZE,
    session: requests.Session | None = None,
) -> Path | None:
    svg_content = scrape_svg(url, svg_id, session=session)
    if not svg_content:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return save_scaled_png(svg_content, output_path, crop_box, target_size)
    except Exception:  # pylint: disable=broad-except
        return None


def generate_synoptic_image_if_needed(
    url: str,
    svg_id: str,
    output_path: Path,
    dependencies: Iterable[Path] | None = None,
) -> Path | None:
    if dependencies and not all(dep.exists() for dep in dependencies):
        logger.warning("Dependencies missing; skipping synoptic generation")
        return None
    if output_path.exists():
        return output_path
    return generate_synoptic_image(url, svg_id, output_path)
