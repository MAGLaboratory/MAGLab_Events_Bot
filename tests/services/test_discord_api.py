import asyncio
from dataclasses import dataclass, field

import discord
import pendulum

from maglab_events_bot.services import discord_api


@dataclass
class StubEvent:
    id: int
    name: str
    start_time: pendulum.DateTime
    end_time: pendulum.DateTime
    location: str = "MAG Laboratory"
    status: discord.EventStatus = discord.EventStatus.scheduled
    edits: list[dict] = field(default_factory=list)

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self


def test_pick_synoptic_target_prefers_active_non_we():
    now = pendulum.now("UTC")
    active_main = StubEvent(1, "Main Event", now.subtract(minutes=10), now.add(hours=1))
    active_we = StubEvent(2, "We are OPEN", now.subtract(minutes=5), now.add(minutes=30))
    upcoming = StubEvent(3, "Future Event", now.add(hours=2), now.add(hours=3))

    target = discord_api.pick_synoptic_target([active_main, active_we, upcoming], "UTC")
    assert target is active_main


def test_enforce_single_synoptic_image_sets_and_clears(monkeypatch):
    now = pendulum.now("UTC")
    active = StubEvent(1, "Main Event", now.subtract(minutes=5), now.add(minutes=55))
    previous = StubEvent(2, "Previous Event", now.subtract(hours=2), now.subtract(minutes=1))

    async def fake_fetch_relevant_events(_guild):
        return [active, previous]

    monkeypatch.setattr(discord_api, "fetch_relevant_events", fake_fetch_relevant_events)
    monkeypatch.setattr(discord_api, "_last_synoptic_event_id", previous.id)
    monkeypatch.setattr(discord_api, "_last_synoptic_hash", "old-hash")
    previous.edits.append({"image": b"old"})

    # Initial enforcement attaches image to active event
    asyncio.run(discord_api.enforce_single_synoptic_image(object(), b"image-bytes", "UTC"))
    assert active.edits and active.edits[-1]["image"] == b"image-bytes"
    assert previous.edits[-1]["image"] is None

    # Second call with same image avoids duplicate edits
    asyncio.run(discord_api.enforce_single_synoptic_image(object(), b"image-bytes", "UTC"))
    assert len([edit for edit in active.edits if edit.get("image")]) == 1

    # Changing image causes update and clears previous target
    new_bytes = b"new-image"
    asyncio.run(discord_api.enforce_single_synoptic_image(object(), new_bytes, "UTC"))
    assert active.edits[-1]["image"] == new_bytes
    assert any(edit.get("image") is None for edit in previous.edits)
