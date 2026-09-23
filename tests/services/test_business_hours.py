from datetime import date

import pendulum

from maglab_events_bot.models.events import CalendarEvent
from maglab_events_bot.services.business_hours import build_business_hours_plan

TZ = "America/Los_Angeles"


def event(
    name: str, day: int, start: int, end: int, *, weekly: bool = False, month: int = 9
) -> CalendarEvent:
    start_time = pendulum.datetime(2026, month, day, start, tz=TZ)
    return CalendarEvent(
        uid=f"{name}-{day}",
        name=name,
        description="",
        start_time=start_time.in_timezone("UTC"),
        end_time=start_time.add(hours=end - start).in_timezone("UTC"),
        location="MAGLab",
        weekly_pattern=weekly,
    )


def plan(*events: CalendarEvent):
    return build_business_hours_plan(
        events,
        timezone_name=TZ,
        first_date=date(2026, 9, 21),
        last_date=date(2026, 10, 4),
    )


def test_weekly_weekday_sets_regular_hours_and_one_off_extends_only_its_date():
    result = plan(
        event("Workshop", 21, 10, 12, weekly=True),
        event("Workshop", 28, 10, 12, weekly=True),
        event("Special event", 21, 13, 15),
    )

    assert result.regular_periods == [
        {"openDay": "MONDAY", "openTime": {"hours": 10, "minutes": 0}, "closeDay": "MONDAY", "closeTime": {"hours": 12, "minutes": 0}}
    ]
    assert result.special_periods == [
        {"startDate": {"year": 2026, "month": 9, "day": 21}, "openTime": {"hours": 10, "minutes": 0}, "closeTime": {"hours": 12, "minutes": 0}},
        {"startDate": {"year": 2026, "month": 9, "day": 21}, "openTime": {"hours": 13, "minutes": 0}, "closeTime": {"hours": 15, "minutes": 0}},
    ]


def test_recurring_weekend_sets_regular_hours():
    result = plan(
        event("Open Shop", 26, 10, 13, weekly=True),
        event("Open Shop", 3, 10, 13, weekly=True, month=10),
    )

    assert result.regular_periods == [
        {"openDay": "SATURDAY", "openTime": {"hours": 10, "minutes": 0}, "closeDay": "SATURDAY", "closeTime": {"hours": 13, "minutes": 0}}
    ]
    assert result.special_periods == []


def test_one_off_weekend_remains_date_specific():
    result = plan(event("Weekend Open House", 26, 10, 13))

    assert result.regular_periods == []
    assert result.special_periods == [
        {"startDate": {"year": 2026, "month": 9, "day": 26}, "openTime": {"hours": 10, "minutes": 0}, "closeTime": {"hours": 13, "minutes": 0}}
    ]


def test_cancelled_weekly_occurrence_becomes_one_day_closure():
    result = plan(event("Workshop", 21, 10, 12, weekly=True))

    assert result.special_periods == [
        {"startDate": {"year": 2026, "month": 9, "day": 28}, "closed": True}
    ]


def test_online_and_cancelled_titles_never_add_hours():
    result = plan(
        event("Online Public Business Meeting", 21, 10, 12, weekly=True),
        event("[Cancelled] Open House", 22, 10, 12, weekly=True),
        event("(Cancel) Open House", 23, 10, 12),
        event("Cancellation notice", 24, 10, 12),
    )

    assert result.regular_periods == []
    assert result.special_periods == []


def test_overlapping_events_merge_into_one_special_period():
    result = plan(event("One-off", 26, 10, 13), event("Another", 26, 12, 15))

    assert result.special_periods == [
        {"startDate": {"year": 2026, "month": 9, "day": 26}, "openTime": {"hours": 10, "minutes": 0}, "closeTime": {"hours": 15, "minutes": 0}}
    ]
