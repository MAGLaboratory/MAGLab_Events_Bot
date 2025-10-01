import pytest
from pydantic import ValidationError

from maglab_events_bot.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
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
