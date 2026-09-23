"""Build Google Business Profile hours from the same calendar events as Discord."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import pendulum

from maglab_events_bot.models.events import CalendarEvent
from maglab_events_bot.services.event_rules import is_public_in_person_event

_DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
_Interval = tuple[int, int]


def _merge(intervals: Iterable[_Interval]) -> list[_Interval]:
    merged: list[_Interval] = []
    for start, end in sorted(set(intervals)):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _time_of_day(minutes: int) -> dict[str, int]:
    return {"hours": minutes // 60, "minutes": minutes % 60}


def _google_date(day: date) -> dict[str, int]:
    return {"year": day.year, "month": day.month, "day": day.day}


@dataclass(frozen=True)
class BusinessHoursPlan:
    """A full regular schedule and date-specific exceptions for an inclusive date range."""

    regular_periods: list[dict]
    special_periods: list[dict]

    def as_location_patch(self) -> dict:
        return {
            "regularHours": {"periods": self.regular_periods},
            "specialHours": {"specialHourPeriods": self.special_periods},
        }


def build_business_hours_plan(
    events: Iterable[CalendarEvent],
    *,
    timezone_name: str,
    first_date: date,
    last_date: date,
) -> BusinessHoursPlan:
    """Plan hours; the caller must provide at least two weeks of calendar occurrences.

    Weekly/daily repeating events make the baseline. Every calendar date
    that differs from that baseline becomes a complete special-hours override.
    This preserves regular hours when a one-off event extends a weekday, and
    makes a cancelled recurring occurrence a one-day closure.
    """
    if last_date < first_date:
        raise ValueError("last_date must be on or after first_date")

    timezone = pendulum.timezone(timezone_name)
    weekly: dict[int, list[_Interval]] = defaultdict(list)
    actual: dict[date, list[_Interval]] = defaultdict(list)
    for event in events:
        if not is_public_in_person_event(event.name):
            continue
        start = event.start_time.in_timezone(timezone)
        end = event.end_time.in_timezone(timezone)
        if end <= start:
            continue
        cursor = start
        while cursor < end:
            midnight = cursor.start_of("day").add(days=1)
            segment_end = min(end, midnight)
            day = cursor.date()
            start_minute = cursor.hour * 60 + cursor.minute
            end_minute = 1440 if segment_end == midnight else segment_end.hour * 60 + segment_end.minute
            if end_minute > start_minute:
                if event.weekly_pattern:
                    weekly[day.weekday()].append((start_minute, end_minute))
                if first_date <= day <= last_date:
                    actual[day].append((start_minute, end_minute))
            cursor = segment_end

    baseline = {weekday: _merge(intervals) for weekday, intervals in weekly.items()}
    regular_periods = [
        {
            "openDay": _DAYS[weekday],
            "openTime": _time_of_day(start),
            "closeDay": _DAYS[weekday],
            "closeTime": _time_of_day(end),
        }
        for weekday in range(7)
        for start, end in baseline.get(weekday, [])
    ]

    special_periods: list[dict] = []
    day = first_date
    while day <= last_date:
        expected = baseline.get(day.weekday(), [])
        observed = _merge(actual.get(day, []))
        if observed != expected:
            if not observed:
                special_periods.append({"startDate": _google_date(day), "closed": True})
            else:
                for start, end in observed:
                    special_periods.append(
                        {
                            "startDate": _google_date(day),
                            "openTime": _time_of_day(start),
                            "closeTime": _time_of_day(end),
                        }
                    )
        day += timedelta(days=1)

    return BusinessHoursPlan(regular_periods=regular_periods, special_periods=special_periods)
