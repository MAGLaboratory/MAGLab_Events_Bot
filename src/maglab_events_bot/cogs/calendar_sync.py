"""Discord cog that keeps scheduled events in sync with Google Calendars."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional, Sequence

import discord
import pendulum
from discord.ext import commands, tasks

from maglab_events_bot.config import get_settings
from maglab_events_bot.models.events import CalendarEvent, CancelledCalendarEvent
from maglab_events_bot.services.calendar import CalendarFetcher
from maglab_events_bot.services.discord_api import (
    SynopticImageCache,
    apply_uid_marker,
    enforce_single_synoptic_image,
    find_matching_discord_event,
    prune_orphaned_events,
)
from maglab_events_bot.tasks.synoptic import get_synoptic_image_bytes_async

logger = logging.getLogger(__name__)


class CalendarSyncCog(commands.Cog):
    """Synchronizes Discord scheduled events with Google Calendar feeds."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.timezone = pendulum.timezone(self.settings.timezone)
        self.interval_hours = self.settings.calendar_sync_interval_hours
        self.allow_fragments = ("We are",)
        self.fetcher = CalendarFetcher()
        if not hasattr(bot, "synoptic_cache"):
            bot.synoptic_cache = SynopticImageCache()  # type: ignore[attr-defined]
        self._synoptic_cache = bot.synoptic_cache  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        if not self.sync_calendar_events.is_running():
            self.sync_calendar_events.change_interval(hours=self.interval_hours)
            self.sync_calendar_events.start()
            logger.info("Calendar sync loop started")

    async def cog_unload(self) -> None:
        if self.sync_calendar_events.is_running():
            self.sync_calendar_events.cancel()
            logger.info("Calendar sync loop stopped")

    async def _get_guild(self) -> Optional[discord.Guild]:
        guild = self.bot.get_guild(self.settings.guild_id)
        if guild:
            return guild
        try:
            return await self.bot.fetch_guild(self.settings.guild_id)
        except discord.NotFound:
            logger.error("Guild with id %s not found", self.settings.guild_id)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to fetch guild %s: %s", self.settings.guild_id, exc)
        return None

    @tasks.loop(hours=1)
    async def sync_calendar_events(self) -> None:
        try:
            await self._sync_calendar_events_once()
        except discord.HTTPException as exc:
            logger.warning(
                "calendar.sync_discord_api_failed",
                extra={
                    "guild_id": self.settings.guild_id,
                    "status": getattr(exc, "status", None),
                    "code": getattr(exc, "code", None),
                    "error": str(exc),
                },
            )

    async def _sync_calendar_events_once(self) -> None:
        guild = await self._get_guild()
        if not guild:
            return

        events, cancellations = await self.fetcher.fetch_events(
            self.settings.get_ics_urls(),
            sync_horizon_days=self.settings.sync_days,
            timezone_name=self.settings.timezone,
        )
        existing_events = await guild.fetch_scheduled_events()

        await self._process_events(guild, existing_events, events)
        await self._process_cancellations(guild, existing_events, cancellations)

        allowed_uids = {event.uid for event in events} | {event.instance_uid for event in events}
        calendar_keys = self._build_calendar_keys(events)
        await prune_orphaned_events(
            guild,
            calendar_keys,
            allowed_uids=allowed_uids,
            timezone_name=self.settings.timezone,
            allow_fragments=self.allow_fragments,
        )

        image_bytes = await get_synoptic_image_bytes_async()
        await enforce_single_synoptic_image(
            guild,
            image_bytes,
            self.settings.timezone,
            cache=self._synoptic_cache,
        )

        logger.info(
            "calendar.sync_completed",
            extra={
                "guild_id": self.settings.guild_id,
                "events": len(events),
                "cancellations": len(cancellations),
            },
        )

    @sync_calendar_events.before_loop
    async def before_sync(self) -> None:
        await self.bot.wait_until_ready()

    @sync_calendar_events.after_loop
    async def after_sync(self) -> None:
        logger.info("Calendar sync loop ended")

    async def _process_events(
        self,
        guild: discord.Guild,
        discord_events: Sequence[discord.ScheduledEvent],
        calendar_events: Iterable[CalendarEvent],
    ) -> None:
        for calendar_event in calendar_events:
            start_time_display = calendar_event.start_time.in_timezone(
                self.timezone
            ).to_datetime_string()
            match = find_matching_discord_event(discord_events, calendar_event)
            desired_description = apply_uid_marker(
                calendar_event.description, calendar_event.instance_uid
            )
            if match:
                if self._event_matches_calendar(match, calendar_event, desired_description):
                    logger.debug(
                        "Skipping unchanged event '%s' scheduled at %s",
                        calendar_event.name,
                        start_time_display,
                    )
                    continue
                logger.info(
                    "Updating event '%s' scheduled at %s", calendar_event.name, start_time_display
                )
                try:
                    await match.edit(
                        name=calendar_event.name,
                        description=desired_description,
                        start_time=calendar_event.start_time,
                        end_time=calendar_event.end_time,
                        location=calendar_event.location,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Failed updating event '%s': %s", calendar_event.name, exc)
            else:
                logger.info(
                    "Creating event '%s' scheduled at %s", calendar_event.name, start_time_display
                )
                try:
                    await guild.create_scheduled_event(
                        name=calendar_event.name,
                        description=desired_description,
                        start_time=calendar_event.start_time,
                        end_time=calendar_event.end_time,
                        entity_type=discord.EntityType.external,
                        location=calendar_event.location,
                        privacy_level=discord.PrivacyLevel.guild_only,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Failed creating event '%s': %s", calendar_event.name, exc)

    async def _process_cancellations(
        self,
        guild: discord.Guild,
        discord_events: Sequence[discord.ScheduledEvent],
        cancellations: Iterable[CancelledCalendarEvent],
    ) -> None:
        for cancellation in cancellations:
            match = find_matching_discord_event(discord_events, cancellation)
            if not match:
                continue
            start_time_display = cancellation.start_time.in_timezone(
                self.timezone
            ).to_datetime_string()
            logger.info(
                "Removing canceled event '%s' scheduled at %s",
                cancellation.name,
                start_time_display,
            )
            try:
                await match.delete()
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Failed deleting event '%s': %s", cancellation.name, exc)

    @staticmethod
    def _build_calendar_keys(
        events: Iterable[CalendarEvent],
    ) -> Iterable[tuple[str, pendulum.DateTime, str]]:
        for event in events:
            start = event.start_time.replace(second=0, microsecond=0)
            yield (event.name, start, event.location)

    @staticmethod
    def _to_utc_minute(value: datetime | None) -> Optional[pendulum.DateTime]:
        if value is None:
            return None
        return pendulum.instance(value).in_timezone("UTC").replace(second=0, microsecond=0)

    @classmethod
    def _event_matches_calendar(
        cls,
        discord_event: discord.ScheduledEvent,
        calendar_event: CalendarEvent,
        desired_description: str,
    ) -> bool:
        start = cls._to_utc_minute(discord_event.start_time)
        end = cls._to_utc_minute(discord_event.end_time)
        return (
            (discord_event.name or "") == calendar_event.name
            and (discord_event.description or "") == desired_description
            and start == calendar_event.start_time.replace(second=0, microsecond=0)
            and end == calendar_event.end_time.replace(second=0, microsecond=0)
            and (discord_event.location or "MAG Laboratory").strip() == calendar_event.location
        )
