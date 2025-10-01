import pytest

from maglab_events_bot.cogs.open_status import OpenStatusCog


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:  # pragma: no cover - simple setter
        self.closed = True


@pytest.fixture(autouse=True)
def _ensure_discord_token(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    yield


@pytest.mark.asyncio
async def test_cog_unload_closes_session(monkeypatch):
    cog = OpenStatusCog(bot=object())
    dummy_session = DummySession()
    cog._session = dummy_session  # type: ignore[attr-defined]

    await cog.cog_unload()

    assert dummy_session.closed
