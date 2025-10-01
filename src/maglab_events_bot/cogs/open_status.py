"""Discord cog that manages the open-status scheduled event."""
from __future__ import annotations

import logging
from typing import Optional

import discord
import pendulum
from discord.ext import commands, tasks

from maglab_events_bot.config import get_settings
from maglab_events_bot.services.discord_api import (
    delete_events_by_name_fragment,
    enforce_single_synoptic_image,
    ensure_open_status_event,
    has_active_non_fragment_event,
)
from maglab_events_bot.services.hal import fetch_hal_status
from maglab_events_bot.tasks.synoptic import get_synoptic_image_bytes
from maglab_events_bot.utils.formatting import format_hal_sensor_table
from maglab_events_bot.utils.http import build_session

logger = logging.getLogger(__name__)


class OpenStatusCog(commands.Cog):
    """Keeps the "We are OPEN" event up-to-date based on HAL status."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.timezone = pendulum.timezone(self.settings.timezone)
        self.interval_minutes = self.settings.open_status_interval_minutes
        self.we_are_fragment = "We are"
        self._session = build_session()

    async def cog_load(self) -> None:
        if not self.poll_hal_status.is_running():
            self.poll_hal_status.change_interval(minutes=self.interval_minutes)
            self.poll_hal_status.start()
            logger.info("Open status poll loop started")

    async def cog_unload(self) -> None:
        if self.poll_hal_status.is_running():
            self.poll_hal_status.cancel()
            logger.info("Open status poll loop stopped")
        self._session.close()

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

    @tasks.loop(minutes=1)
    async def poll_hal_status(self) -> None:
        guild = await self._get_guild()
        if not guild:
            return

        hal_status = await fetch_hal_status(
            str(self.settings.hal_url), self.timezone, session=self._session
        )
        image_bytes = get_synoptic_image_bytes()

        if hal_status is None:
            logger.warning(
                "hal.status_unavailable",
                extra={"guild_id": self.settings.guild_id},
            )
            await enforce_single_synoptic_image(guild, image_bytes, self.settings.timezone)
            return

        if not hal_status.is_open:
            await delete_events_by_name_fragment(guild, self.we_are_fragment)
            await enforce_single_synoptic_image(guild, image_bytes, self.settings.timezone)
            logger.info(
                "hal.status_closed",
                extra={"guild_id": self.settings.guild_id},
            )
            return

        if not hal_status.sensors:
            logger.warning(
                "hal.sensor_data_missing",
                extra={"guild_id": self.settings.guild_id},
            )
            await enforce_single_synoptic_image(guild, image_bytes, self.settings.timezone)
            return

        if await has_active_non_fragment_event(guild, fragment=self.we_are_fragment):
            await delete_events_by_name_fragment(guild, self.we_are_fragment)
            await enforce_single_synoptic_image(guild, image_bytes, self.settings.timezone)
            logger.info(
                "hal.skipped_due_to_active_event",
                extra={"guild_id": self.settings.guild_id},
            )
            return

        scraped_display = hal_status.scraped_at.to_datetime_string()
        formatted_message = format_hal_sensor_table(
            hal_status.status_text,
            hal_status.sensors,
            scraped_display,
            str(self.settings.hal_url),
        )
        await ensure_open_status_event(
            guild,
            status_text=hal_status.status_text,
            description=formatted_message,
            timezone_name=self.settings.timezone,
        )
        await enforce_single_synoptic_image(guild, image_bytes, self.settings.timezone)
        logger.info(
            "hal.status_open",
            extra={
                "guild_id": self.settings.guild_id,
                "sensor_count": len(hal_status.sensors),
            },
        )

    @poll_hal_status.before_loop
    async def before_poll_hal_status(self) -> None:
        await self.bot.wait_until_ready()

    @poll_hal_status.after_loop
    async def after_poll_hal_status(self) -> None:
        logger.info("Open status poll loop ended")
