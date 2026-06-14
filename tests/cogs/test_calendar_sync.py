import discord
import pendulum
import pytest

from maglab_events_bot.cogs.calendar_sync import CalendarSyncCog
from maglab_events_bot.models.events import CalendarEvent
from maglab_events_bot.services.discord_api import apply_uid_marker


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


class StubEvent:
    def __init__(self, calendar_event: CalendarEvent) -> None:
        self.name = calendar_event.name
        self.description = apply_uid_marker(
            calendar_event.description, calendar_event.instance_uid
        )
        self.start_time = calendar_event.start_time
        self.end_time = calendar_event.end_time
        self.location = calendar_event.location
        self.status = discord.EventStatus.scheduled
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self


class StubGuild:
    def __init__(self) -> None:
        self.created = []

    async def create_scheduled_event(self, **kwargs):
        self.created.append(kwargs)


def test_process_events_skips_unchanged_match():
    import asyncio

    start = pendulum.now("UTC").replace(second=0, microsecond=0)
    calendar_event = CalendarEvent(
        uid="abc",
        name="Test Event",
        description="Same description",
        start_time=start,
        end_time=start.add(hours=1),
        location="MAG Laboratory",
    )
    existing = StubEvent(calendar_event)
    guild = StubGuild()
    cog = CalendarSyncCog.__new__(CalendarSyncCog)
    cog.timezone = pendulum.timezone("UTC")

    asyncio.run(cog._process_events(guild, [existing], [calendar_event]))

    assert existing.edits == []
    assert guild.created == []
