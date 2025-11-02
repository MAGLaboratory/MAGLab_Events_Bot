"""Application settings management."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Union

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration loaded from environment variables."""

    discord_token: str = Field(..., alias="DISCORD_TOKEN")
    guild_id: int = Field(697971426799517774, alias="GUILD_ID")

    hal_url: AnyHttpUrl = Field(
        "https://www.maglaboratory.org/hal",
        alias="HAL_STATUS_URL",
    )
    open_status_interval_minutes: int = Field(5, ge=1, alias="OPEN_STATUS_INTERVAL_MINUTES")

    ics_urls: Union[List[AnyHttpUrl], str] = Field(
        default_factory=lambda: [
            "https://calendar.google.com/calendar/ical/c_3keov3j3lc5qscq754mb4n38b4%40group.calendar.google.com/public/basic.ics",
            "https://calendar.google.com/calendar/ical/bjpkvaeg1rjq9u3c6utecq1jos%40group.calendar.google.com/public/basic.ics",
        ],
        alias="ICS_URLS",
    )
    sync_days: int = Field(7, ge=1, alias="SYNC_DAYS")
    calendar_sync_interval_hours: int = Field(1, ge=1, alias="CALENDAR_SYNC_INTERVAL_HOURS")

    timezone: str = Field("America/Los_Angeles", alias="TIMEZONE")

    synoptic_output_path: Path = Field(
        Path(__file__).resolve().parent / "data/static/maglab_synoptic_view_scaled.png",
        alias="SYNOPTIC_OUTPUT_PATH",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    def get_ics_urls(self) -> List[str]:
        if isinstance(self.ics_urls, str):
            return [s.strip() for s in self.ics_urls.split(",") if s.strip()]
        return [str(url) for url in self.ics_urls]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
