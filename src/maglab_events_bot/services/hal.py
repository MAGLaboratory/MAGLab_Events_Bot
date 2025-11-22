"""Services for interacting with the MAGLab HAL status page."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import pendulum
import requests
from bs4 import BeautifulSoup

from maglab_events_bot.models.events import HalSensorReading, HalStatus
from maglab_events_bot.utils.http import build_session

logger = logging.getLogger(__name__)


def truncate_status(status: str) -> str:
    if "°F" in status and "/" in status:
        parts = status.split("/")
        if len(parts) > 1:
            return parts[1].strip()
    return status.replace("No Movement", "No Motion")


def _table_matches_sensor_schema(table) -> bool:
    header_candidates = [cell.get_text(strip=True).lower() for cell in table.find_all("th")]
    if not header_candidates:
        first_row = table.find("tr")
        if first_row:
            header_candidates = [
                cell.get_text(strip=True).lower() for cell in first_row.find_all("td")
            ]

    if not header_candidates:
        return False

    has_sensor = any("sensor" in value for value in header_candidates)
    has_status = any("status" in value for value in header_candidates)
    has_last_update = any("last" in value and "update" in value for value in header_candidates)
    return has_sensor and has_status and has_last_update


def _find_sensor_table(soup: BeautifulSoup):
    tables = soup.find_all("table")
    for table in tables:
        if _table_matches_sensor_schema(table):
            return table
    return None


def _collapse_text(element) -> str:
    """Return the visible text for the element with normalized whitespace."""
    return " ".join(segment.strip() for segment in element.stripped_strings if segment.strip())


def _resolve_lab_status(soup: BeautifulSoup) -> str:
    """Determine the HAL status message from the indicator banner or page text."""
    indicator = soup.select_one("#openness")
    if indicator:
        classes = indicator.get("class") or []
        if any("alert-success" in class_name for class_name in classes):
            return "We are OPEN"
        if any("alert-danger" in class_name for class_name in classes):
            return "We are CLOSED"

        indicator_text = _collapse_text(indicator).lower()
        if "open" in indicator_text and "closed" not in indicator_text:
            return "We are OPEN"
        if "closed" in indicator_text and "open" not in indicator_text:
            return "We are CLOSED"

    page_text_lower = _collapse_text(soup).lower()

    if "we are open" in page_text_lower or "the space is open" in page_text_lower:
        return "We are OPEN"
    if "we are closed" in page_text_lower or "the space is closed" in page_text_lower:
        return "We are CLOSED"

    return "We are OPEN" if ("open" in page_text_lower and "closed" not in page_text_lower) else "We are CLOSED"


def _parse_last_update(timestamp_str: str, tz: pendulum.tz.timezone.Timezone) -> str:
    timestamp_str = timestamp_str.rsplit(" ", 1)[0]
    timestamp_format = "%b %d, %Y, %I:%M %p"
    try:
        parsed = datetime.strptime(timestamp_str, timestamp_format)
        localized = pendulum.instance(parsed, tz=tz.name)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to parse HAL timestamp: %s", timestamp_str)
        return "Unknown"

    delta = pendulum.now(tz) - localized
    if delta < timedelta(minutes=1):
        return "Just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} hr ago"
    return f"{delta.days} days ago"


async def _fetch_hal_page(url: str, session: requests.Session) -> Optional[str]:
    loop = asyncio.get_running_loop()
    for attempt in range(3):
        try:
            response = await loop.run_in_executor(None, session.get, url)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            logger.warning(
                "hal.fetch_failed",
                url,
                attempt + 1,
                exc,
            )
            await asyncio.sleep(2**attempt)
    logger.error("hal.fetch_exhausted", extra={"url": url})
    return None


async def fetch_hal_status(
    url: str,
    tz: pendulum.tz.timezone.Timezone,
    session: Optional[requests.Session] = None,
) -> Optional[HalStatus]:
    session = session or build_session()
    html = await _fetch_hal_page(url, session)
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    lab_status = _resolve_lab_status(soup)

    sensor_data: list[HalSensorReading] = []
    sensor_table = _find_sensor_table(soup)
    if sensor_table:
        for row in sensor_table.find_all("tr"):
            if row.find("th"):
                continue
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            sensor_name = cells[0].get_text(strip=True)
            if sensor_name in {"Page Loaded", "Auto Refresh"} or not sensor_name:
                continue
            status = truncate_status(cells[1].get_text(strip=True))
            last_cell = cells[-1].get_text(strip=True)
            last_update_display = _parse_last_update(last_cell, tz)
            sensor_data.append(
                HalSensorReading(
                    name=sensor_name,
                    status=status,
                    last_update_display=last_update_display,
                )
            )
    else:
        logger.warning("hal.sensor_table_missing", extra={"url": url})

    if not sensor_data:
        logger.warning("hal.sensor_data_empty", extra={"url": url})

    scraped_at = pendulum.now(tz)
    return HalStatus(status_text=lab_status, sensors=sensor_data, scraped_at=scraped_at)
