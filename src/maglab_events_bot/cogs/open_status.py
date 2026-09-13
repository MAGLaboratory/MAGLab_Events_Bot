"""Discord cog that manages the open-status scheduled event."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp
import discord
import pendulum
from discord.ext import commands, tasks

from maglab_events_bot.config import get_settings
from maglab_events_bot.services.discord_api import (
    SynopticImageCache,
    delete_events_by_name_fragment,
    enforce_single_synoptic_image,
    ensure_open_status_event,
    fetch_relevant_events,
    has_active_non_fragment_event,
)
from maglab_events_bot.services.grafana import (
    fetch_grafana_sensor_samples,
    get_grafana_open_status,
)
from maglab_events_bot.services.hal import fetch_hal_status
from maglab_events_bot.services.status_channel import StatusChannelReconciler
from maglab_events_bot.services.synoptic import SYNOPTIC_FIELDS, synoptic_image_state_key
from maglab_events_bot.tasks.synoptic import get_synoptic_image_bytes_async
from maglab_events_bot.utils.formatting import format_hal_sensor_table
from maglab_events_bot.utils.http import build_aiohttp_client

logger = logging.getLogger(__name__)
REMOTE_ONLY_EVENT_NAMES = frozenset({"Public Business Meeting"})


class OpenStatusCog(commands.Cog):
    """Keeps the "We are OPEN" event up-to-date from Grafana's live switch."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.timezone = pendulum.timezone(self.settings.timezone)
        self.interval_minutes = self.settings.open_status_interval_minutes
        self.we_are_fragment = "We are"
        self._http_client: Optional[aiohttp.ClientSession] = None
        if not hasattr(bot, "synoptic_cache"):
            bot.synoptic_cache = SynopticImageCache()  # type: ignore[attr-defined]
        self._synoptic_cache = bot.synoptic_cache  # type: ignore[attr-defined]
        if not hasattr(bot, "reconciliation_lock"):
            bot.reconciliation_lock = asyncio.Lock()  # type: ignore[attr-defined]
        self._reconciliation_lock = bot.reconciliation_lock  # type: ignore[attr-defined]
        self._status_channel = StatusChannelReconciler(
            bot,
            channel_id=self.settings.status_channel_id,
            message_id=self.settings.status_message_id,
            rename_channel=self.settings.status_channel_rename,
            hal_url=str(self.settings.hal_url),
        )

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

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.channel_id == self.settings.status_channel_id:
            self._status_channel.invalidate_message(payload.message_id)

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
        try:
            async with self._reconciliation_lock:
                await self._poll_hal_status_once()
        except discord.HTTPException as exc:
            logger.warning(
                "hal.poll_discord_api_failed",
                extra={
                    "guild_id": self.settings.guild_id,
                    "status": getattr(exc, "status", None),
                    "code": getattr(exc, "code", None),
                    "error": str(exc),
                },
            )

    async def _poll_hal_status_once(self) -> None:
        guild = await self._get_guild()
        if not guild:
            return

        discord_events = await fetch_relevant_events(guild)

        if self._http_client is None:
            self._http_client = await build_aiohttp_client(
                verify_ssl=self.settings.grafana_verify_ssl
            )

        grafana_samples = await fetch_grafana_sensor_samples(
            base_url=str(self.settings.grafana_base_url),
            datasource_id=self.settings.grafana_datasource_id,
            database=self.settings.grafana_database,
            measurement=self.settings.grafana_measurement,
            fields=SYNOPTIC_FIELDS,
            username=self.settings.grafana_username,
            password=self.settings.grafana_password,
            verify_tls=self.settings.grafana_verify_ssl,
            session=self._http_client,
        )
        grafana_is_open = get_grafana_open_status(
            grafana_samples,
            self.settings.grafana_open_switch_field,
            self.settings.grafana_max_sample_age_minutes,
        )
        calendar_forces_space_open = await has_active_non_fragment_event(
            guild,
            fragment=self.we_are_fragment,
            excluded_names=REMOTE_ONLY_EVENT_NAMES,
            events=discord_events,
        )
        image_bytes = await get_synoptic_image_bytes_async(
            grafana_samples or {},
            space_is_open_override=True if calendar_forces_space_open else None,
            serve_cached_on_failure=False,
        )
        image_state_key = synoptic_image_state_key(
            grafana_samples or {},
            space_is_open_override=True if calendar_forces_space_open else None,
        )
        await self._status_channel.reconcile(
            guild,
            grafana_samples or {},
            image_bytes,
            image_state_key,
            space_is_open_override=True if calendar_forces_space_open else None,
        )

        if grafana_is_open is None:
            logger.warning(
                "grafana.open_switch_unavailable",
                extra={"guild_id": self.settings.guild_id},
            )
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
                events=discord_events,
                image_state_key=image_state_key,
            )
            return

        if not grafana_is_open:
            discord_events = await delete_events_by_name_fragment(
                guild,
                self.we_are_fragment,
                events=discord_events,
            )
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
                events=discord_events,
                image_state_key=image_state_key,
            )
            logger.info(
                "grafana.status_closed",
                extra={"guild_id": self.settings.guild_id},
            )
            return

        hal_status = await fetch_hal_status(
            str(self.settings.hal_url),
            self.timezone,
            session=self._http_client,
        )
        if hal_status is None:
            logger.warning(
                "hal.sensor_data_unavailable",
                extra={"guild_id": self.settings.guild_id},
            )
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
                events=discord_events,
                image_state_key=image_state_key,
            )
            return

        hal_status.status_text = "We are OPEN"

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
                events=discord_events,
                image_state_key=image_state_key,
            )
            return

        if calendar_forces_space_open or await has_active_non_fragment_event(
            guild,
            fragment=self.we_are_fragment,
            events=discord_events,
        ):
            discord_events = await delete_events_by_name_fragment(
                guild,
                self.we_are_fragment,
                events=discord_events,
            )
            await enforce_single_synoptic_image(
                guild,
                image_bytes,
                self.settings.timezone,
                cache=self._synoptic_cache,
                events=discord_events,
                image_state_key=image_state_key,
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
        discord_events = await ensure_open_status_event(
            guild,
            status_text=hal_status.status_text,
            description=formatted_message,
            timezone_name=self.settings.timezone,
            events=discord_events,
        )
        await enforce_single_synoptic_image(
            guild,
            image_bytes,
            self.settings.timezone,
            cache=self._synoptic_cache,
            events=discord_events,
            image_state_key=image_state_key,
        )
        logger.info(
            "grafana.status_open",
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
