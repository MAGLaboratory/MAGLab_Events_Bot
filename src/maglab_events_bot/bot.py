"""Primary Discord bot application wiring."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands
from pydantic import ValidationError

from maglab_events_bot.cogs.calendar_sync import CalendarSyncCog
from maglab_events_bot.cogs.open_status import OpenStatusCog
from maglab_events_bot.config import get_settings
from maglab_events_bot.logging import configure_logging

logger = logging.getLogger(__name__)


class MagLabBot(commands.Bot):
    def __init__(self, *args, **kwargs) -> None:
        intents = kwargs.pop("intents", discord.Intents.default())
        super().__init__(*args, intents=intents, **kwargs)
        self.settings = get_settings()

    async def setup_hook(self) -> None:
        await self.add_cog(OpenStatusCog(self))
        await self.add_cog(CalendarSyncCog(self))
        logger.info("Cogs loaded")


def build_bot(command_prefix: str = "!") -> MagLabBot:
    configure_logging()
    intents = discord.Intents.default()
    intents.guilds = True
    bot = MagLabBot(command_prefix=command_prefix, intents=intents)

    @bot.event
    async def on_ready() -> None:
        logger.info("Bot %s connected", bot.user)

    @bot.event
    async def on_disconnect() -> None:
        logger.warning("Bot disconnected; attempting to reconnect")

    @bot.event
    async def on_resumed() -> None:
        logger.info("Bot resumed connection")

    @bot.event
    async def on_error(event_method: str, *args, **kwargs) -> None:
        logger.exception("Error in event %s: args=%s kwargs=%s", event_method, args, kwargs)

    return bot


def main(token: Optional[str] = None) -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        logger.error("Invalid configuration: %s", exc)
        raise SystemExit(1) from exc

    bot = build_bot()
    bot.run(token or settings.discord_token)


if __name__ == "__main__":
    main()
