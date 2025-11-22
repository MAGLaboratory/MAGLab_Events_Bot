"""Discord cog that manages the open-status scheduled event."""

from __future__ import annotations

import logging
from typing import Optional

import discord
import pendulum
from discord.ext import commands, tasks

from maglab_events_bot.config import get_settings
from maglab_events_bot.services.discord_api import (
    SynopticImageCache,
    delete_events_by_name_fragment,
    enforce_single_synoptic_image,
    ensure_open_status_event,
    has_active_non_fragment_event,
)
from maglab_events_bot.services.grafana import fetch_grafana_open_status
from maglab_events_bot.services.hal import fetch_hal_status
from maglab_events_bot.tasks.synoptic import get_synoptic_image_bytes_async
from maglab_events_bot.utils.formatting import format_hal_sensor_table
from maglab_events_bot.utils.http import build_aiohttp_client

logger = logging.getLogger(__name__)


class OpenStatusCog(commands.Cog):
    """Keeps the "We are OPEN" event up-to-date based on HAL status."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.timezone = pendulum.timezone(self.settings.timezone)
        self.interval_minutes = self.settings.open_status_interval_minutes
        self.we_are_fragment = "We are"
        self._http_client = None
        if not hasattr(bot, "synoptic_cache"):
            bot.synoptic_cache = SynopticImageCache()  # type: ignore[attr-defined]
        self._synoptic_cache = bot.synoptic_cache  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        if self._http_client is None:
            self._http_client = await build_aiohttp_client(
                verify_ssl=self.settings.grafana_verify_ssl
            )
        if not self.poll_hal_status.is_running():
            self.poll_hal_status.change_interval(minutes=self.interval_minutes)
            self.poll_hal_status.start()
            logger.info("Open status poll loop started")

    async def cog_unload(self) -> None:
        if self.poll_hal_status.is_running():
            self.poll_hal_status.cancel()
            logger.info("Open status poll loop stopped")
        if self._http_client:
            await self._http_client.close()

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

        if self._http_client is None:
            self._http_client = await build_aiohttp_client(
                verify_ssl=self.settings.grafana_verify_ssl
            )

        hal_status = await fetch_hal_status(str(self.settings.hal_url), self.timezone, session=self._http_client)
        image_bytes = await get_synoptic_image_bytes_async()

        grafana_is_open = await fetch_grafana_open_status(
            base_url=str(self.settings.grafana_base_url),
            alert_name=self.settings.grafana_alert_name,
            alerts_endpoint=self.settings.grafana_alerts_endpoint,
            username=self.settings.grafana_username,
            password=self.settings.grafana_password,
            verify_tls=self.settings.grafana_verify_ssl,
            session=self._http_client,
        )

        if hal_status is None:
            logger.warning(
                "hal.status_unavailable",
                extra={"guild_id": self.settings.guild_id},
            )
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
            )
            return

        if grafana_is_open is not None:
            hal_status.status_text = "We are OPEN" if grafana_is_open else "We are CLOSED"

        if not hal_status.is_open:
            await delete_events_by_name_fragment(guild, self.we_are_fragment)
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
            )
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
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
            )
            return

        if await has_active_non_fragment_event(guild, fragment=self.we_are_fragment):
            await delete_events_by_name_fragment(guild, self.we_are_fragment)
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
            )
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
        await enforce_single_synoptic_image(
            guild,
            image_bytes,
            self.settings.timezone,
            cache=self._synoptic_cache,
        )
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
