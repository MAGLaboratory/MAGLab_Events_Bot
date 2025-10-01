"""Abstractions over Discord scheduled event operations."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Callable, Coroutine, Iterable, List, Optional, Sequence, Tuple, cast

import discord
import pendulum

from maglab_events_bot.models.events import CalendarEvent, CancelledCalendarEvent

logger = logging.getLogger(__name__)


def _to_utc_datetime(value: datetime | None) -> Optional[pendulum.DateTime]:
    if value is None:
        return None
    return pendulum.instance(value).in_timezone("UTC")


def _filter_active(events: Sequence[discord.ScheduledEvent], tz_name: str) -> Tuple[List[discord.ScheduledEvent], List[discord.ScheduledEvent]]:
    now = pendulum.now("UTC")
    del tz_name
    active: List[discord.ScheduledEvent] = []
    upcoming: List[discord.ScheduledEvent] = []
    for event in events:
        start = _to_utc_datetime(event.start_time)
        end = _to_utc_datetime(event.end_time)
        if start is None or end is None:
            continue
        if start <= now <= end:
            active.append(event)
        elif start > now:
            upcoming.append(event)
    active.sort(key=lambda e: _to_utc_datetime(e.end_time) or now)
    upcoming.sort(key=lambda e: _to_utc_datetime(e.start_time) or now)
    return active, upcoming


async def fetch_relevant_events(guild: discord.Guild) -> List[discord.ScheduledEvent]:
    events = await guild.fetch_scheduled_events()
    return [event for event in events if event.status != discord.EventStatus.completed]


async def delete_events_by_name_fragment(guild: discord.Guild, fragment: str) -> None:
    events = await fetch_relevant_events(guild)
    for event in events:
        if fragment.lower() in (event.name or "").lower():
            try:
                await event.delete()
                logger.info("Deleted event '%s'", event.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to delete event %s: %s", event.name, exc)


def pick_synoptic_target(
    events: Sequence[discord.ScheduledEvent],
    timezone_name: str,
    we_are_fragment: str = "We are",
) -> Optional[discord.ScheduledEvent]:
    active, upcoming = _filter_active(events, timezone_name)

    active_non_we = [e for e in active if we_are_fragment.lower() not in (e.name or "").lower()]
    if active_non_we:
        return active_non_we[0]

    active_we = [e for e in active if we_are_fragment.lower() in (e.name or "").lower()]
    if active_we:
        return active_we[0]

    if upcoming:
        return upcoming[0]
    return None


_last_synoptic_event_id: int | None = None
_last_synoptic_hash: str | None = None


_EditCoroutine = Coroutine[object, object, discord.ScheduledEvent]


async def _apply_event_image(event: discord.ScheduledEvent, image: Optional[bytes]) -> None:
    edit_fn = cast(Callable[..., _EditCoroutine], event.edit)
    await edit_fn(image=image)


async def enforce_single_synoptic_image(
    guild: discord.Guild,
    image_bytes: Optional[bytes],
    timezone_name: str,
    *,
    we_are_fragment: str = "We are",
) -> None:
    global _last_synoptic_event_id, _last_synoptic_hash

    events = await fetch_relevant_events(guild)
    target = pick_synoptic_target(events, timezone_name, we_are_fragment)
    target_id = target.id if target else None
    previous_id = _last_synoptic_event_id

    new_hash: Optional[str] = None
    if target and image_bytes:
        new_hash = hashlib.sha1(image_bytes).hexdigest()
        if target_id != _last_synoptic_event_id or new_hash != _last_synoptic_hash:
            try:
                await _apply_event_image(target, image_bytes)
                logger.info("Synoptic image set on '%s'", target.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to set synoptic image on '%s': %s", target.name, exc)
        else:
            logger.debug("Synoptic image already current on event '%s'", target.name)

    if previous_id and (previous_id != target_id or not new_hash):
        previous = next((e for e in events if e.id == previous_id), None)
        if previous:
            try:
                await _apply_event_image(previous, None)
                logger.info("Cleared synoptic image from '%s'", previous.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to clear synoptic image on '%s': %s", previous.name, exc)

    _last_synoptic_event_id = target_id if new_hash else None
    _last_synoptic_hash = new_hash


async def has_active_non_fragment_event(
    guild: discord.Guild,
    *,
    fragment: str,
) -> bool:
    events = await fetch_relevant_events(guild)
    now = pendulum.now("UTC")
    for event in events:
        start = _to_utc_datetime(event.start_time)
        end = _to_utc_datetime(event.end_time)
        if start is None or end is None:
            continue
        if start <= now <= end and fragment.lower() not in (event.name or "").lower():
            logger.info(
                "Active non-fragment event found: '%s'", event.name
            )
            return True
    return False


def find_matching_discord_event(
    discord_events: Sequence[discord.ScheduledEvent],
    calendar_event: CalendarEvent | CancelledCalendarEvent,
) -> Optional[discord.ScheduledEvent]:
    for event in discord_events:
        if event.status == discord.EventStatus.completed:
            continue
        event_name = event.name or ""
        if event_name != calendar_event.name:
            continue
        start_time = _to_utc_datetime(event.start_time)
        if start_time is None or _to_utc_datetime(event.end_time) is None:
            continue
        start_time = start_time.replace(second=0, microsecond=0)
        cal_start = calendar_event.start_time.replace(second=0, microsecond=0)
        event_location = (event.location or "MAG Laboratory").strip()
        if start_time == cal_start and event_location == calendar_event.location:
            return event
    return None


async def ensure_open_status_event(
    guild: discord.Guild,
    *,
    status_text: str,
    description: str,
    timezone_name: str,
    duration_minutes: int = 10,
    we_are_fragment: str = "We are",
) -> None:
    now = pendulum.now(timezone_name)
    end_time = now.add(minutes=duration_minutes)

    events = await fetch_relevant_events(guild)
    we_events = [e for e in events if we_are_fragment.lower() in (e.name or "").lower()]

    primary_event = we_events[0] if we_events else None

    if primary_event:
        start_time = _to_utc_datetime(primary_event.start_time)
        end_existing = _to_utc_datetime(primary_event.end_time)
        if start_time and end_existing and start_time <= now <= end_existing:
            try:
                await primary_event.edit(
                    name=status_text,
                    description=description,
                    end_time=end_time,
                )
                logger.info("Updated existing open-status event '%s'", primary_event.name)
                return
            except discord.errors.Forbidden:
                logger.warning("Permission denied updating event '%s'; deleting", primary_event.name)
                try:
                    await primary_event.delete()
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Failed to delete event '%s': %s", primary_event.name, exc)
        else:
            try:
                await primary_event.delete()
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to delete stale event '%s': %s", primary_event.name, exc)

    try:
        await guild.create_scheduled_event(
            name=status_text,
            description=description,
            start_time=now.add(seconds=10),
            end_time=end_time,
            entity_type=discord.EntityType.external,
            location="MAG Laboratory",
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        logger.info("Created new open-status event '%s'", status_text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to create open-status event: %s", exc)


async def prune_orphaned_events(
    guild: discord.Guild,
    calendar_keys: Iterable[Tuple[str, pendulum.DateTime, str]],
    *,
    timezone_name: str,
    allow_fragments: Optional[Iterable[str]] = None,
) -> None:
    event_keys = set(calendar_keys)
    allow_fragments = {frag.lower() for frag in allow_fragments or []}
    now = pendulum.now("UTC")

    events = await fetch_relevant_events(guild)
    for event in events:
        start = _to_utc_datetime(event.start_time)
        end = _to_utc_datetime(event.end_time)
        if start is None or end is None:
            continue
        if start <= now <= end:
            continue
        name = event.name or ""
        if any(fragment in name.lower() for fragment in allow_fragments):
            continue
        location = (event.location or "MAG Laboratory").strip()
        key = (name, start.replace(second=0, microsecond=0), location)
        if key not in event_keys:
            try:
                await event.delete()
                logger.info("Deleted orphaned event '%s'", name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to delete event '%s': %s", name, exc)
