import asyncio

import pytest

from maglab_events_bot.cogs.open_status import OpenStatusCog
from maglab_events_bot.services.discord_api import SynopticImageCache


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:  # pragma: no cover - simple setter
        self.closed = True


@pytest.fixture(autouse=True)
def _ensure_discord_token(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    yield


class DummyBot:
    def __init__(self) -> None:
        self.synoptic_cache = SynopticImageCache()


def test_cog_unload_closes_session(monkeypatch):
    cog = OpenStatusCog(bot=DummyBot())
    dummy_session = DummySession()
    cog._http_client = dummy_session  # type: ignore[attr-defined]

    asyncio.run(cog.cog_unload())

    assert dummy_session.closed
