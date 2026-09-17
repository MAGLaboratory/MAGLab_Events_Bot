"""Maintain a single live MAGLab status message in a dedicated Discord channel."""

from __future__ import annotations

import io
import logging
from typing import Any, Mapping, Optional

import discord
from discord.ext import commands

from maglab_events_bot.services.grafana import GrafanaSample
from maglab_events_bot.services.synoptic import (
    SynopticStatusSummary,
    get_synoptic_status_summary,
)

logger = logging.getLogger(__name__)

STATUS_MESSAGE_MARKER = "MAGLab live status"
STATUS_IMAGE_FILENAME = "maglab-status.png"
STATUS_CHANNEL_NAMES = {
    "Open": "🟢・space-open",
    "ClosedActive": "🟡・space-closed-but-active",
    "ClosedInactive": "🔴・space-closed-and-inactive",
    "Unknown": "⚪・space-status-unknown",
}
STATUS_COLORS = {
    "Open": 0x2ECC40,
    "Closed": 0xC62828,
    "Unknown": 0x555555,
}


class StatusChannelReconciler:
    """Own the bot-authored dashboard message and optional channel name."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        channel_id: int | None,
        message_id: int | None,
        rename_channel: bool,
        hal_url: str,
    ) -> None:
        self.bot = bot
        self.channel_id = channel_id
        self.message_id = message_id
        self.rename_channel = rename_channel
        self.hal_url = hal_url
        self._last_state_key: Optional[str] = None
        self._message: Optional[discord.Message] = None

    @property
    def enabled(self) -> bool:
        return self.channel_id is not None

    async def reconcile(
        self,
        guild: discord.Guild,
        samples: Mapping[str, GrafanaSample],
        image_bytes: bytes | None,
        image_state_key: str,
        *,
        space_is_open_override: bool | None = None,
    ) -> None:
        if self.channel_id is None:
            return

        try:
            channel: Any = guild.get_channel(self.channel_id)
            if channel is None:
                channel = await guild.fetch_channel(self.channel_id)
            if not all(hasattr(channel, attribute) for attribute in ("send", "edit", "pins")):
                logger.error(
                    "status_channel.unsupported_channel",
                    extra={"channel_id": self.channel_id},
                )
                return

            summary = get_synoptic_status_summary(
                samples,
                space_is_open_override=space_is_open_override,
            )
            await self._rename_if_needed(channel, summary)

            message = await self._find_message(channel)
            state_key = f"{image_state_key}:{summary.space}:{summary.last_motion}"
            if message is not None and self._last_state_key == state_key:
                return

            embed = self._build_embed(summary, image_bytes)
            file = self._image_file(image_bytes)
            if message is None:
                message = await self._create_message(channel, embed, file)
            else:
                edit_kwargs: dict[str, Any] = {
                    "embed": embed,
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                if file is not None:
                    edit_kwargs["attachments"] = [file]
                try:
                    message = await message.edit(**edit_kwargs)
                except discord.NotFound:
                    self.invalidate_message(message.id)
                    message = await self._create_message(
                        channel,
                        embed,
                        self._image_file(image_bytes),
                    )

            self._message = message
            self.message_id = message.id
            self._last_state_key = state_key
            logger.info(
                "status_channel.reconciled",
                extra={"channel_id": self.channel_id, "message_id": message.id},
            )
        except discord.Forbidden:
            logger.warning(
                "status_channel.permission_denied",
                extra={"channel_id": self.channel_id},
            )
        except discord.HTTPException as exc:
            logger.warning(
                "status_channel.discord_api_failed",
                extra={
                    "channel_id": self.channel_id,
                    "status": getattr(exc, "status", None),
                    "error": str(exc),
                },
            )

    async def _rename_if_needed(
        self,
        channel: Any,
        summary: SynopticStatusSummary,
    ) -> None:
        if not self.rename_channel:
            return
        if summary.space == "Open":
            name_key = "Open"
        elif summary.space == "Closed" and summary.motion_active is True:
            name_key = "ClosedActive"
        elif summary.space == "Closed" and summary.motion_active is False:
            name_key = "ClosedInactive"
        else:
            name_key = "Unknown"
        desired_name = STATUS_CHANNEL_NAMES[name_key]
        if getattr(channel, "name", None) != desired_name:
            await channel.edit(name=desired_name, reason="MAGLab live space status changed")

    async def _find_message(self, channel: Any) -> Optional[discord.Message]:
        if self._message is not None:
            return self._message

        if self.message_id is not None:
            try:
                message = await channel.fetch_message(self.message_id)
                if self._is_owned_dashboard(message):
                    return message
            except discord.NotFound:
                logger.info(
                    "status_channel.configured_message_missing",
                    extra={"channel_id": self.channel_id, "message_id": self.message_id},
                )

        pinned_messages = await channel.pins()
        matches = [message for message in pinned_messages if self._is_owned_dashboard(message)]
        if matches:
            matches.sort(key=lambda message: message.id, reverse=True)
            for duplicate in matches[1:]:
                try:
                    await duplicate.delete()
                except discord.HTTPException as exc:
                    logger.warning(
                        "status_channel.duplicate_delete_failed",
                        extra={"message_id": duplicate.id, "error": str(exc)},
                    )
            return matches[0]
        return None

    async def _create_message(
        self,
        channel: Any,
        embed: discord.Embed,
        file: Optional[discord.File],
    ) -> discord.Message:
        send_kwargs: dict[str, Any] = {
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions.none(),
            "silent": True,
        }
        if file is not None:
            send_kwargs["file"] = file
        message = await channel.send(**send_kwargs)
        self._message = message
        self.message_id = message.id
        await self._pin_message(message)
        return message

    def invalidate_message(self, message_id: int) -> None:
        """Forget a dashboard removed through Discord so the next poll recreates it."""

        if self.message_id == message_id:
            self._message = None
            self.message_id = None
            self._last_state_key = None

    def _is_owned_dashboard(self, message: discord.Message) -> bool:
        bot_user = self.bot.user
        if bot_user is None or message.author.id != bot_user.id:
            return False
        return any(
            (embed.description or "").startswith(STATUS_MESSAGE_MARKER)
            or (embed.footer.text or "").startswith(STATUS_MESSAGE_MARKER)
            for embed in message.embeds
        )

    def _build_embed(
        self,
        summary: SynopticStatusSummary,
        image_bytes: bytes | None,
    ) -> discord.Embed:
        status = summary.space.upper()
        updated_time = self._without_timezone(summary.updated_time)
        last_motion = self._without_timezone(summary.last_motion)
        description_lines = [
            f"{STATUS_MESSAGE_MARKER} • {summary.updated_date} {updated_time}".rstrip(),
            "",
            f"Pod Bay Door: **{summary.pod_bay_door}**",
            f"Front Door: **{summary.front_door}**",
            f"Last Motion: **{last_motion}**",
        ]
        embed = discord.Embed(
            title=f"MAGLab is {status}",
            url=self.hal_url,
            description="\n".join(description_lines),
            color=STATUS_COLORS.get(summary.space, STATUS_COLORS["Unknown"]),
        )
        if image_bytes is not None:
            embed.set_image(url=f"attachment://{STATUS_IMAGE_FILENAME}")
        return embed

    @staticmethod
    def _without_timezone(value: str) -> str:
        parts = value.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isalpha() and parts[1].isupper():
            return parts[0]
        return value

    @staticmethod
    def _image_file(image_bytes: bytes | None) -> Optional[discord.File]:
        if image_bytes is None:
            return None
        return discord.File(io.BytesIO(image_bytes), filename=STATUS_IMAGE_FILENAME)

    @staticmethod
    async def _pin_message(message: discord.Message) -> None:
        if not message.pinned:
            try:
                await message.pin(reason="MAGLab live status dashboard")
            except discord.HTTPException as exc:
                logger.warning(
                    "status_channel.pin_failed",
                    extra={"message_id": message.id, "error": str(exc)},
                )
