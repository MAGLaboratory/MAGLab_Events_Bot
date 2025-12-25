import asyncio
from dataclasses import dataclass, field

import discord
import pendulum

from maglab_events_bot.models.events import CalendarEvent
from maglab_events_bot.services import discord_api


@dataclass
class StubEvent:
    id: int
    name: str
    start_time: pendulum.DateTime
    end_time: pendulum.DateTime
    location: str = "MAG Laboratory"
    status: discord.EventStatus = discord.EventStatus.scheduled
    description: str | None = ""
    edits: list[dict] = field(default_factory=list)
    deleted: bool = False

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self

    async def delete(self):
        self.deleted = True


def test_pick_synoptic_target_prefers_active_non_we():
    now = pendulum.now("UTC")
    active_main = StubEvent(1, "Main Event", now.subtract(minutes=10), now.add(hours=1))
    active_we = StubEvent(2, "We are OPEN", now.subtract(minutes=5), now.add(minutes=30))
    upcoming = StubEvent(3, "Future Event", now.add(hours=2), now.add(hours=3))

    target = discord_api.pick_synoptic_target([active_main, active_we, upcoming], "UTC")
    assert target is active_main


class StubGuild:
    def __init__(self, guild_id: int = 123) -> None:
        self.id = guild_id


def test_enforce_single_synoptic_image_sets_and_clears(monkeypatch):
    now = pendulum.now("UTC")
    active = StubEvent(1, "Main Event", now.subtract(minutes=5), now.add(minutes=55))
    previous = StubEvent(2, "Previous Event", now.subtract(hours=2), now.subtract(minutes=1))

    async def fake_fetch_relevant_events(_guild):
        return [active, previous]

    monkeypatch.setattr(discord_api, "fetch_relevant_events", fake_fetch_relevant_events)
    previous.edits.append({"image": b"old"})

    cache = discord_api.SynopticImageCache()
    cache.update(123, event_id=previous.id, content_hash="old-hash")
    guild = StubGuild()

    # Initial enforcement attaches image to active event
    asyncio.run(
        discord_api.enforce_single_synoptic_image(
            guild,
            b"image-bytes",
            "UTC",
            cache=cache,
        )
    )
    assert active.edits and active.edits[-1]["image"] == b"image-bytes"
    assert previous.edits[-1]["image"] is None

    # Second call with same image avoids duplicate edits
    asyncio.run(
        discord_api.enforce_single_synoptic_image(
            guild,
            b"image-bytes",
            "UTC",
            cache=cache,
        )
    )
    assert len([edit for edit in active.edits if edit.get("image")]) == 1

    # Changing image causes update and clears previous target
    new_bytes = b"new-image"
    asyncio.run(
        discord_api.enforce_single_synoptic_image(
            guild,
            new_bytes,
            "UTC",
            cache=cache,
        )
    )
    assert active.edits[-1]["image"] == new_bytes
    assert any(edit.get("image") is None for edit in previous.edits)


def test_find_matching_discord_event_prefers_uid_marker():
    start_time = pendulum.now("UTC").replace(second=0, microsecond=0)
    calendar_event = CalendarEvent(
        uid="uid-123",
        name="Curator Hours",
        description="Updated description",
        start_time=start_time,
        end_time=start_time.add(hours=2),
        location="MAG Laboratory",
    )

    existing = StubEvent(1, "Curator Hours", calendar_event.start_time, calendar_event.end_time)
    existing.description = discord_api.apply_uid_marker(
        "Original description", calendar_event.instance_uid
    )

    match = discord_api.find_matching_discord_event([existing], calendar_event)
    assert match is existing


def test_find_matching_discord_event_requires_start_time_with_legacy_uid():
    now = pendulum.now("UTC").replace(second=0, microsecond=0)
    existing = StubEvent(1, "Curator Hours", now, now.add(hours=2))
    existing.description = discord_api.apply_uid_marker("Original description", "legacy-uid")

    calendar_event = CalendarEvent(
        uid="legacy-uid",
        name="Curator Hours",
        description="Updated description",
        start_time=now.add(days=1),
        end_time=now.add(days=1, hours=2),
        location="MAG Laboratory",
    )

    match = discord_api.find_matching_discord_event([existing], calendar_event)
    assert match is None


def test_find_matching_discord_event_reschedules_singleton_uid():
    start_time = pendulum.now("UTC").replace(second=0, microsecond=0)
    existing = StubEvent(1, "Curator Hours", start_time, start_time.add(hours=2))
    existing.description = discord_api.apply_uid_marker(
        "Original description", "singleton-uid::" + start_time.to_iso8601_string()
    )

    calendar_event = CalendarEvent(
        uid="singleton-uid",
        name="Curator Hours",
        description="Updated description",
        start_time=start_time.add(hours=1),
        end_time=start_time.add(hours=3),
        location="MAG Laboratory",
    )

    match = discord_api.find_matching_discord_event([existing], calendar_event)
    assert match is existing


def test_apply_uid_marker_obeys_discord_limit():
    description = "A" * 995
    result = discord_api.apply_uid_marker(description, "uid-456")
    assert len(result) <= 1000
    assert result.endswith("uid-456]")


def test_prune_orphaned_events_removes_duplicate_keys(monkeypatch):
    now = pendulum.now("UTC").add(days=2)
    start = now
    end = start.add(hours=2)

    keeper = StubEvent(1, "Curator Hours: Ben", start, end)
    duplicate = StubEvent(2, "Curator Hours: Ben", start, end)

    async def fake_fetch_relevant_events(_guild):
        return [keeper, duplicate]

    monkeypatch.setattr(discord_api, "fetch_relevant_events", fake_fetch_relevant_events)

    calendar_key = (
        "Curator Hours: Ben",
        start.replace(second=0, microsecond=0),
        "MAG Laboratory",
    )

    asyncio.run(
        discord_api.prune_orphaned_events(
            StubGuild(),
            [calendar_key],
            timezone_name="UTC",
        )
    )

    assert not keeper.deleted
    assert duplicate.deleted
