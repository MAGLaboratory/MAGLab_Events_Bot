import pendulum
import pytest

from maglab_events_bot.cogs.calendar_sync import CalendarSyncCog
from maglab_events_bot.models.events import CalendarEvent


@pytest.fixture(autouse=True)
def _ensure_discord_token(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    yield


def test_build_calendar_keys_normalizes_microseconds():
    start = pendulum.now("UTC").replace(second=42, microsecond=123456)
    event = CalendarEvent(
        uid="abc",
        name="Test Event",
        description="",
        start_time=start,
        end_time=start.add(hours=1),
        location="MAG Laboratory",
    )

    keys = list(CalendarSyncCog._build_calendar_keys([event]))

    assert keys == [
        (
            "Test Event",
            start.replace(second=0, microsecond=0),
            "MAG Laboratory",
        )
    ]
