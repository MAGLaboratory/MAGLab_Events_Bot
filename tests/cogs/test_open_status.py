import asyncio

import pytest

import maglab_events_bot.cogs.open_status as open_status_module
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


class PollingBot(DummyBot):
    def __init__(self) -> None:
        super().__init__()
        self.guild = object()

    def get_guild(self, _guild_id):
        return self.guild


def test_cog_unload_closes_session(monkeypatch):
    cog = OpenStatusCog(bot=DummyBot())
    dummy_session = DummySession()
    cog._http_client = dummy_session  # type: ignore[attr-defined]

    asyncio.run(cog.cog_unload())

    assert dummy_session.closed


def test_closed_grafana_switch_deletes_open_event_without_hal_status(monkeypatch):
    bot = PollingBot()
    cog = OpenStatusCog(bot=bot)
    cog._http_client = DummySession()  # type: ignore[assignment]
    deleted = False

    async def fake_grafana(**_kwargs):
        return False

    async def fake_hal(*_args, **_kwargs):
        return None

    async def fake_image():
        return b"image"

    async def fake_delete(guild, fragment):
        nonlocal deleted
        assert guild is bot.guild
        assert fragment == "We are"
        deleted = True

    async def fake_enforce(*_args, **_kwargs):
        return None

    monkeypatch.setattr(open_status_module, "fetch_grafana_open_status", fake_grafana)
    monkeypatch.setattr(open_status_module, "fetch_hal_status", fake_hal)
    monkeypatch.setattr(open_status_module, "get_synoptic_image_bytes_async", fake_image)
    monkeypatch.setattr(open_status_module, "delete_events_by_name_fragment", fake_delete)
    monkeypatch.setattr(open_status_module, "enforce_single_synoptic_image", fake_enforce)

    asyncio.run(cog.poll_hal_status.coro(cog))

    assert deleted


def test_missing_grafana_switch_does_not_fall_back_to_hal(monkeypatch):
    bot = PollingBot()
    cog = OpenStatusCog(bot=bot)
    cog._http_client = DummySession()  # type: ignore[assignment]
    deleted = False

    async def fake_grafana(**_kwargs):
        return None

    async def fake_hal(*_args, **_kwargs):
        raise AssertionError("HAL status must not decide open/closed")

    async def fake_image():
        return b"image"

    async def fake_delete(*_args, **_kwargs):
        nonlocal deleted
        deleted = True

    async def fake_enforce(*_args, **_kwargs):
        return None

    monkeypatch.setattr(open_status_module, "fetch_grafana_open_status", fake_grafana)
    monkeypatch.setattr(open_status_module, "fetch_hal_status", fake_hal)
    monkeypatch.setattr(open_status_module, "get_synoptic_image_bytes_async", fake_image)
    monkeypatch.setattr(open_status_module, "delete_events_by_name_fragment", fake_delete)
    monkeypatch.setattr(open_status_module, "enforce_single_synoptic_image", fake_enforce)

    asyncio.run(cog.poll_hal_status.coro(cog))

    assert not deleted
