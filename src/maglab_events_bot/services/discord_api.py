"""Abstractions over Discord scheduled event operations."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Callable,
    Collection,
    Coroutine,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    cast,
)

import discord
import pendulum

from maglab_events_bot.models.events import CalendarEvent, CancelledCalendarEvent

logger = logging.getLogger(__name__)

UID_MARKER_PREFIX = "\n\n[maglab_uid:"
UID_MARKER_SUFFIX = "]"
UID_MARKER_MAX_LENGTH = 1000


def _to_utc_datetime(value: datetime | None) -> Optional[pendulum.DateTime]:
    if value is None:
        return None
    return pendulum.instance(value).in_timezone("UTC")


def _filter_active(
    events: Sequence[discord.ScheduledEvent], tz_name: str
) -> Tuple[List[discord.ScheduledEvent], List[discord.ScheduledEvent]]:
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


async def delete_events_by_name_fragment(
    guild: discord.Guild,
    fragment: str,
    *,
    events: Optional[Sequence[discord.ScheduledEvent]] = None,
) -> List[discord.ScheduledEvent]:
    relevant_events = list(events) if events is not None else await fetch_relevant_events(guild)
    remaining_events: List[discord.ScheduledEvent] = []
    for event in relevant_events:
        if fragment.lower() in (event.name or "").lower():
            try:
                await event.delete()
                logger.info("Deleted event '%s'", event.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to delete event %s: %s", event.name, exc)
                remaining_events.append(event)
        else:
            remaining_events.append(event)
    return remaining_events


def _strip_uid_marker(description: Optional[str]) -> str:
    if not description:
        return ""
    index = description.rfind(UID_MARKER_PREFIX)
    if index == -1:
        return description
    return description[:index]


def _ensure_uid_marker(description: Optional[str], uid: str) -> str:
    base = _strip_uid_marker(description).rstrip()
    marker = f"{UID_MARKER_PREFIX}{uid}{UID_MARKER_SUFFIX}"

    if not base:
        trimmed_marker = marker.lstrip("\n")
        if len(trimmed_marker) > UID_MARKER_MAX_LENGTH:
            return trimmed_marker[:UID_MARKER_MAX_LENGTH]
        return trimmed_marker

    available = UID_MARKER_MAX_LENGTH - len(marker)
    if available <= 0:
        trimmed_marker = marker.lstrip("\n")
        return trimmed_marker[:UID_MARKER_MAX_LENGTH]

    base_trimmed = base[:available].rstrip()
    if base_trimmed:
        return base_trimmed + marker

    trimmed_marker = marker.lstrip("\n")
    if len(trimmed_marker) > UID_MARKER_MAX_LENGTH:
        return trimmed_marker[:UID_MARKER_MAX_LENGTH]
    return trimmed_marker


def _extract_uid_marker(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    index = description.rfind(UID_MARKER_PREFIX)
    if index == -1:
        return None
    start = index + len(UID_MARKER_PREFIX)
    end = description.find(UID_MARKER_SUFFIX, start)
    if end == -1:
        return None
    return description[start:end].strip() or None


def apply_uid_marker(description: Optional[str], uid: str) -> str:
    """Append a hidden marker to the description so we can correlate events by UID."""
    return _ensure_uid_marker(description, uid)


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


_EditCoroutine = Coroutine[object, object, discord.ScheduledEvent]


async def _apply_event_image(event: discord.ScheduledEvent, image: Optional[bytes]) -> None:
    edit_fn = cast(Callable[..., _EditCoroutine], event.edit)
    await edit_fn(image=image)


@dataclass(frozen=True)
class SynopticImageState:
    """Track the last synoptic image that was applied to a guild's target event."""

    event_id: int | None = None
    content_hash: str | None = None


class SynopticImageCache:
    """Store per-guild synoptic image state to avoid cross-guild leakage."""

    def __init__(self) -> None:
        self._state: Dict[int, SynopticImageState] = {}

    def get(self, guild_id: int) -> SynopticImageState:
        return self._state.get(guild_id, SynopticImageState())

    def update(self, guild_id: int, *, event_id: int | None, content_hash: str | None) -> None:
        self._state[guild_id] = SynopticImageState(event_id, content_hash)

    def clear(self, guild_id: int) -> None:
        self._state.pop(guild_id, None)


