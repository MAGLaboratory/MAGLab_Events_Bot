import asyncio

import pytest

import maglab_events_bot.cogs.open_status as open_status_module
import maglab_events_bot.config as config_module
from maglab_events_bot.cogs.open_status import OpenStatusCog
from maglab_events_bot.config import get_settings
from maglab_events_bot.services.discord_api import SynopticImageCache


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:  # pragma: no cover - simple setter
        self.closed = True


@pytest.fixture(autouse=True)
def _ensure_discord_token(monkeypatch):
    get_settings.cache_clear()
    model_config = dict(config_module.Settings.model_config)
    model_config["env_file"] = None
    monkeypatch.setattr(config_module.Settings, "model_config", model_config, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    yield
    get_settings.cache_clear()


class DummyBot:
    def __init__(self) -> None:
        self.synoptic_cache = SynopticImageCache()


class PollingBot(DummyBot):
    def __init__(self) -> None:
        super().__init__()
        self.guild = PollingGuild()

    def get_guild(self, _guild_id):
        return self.guild


class PollingGuild:
    def __init__(self) -> None:
        self.fetch_count = 0

    async def fetch_scheduled_events(self):
        self.fetch_count += 1
        return []


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
        return {}

    async def fake_hal(*_args, **_kwargs):
        return None

    async def fake_image(_samples, **_kwargs):
        return b"image"

    async def fake_active_event(*_args, **_kwargs):
        return False

    async def fake_delete(guild, fragment, **_kwargs):
        nonlocal deleted
        assert guild is bot.guild
        assert fragment == "We are"
        deleted = True

    async def fake_enforce(*_args, **_kwargs):
        return None

    monkeypatch.setattr(open_status_module, "fetch_grafana_sensor_samples", fake_grafana)
    monkeypatch.setattr(
        open_status_module,
        "get_grafana_open_status",
        lambda *_args: False,
    )
    monkeypatch.setattr(open_status_module, "fetch_hal_status", fake_hal)
    monkeypatch.setattr(
        open_status_module,
        "has_active_non_fragment_event",
        fake_active_event,
    )
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

    async def fake_image(_samples, **_kwargs):
        return b"image"

    async def fake_active_event(*_args, **_kwargs):
        return False

    async def fake_delete(*_args, **_kwargs):
        nonlocal deleted
        deleted = True

    async def fake_enforce(*_args, **_kwargs):
        return None

    monkeypatch.setattr(open_status_module, "fetch_grafana_sensor_samples", fake_grafana)
    monkeypatch.setattr(open_status_module, "fetch_hal_status", fake_hal)
    monkeypatch.setattr(
        open_status_module,
        "has_active_non_fragment_event",
        fake_active_event,
    )
    monkeypatch.setattr(open_status_module, "get_synoptic_image_bytes_async", fake_image)
    monkeypatch.setattr(open_status_module, "delete_events_by_name_fragment", fake_delete)
    monkeypatch.setattr(open_status_module, "enforce_single_synoptic_image", fake_enforce)

    asyncio.run(cog.poll_hal_status.coro(cog))

    assert not deleted


def test_active_calendar_event_overrides_closed_switch_for_banner(monkeypatch):
    bot = PollingBot()
    cog = OpenStatusCog(bot=bot)
    cog._http_client = DummySession()  # type: ignore[assignment]
    rendered_with_override = False

    async def fake_grafana(**_kwargs):
        return {}

    async def fake_active_event(_guild, *, fragment, excluded_names, events):
        assert fragment == "We are"
        assert excluded_names == open_status_module.REMOTE_ONLY_EVENT_NAMES
        assert events == []
        return True

    async def fake_image(_samples, *, space_is_open_override, serve_cached_on_failure):
        nonlocal rendered_with_override
        rendered_with_override = space_is_open_override is True
        assert serve_cached_on_failure is False
        return b"image"

    async def fake_delete(*_args, **_kwargs):
        return None

    async def fake_enforce(*_args, **_kwargs):
        return None

    monkeypatch.setattr(open_status_module, "fetch_grafana_sensor_samples", fake_grafana)
    monkeypatch.setattr(
        open_status_module,
        "get_grafana_open_status",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        open_status_module,
        "has_active_non_fragment_event",
        fake_active_event,
    )
    monkeypatch.setattr(open_status_module, "get_synoptic_image_bytes_async", fake_image)
    monkeypatch.setattr(open_status_module, "delete_events_by_name_fragment", fake_delete)
    monkeypatch.setattr(open_status_module, "enforce_single_synoptic_image", fake_enforce)

    asyncio.run(cog.poll_hal_status.coro(cog))

    assert rendered_with_override
    assert bot.guild.fetch_count == 1
