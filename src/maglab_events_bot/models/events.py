"""Data models used across services and tasks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pendulum


@dataclass(slots=True)
class HalSensorReading:
    name: str
    status: str
    last_update_display: str


@dataclass(slots=True)
class HalStatus:
    status_text: str
    sensors: List[HalSensorReading]
    scraped_at: pendulum.DateTime

    @property
    def is_open(self) -> bool:
        return self.status_text.lower().startswith("we are open")


@dataclass(slots=True)
class CalendarEvent:
    uid: str
    name: str
    description: str
    start_time: pendulum.DateTime
    end_time: pendulum.DateTime
    location: str


@dataclass(slots=True)
class CancelledCalendarEvent:
    uid: str
    name: str
    start_time: pendulum.DateTime
    end_time: pendulum.DateTime
    location: str
    description: Optional[str] = None


@dataclass(slots=True)
class SynopticImage:
    path: str
    generated_at: datetime
