import pytest
from pydantic import ValidationError

import maglab_events_bot.config as config_module
from maglab_events_bot.config import get_settings


@pytest.fixture(autouse=True)
def _config_fixture(monkeypatch):
    get_settings.cache_clear()
    model_config = dict(config_module.Settings.model_config)
    model_config["env_file"] = None
    monkeypatch.setattr(config_module.Settings, "model_config", model_config, raising=False)
    yield
    get_settings.cache_clear()


def test_get_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "dummy-token")
    settings = get_settings()
    assert settings.discord_token == "dummy-token"


def test_get_settings_supports_csv_ics(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "dummy-token")
    monkeypatch.setenv(
        "ICS_URLS",
        "https://example.com/one.ics, https://example.com/two.ics",
    )
    settings = get_settings()
    assert settings.get_ics_urls() == [
        "https://example.com/one.ics",
        "https://example.com/two.ics",
    ]


def test_missing_token_raises_validation_error(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        get_settings()
