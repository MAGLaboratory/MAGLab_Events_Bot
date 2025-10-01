from textwrap import dedent

import pendulum
import pytest

from maglab_events_bot.services.calendar import CalendarFetcher


class DummyResponse:
    def __init__(self, content: str) -> None:
        self.content = content.encode("utf-8")

    def raise_for_status(self) -> None:  # pragma: no cover - nothing to do
        return None


class DummySession:
    def __init__(self, ics_content: str) -> None:
        self._content = ics_content

    def get(self, _url: str) -> DummyResponse:  # pragma: no cover - executor wraps
        return DummyResponse(self._content)


def _format_datetime(dt: pendulum.DateTime) -> str:
    return dt.in_timezone("UTC").format("YYYYMMDDTHHmmss") + "Z"


@pytest.mark.asyncio
async def test_fetch_events_parses_single_event():
    now = pendulum.now("UTC")
    start = now.add(days=1).replace(second=0, microsecond=0)
    end = start.add(hours=1)

    ics = dedent(
        f"""
        BEGIN:VCALENDAR
        VERSION:2.0
        BEGIN:VEVENT
        UID:test-single
        DTSTART:{_format_datetime(start)}
        DTEND:{_format_datetime(end)}
        SUMMARY:Board Game Night
        DESCRIPTION:Bring games!
        LOCATION:MAG Laboratory
        END:VEVENT
        END:VCALENDAR
        """
    )

    fetcher = CalendarFetcher(session=DummySession(ics))
    events, cancellations = await fetcher.fetch_events(["dummy://single"], sync_horizon_days=7, timezone_name="America/Los_Angeles")

    assert cancellations == []
    assert len(events) == 1
    event = events[0]
    assert event.name == "Board Game Night"
    assert event.location == "MAG Laboratory"
    assert event.start_time == start
    assert event.end_time == end


@pytest.mark.asyncio
async def test_fetch_events_handles_all_day_and_duration():
    tz = pendulum.timezone("America/Los_Angeles")
    start_date = pendulum.now(tz).add(days=2).date()
    all_day = start_date.strftime("%Y%m%d")

    ics = dedent(
        f"""
        BEGIN:VCALENDAR
        VERSION:2.0
        BEGIN:VEVENT
        UID:test-allday
        DTSTART;VALUE=DATE:{all_day}
        DURATION:PT2H
        SUMMARY:Volunteer Shift
        LOCATION:MAG Laboratory
        END:VEVENT
        END:VCALENDAR
        """
    )

    fetcher = CalendarFetcher(session=DummySession(ics))
    events, _ = await fetcher.fetch_events(["dummy://allday"], sync_horizon_days=7, timezone_name="America/Los_Angeles")

    assert len(events) == 1
    event = events[0]
    assert event.name == "Volunteer Shift"
    assert event.end_time > event.start_time


@pytest.mark.asyncio
async def test_fetch_events_expands_recurring_with_cancellation():
    now = pendulum.now("UTC").replace(second=0, microsecond=0)
    start = now.add(days=1)
    end = start.add(hours=1)
    cancelled_start = start.add(days=1)

    ics = dedent(
        f"""
        BEGIN:VCALENDAR
        VERSION:2.0
        BEGIN:VEVENT
        UID:test-recur
        DTSTART:{_format_datetime(start)}
        DTEND:{_format_datetime(end)}
        SUMMARY:Daily Meetup
        LOCATION:MAG Laboratory
        RRULE:FREQ=DAILY;COUNT=3
        END:VEVENT
        BEGIN:VEVENT
        UID:test-recur
        DTSTART:{_format_datetime(cancelled_start)}
        DTEND:{_format_datetime(cancelled_start.add(hours=1))}
        STATUS:CANCELLED
        END:VEVENT
        END:VCALENDAR
        """
    )

    fetcher = CalendarFetcher(session=DummySession(ics))
    events, cancellations = await fetcher.fetch_events(["dummy://recur"], sync_horizon_days=7, timezone_name="America/Los_Angeles")

    # Expect two events (one cancelled occurrence removed)
    assert len(events) == 2
    assert len(cancellations) == 1
    cancelled = cancellations[0]
    assert cancelled.start_time == cancelled_start
