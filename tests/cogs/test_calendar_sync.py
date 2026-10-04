import asyncio
from types import SimpleNamespace

import discord
import pendulum
import pytest

from maglab_events_bot.cogs import calendar_sync as calendar_sync_module
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
        self.description = apply_uid_marker(calendar_event.description, calendar_event.instance_uid)
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


def test_hourly_sync_reuses_long_fetch_and_limits_discord_horizon(monkeypatch):
    now = pendulum.now("America/Los_Angeles").start_of("hour")
    near = CalendarEvent(
        uid="near",
        name="Near Event",
        description="",
        start_time=now.add(days=2).in_timezone("UTC"),
        end_time=now.add(days=2, hours=1).in_timezone("UTC"),
        location="MAG Laboratory",
    )
    far = CalendarEvent(
        uid="far",
        name="Far Event",
        description="",
        start_time=now.add(days=30).in_timezone("UTC"),
        end_time=now.add(days=30, hours=1).in_timezone("UTC"),
        location="MAG Laboratory",
    )
    fetch_kwargs = {}
    call_order = []
    discord_events = []

    class FakeFetcher:
        async def fetch_events(self, _urls, **kwargs):
            fetch_kwargs.update(kwargs)
            return [near, far], []

    class SyncGuild:
        async def fetch_scheduled_events(self):
            return []

    cog = CalendarSyncCog.__new__(CalendarSyncCog)
    cog.settings = SimpleNamespace(
        sync_days=7,
        business_hours_sync_enabled=True,
        business_hours_horizon_days=60,
        timezone="America/Los_Angeles",
        guild_id=123,
        get_ics_urls=lambda: ["https://example.com/calendar.ics"],
    )
    cog.timezone = pendulum.timezone(cog.settings.timezone)
    cog.allow_fragments = ("We are",)
    cog.fetcher = FakeFetcher()

    async def fake_business(events, *, now_local):
        call_order.append("business")
        assert events == [near, far]
        assert now_local.timezone_name == "America/Los_Angeles"

    async def fake_get_guild():
        call_order.append("guild")
        return SyncGuild()

    async def fake_process_events(_guild, existing, events):
        discord_events.extend(events)
        return list(existing)

    async def fake_process_cancellations(_guild, existing, _cancellations):
        return list(existing)

    async def fake_prune(*_args, **_kwargs):
        return None

    cog._sync_business_profile_hours = fake_business
    cog._get_guild = fake_get_guild
    cog._process_events = fake_process_events
    cog._process_cancellations = fake_process_cancellations
    monkeypatch.setattr(calendar_sync_module, "prune_orphaned_events", fake_prune)

    asyncio.run(cog._sync_calendar_events_once())

    assert fetch_kwargs["sync_horizon_days"] == 60
    assert fetch_kwargs["strict"] is True
    assert fetch_kwargs["window_start"].hour == 0
    assert call_order == ["business", "guild"]
    assert discord_events == [near]


def test_business_profile_failure_is_contained(monkeypatch, caplog):
    now = pendulum.now("America/Los_Angeles").start_of("hour")
    event = CalendarEvent(
        uid="weekly",
        name="Weekly Event",
        description="",
        start_time=now.add(days=1).in_timezone("UTC"),
        end_time=now.add(days=1, hours=1).in_timezone("UTC"),
        location="MAG Laboratory",
        weekly_pattern=True,
    )
    cog = CalendarSyncCog.__new__(CalendarSyncCog)
    cog.settings = SimpleNamespace(
        business_hours_horizon_days=60,
        timezone="America/Los_Angeles",
        gb_profile_location="locations/12345",
        gb_oauth_client_id="client",
        gb_oauth_client_secret="secret",
        gb_oauth_refresh_token="refresh",
    )

    def fail_publish(*_args, **_kwargs):
        raise RuntimeError("temporary Google failure")

    monkeypatch.setattr(calendar_sync_module, "publish_business_hours", fail_publish)

    asyncio.run(cog._sync_business_profile_hours([event], now_local=now))

    assert "business_hours.sync_failed" in caplog.text