async def enforce_single_synoptic_image(
    guild: discord.Guild,
    image_bytes: Optional[bytes],
    timezone_name: str,
    *,
    we_are_fragment: str = "We are",
    cache: SynopticImageCache,
    events: Optional[Sequence[discord.ScheduledEvent]] = None,
    image_state_key: Optional[str] = None,
) -> None:
    relevant_events = list(events) if events is not None else await fetch_relevant_events(guild)
    target = pick_synoptic_target(relevant_events, timezone_name, we_are_fragment)
    target_id = target.id if target else None
    new_hash: Optional[str] = None
    target_is_current = False
    state = cache.get(guild.id)

    if target and image_bytes:
        new_hash = image_state_key or hashlib.sha1(image_bytes).hexdigest()
        if (
            target_id != state.event_id
            or new_hash != state.content_hash
            or getattr(target, "cover_image", None) is None
        ):
            try:
                await _apply_event_image(target, image_bytes)
                logger.info("Synoptic image set on '%s'", target.name)
                target_is_current = True
            except discord.NotFound:
                logger.info(
                    "Synoptic target '%s' disappeared before image update; skipping",
                    target.name,
                )
                new_hash = None
                target = None
                target_id = None
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to set synoptic image on '%s': %s", target.name, exc)
        else:
            logger.debug("Synoptic image already current on event '%s'", target.name)
            target_is_current = True

    for event in relevant_events:
        if (not target or event.id != target.id) and getattr(
            event, "cover_image", None
        ) is not None:
            try:
                await _apply_event_image(event, None)
            except discord.NotFound:
                logger.info("Event '%s' disappeared before clearing image; skipping", event.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to clear synoptic image on '%s': %s", event.name, exc)

    if target_is_current and new_hash and target_id:
        cache.update(guild.id, event_id=target_id, content_hash=new_hash)
    else:
        cache.clear(guild.id)


async def has_active_non_fragment_event(
    guild: discord.Guild,
    *,
    fragment: str,
    excluded_names: Collection[str] = (),
    events: Optional[Sequence[discord.ScheduledEvent]] = None,
) -> bool:
    relevant_events = list(events) if events is not None else await fetch_relevant_events(guild)
    now = pendulum.now("UTC")
    excluded_names_normalized = {name.strip().casefold() for name in excluded_names}
    for event in relevant_events:
        start = _to_utc_datetime(event.start_time)
        end = _to_utc_datetime(event.end_time)
        if start is None or end is None:
            continue
        event_name = (event.name or "").strip()
        if event_name.casefold() in excluded_names_normalized:
            continue
        if start <= now <= end and fragment.casefold() not in event_name.casefold():
            logger.info("Active non-fragment event found: '%s'", event.name)
            return True
    return False


def find_matching_discord_event(
    discord_events: Sequence[discord.ScheduledEvent],
    calendar_event: CalendarEvent | CancelledCalendarEvent,
) -> Optional[discord.ScheduledEvent]:
    base_uid_counts: Counter[str] = Counter()
    for existing in discord_events:
        marker = _extract_uid_marker(getattr(existing, "description", None))
        if not marker:
            continue
        base = marker.split("::", 1)[0]
        base_uid_counts[base] += 1

    for event in discord_events:
        if event.status == discord.EventStatus.completed:
            continue
        instance_uid = getattr(calendar_event, "instance_uid", calendar_event.uid)
        event_uid = _extract_uid_marker(event.description)
        start_time = _to_utc_datetime(event.start_time)
        end_time = _to_utc_datetime(event.end_time)
        if start_time is None or end_time is None:
            continue
        start_time = start_time.replace(second=0, microsecond=0)
        cal_start = calendar_event.start_time.replace(second=0, microsecond=0)
        if event_uid:
            event_uid_base = event_uid.split("::", 1)[0]
            if event_uid == instance_uid:
                return event
            if event_uid == calendar_event.uid and start_time == cal_start:
                return event
            if (
                "::" in event_uid
                and event_uid_base == calendar_event.uid
                and base_uid_counts.get(calendar_event.uid, 0) == 1
            ):
                return event
        event_name = event.name or ""
        if event_name != calendar_event.name:
            continue
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
    events: Optional[Sequence[discord.ScheduledEvent]] = None,
) -> List[discord.ScheduledEvent]:
    now = pendulum.now(timezone_name)
    end_time = now.add(minutes=duration_minutes)
    start_time_minimum = now.add(seconds=60)

    relevant_events = list(events) if events is not None else await fetch_relevant_events(guild)
    we_events = [
        event for event in relevant_events if we_are_fragment.lower() in (event.name or "").lower()
    ]

    primary_event = we_events[0] if we_events else None

    if primary_event:
        start_time = _to_utc_datetime(primary_event.start_time)
        end_existing = _to_utc_datetime(primary_event.end_time)
        if start_time and end_existing and start_time <= now <= end_existing:
            try:
                updated_event = await primary_event.edit(
                    name=status_text,
                    description=description,
                    end_time=end_time,
                )
                logger.info("Updated existing open-status event '%s'", primary_event.name)
                if updated_event is not primary_event:
                    relevant_events = [
                        updated_event if event.id == primary_event.id else event
                        for event in relevant_events
                    ]
                return relevant_events
            except discord.errors.Forbidden:
                logger.warning(
                    "Permission denied updating event '%s'; deleting", primary_event.name
                )
                try:
                    await primary_event.delete()
                    relevant_events = [
                        event for event in relevant_events if event.id != primary_event.id
                    ]
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Failed to delete event '%s': %s", primary_event.name, exc)
        else:
            try:
                await primary_event.delete()
                relevant_events = [
                    event for event in relevant_events if event.id != primary_event.id
                ]
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to delete stale event '%s': %s", primary_event.name, exc)

    try:
        # Ensure start time always sits slightly in the future to avoid Discord rejecting it
        future_now = pendulum.now(timezone_name)
        start_time = max(start_time_minimum, future_now.add(seconds=30))

        created_event = await guild.create_scheduled_event(
            name=status_text,
            description=description,
            start_time=start_time,
            end_time=end_time,
            entity_type=discord.EntityType.external,
            location="MAG Laboratory",
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        relevant_events.append(created_event)
        logger.info("Created new open-status event '%s'", status_text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to create open-status event: %s", exc)
    return relevant_events


async def prune_orphaned_events(
    guild: discord.Guild,
    calendar_keys: Iterable[Tuple[str, pendulum.DateTime, str]],
    *,
    timezone_name: str,
    allowed_uids: Optional[Iterable[str]] = None,
    allow_fragments: Optional[Iterable[str]] = None,
    events: Optional[Sequence[discord.ScheduledEvent]] = None,
) -> List[discord.ScheduledEvent]:
    key_counts = Counter(calendar_keys)
    uid_allowlist = set(allowed_uids or [])
    allow_fragments = {frag.lower() for frag in allow_fragments or []}
    now = pendulum.now("UTC")

    relevant_events = list(events) if events is not None else await fetch_relevant_events(guild)
    remaining_events: List[discord.ScheduledEvent] = []
    for event in relevant_events:
        start = _to_utc_datetime(event.start_time)
        end = _to_utc_datetime(event.end_time)
        if start is None or end is None:
            remaining_events.append(event)
            continue
        if start <= now <= end:
            remaining_events.append(event)
            continue
        name = event.name or ""
        if any(fragment in name.lower() for fragment in allow_fragments):
            remaining_events.append(event)
            continue
        event_uid = _extract_uid_marker(event.description)
        if event_uid and event_uid in uid_allowlist:
            remaining_events.append(event)
            continue
        location = (event.location or "MAG Laboratory").strip()
        key = (name, start.replace(second=0, microsecond=0), location)
        if key_counts[key] > 0:
            key_counts[key] -= 1
            remaining_events.append(event)
            continue
        if key_counts[key] == 0:
            try:
                await event.delete()
                logger.info("Deleted orphaned event '%s'", name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed to delete event '%s': %s", name, exc)
                remaining_events.append(event)
    return remaining_events
