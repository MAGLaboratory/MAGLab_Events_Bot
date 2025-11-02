"""Formatting helpers for Discord messaging and text cleanup."""

from __future__ import annotations

import html
import re
from typing import Iterable

import pandas as pd

from maglab_events_bot.models.events import HalSensorReading

DESCRIPTION_MAX_LENGTH = 1000


def format_hal_sensor_table(
    status_text: str,
    sensors: Iterable[HalSensorReading],
    scraped_at_display: str,
    source_url: str,
) -> str:
    sensor_rows = [
        {"Sensor": sensor.name, "Status": sensor.status, "Last Update": sensor.last_update_display}
        for sensor in sensors
    ]
    table_string = "No sensor data available."
    if sensor_rows:
        df = pd.DataFrame(sensor_rows)
        table_string = df.to_string(index=False)

    return (
        f"**Lab Status:** {status_text}\n"
        f"**Data Scraped on:** {scraped_at_display}\n"
        f"[Source: {source_url}]\n\n"
        f"**Sensor Data:**\n```\n{table_string}\n```"
    )


def clean_description(description: str) -> str:
    description = re.sub(r"<[^>]+>", "", description)
    return html.unescape(description)


def truncate_description(description: str, max_length: int = DESCRIPTION_MAX_LENGTH) -> str:
    clean_desc = clean_description(description)
    return clean_desc[:max_length] if len(clean_desc) > max_length else clean_desc
