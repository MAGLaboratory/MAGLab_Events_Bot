import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from maglab_events_bot.config import Settings
from maglab_events_bot.tasks import synoptic


def _make_settings(**overrides) -> Settings:
    base = {"discord_token": "token"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _mark_mtime(path: Path, dt: datetime) -> None:
    timestamp = dt.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_load_uses_cached_when_fresh(monkeypatch, tmp_path):
    settings = _make_settings()
    target = tmp_path / "synoptic.png"
    target.write_bytes(b"fresh-bytes")
    _mark_mtime(target, datetime.now(timezone.utc))

    def fake_generate(*_args, **_kwargs):
        pytest.fail("generate_synoptic_image should not be called for fresh cache")

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    result = synoptic._load_synoptic_image_bytes(settings, target)

    assert result == b"fresh-bytes"


def test_load_regenerates_when_stale(monkeypatch, tmp_path):
    settings = _make_settings(synoptic_max_age_minutes=1)
    target = tmp_path / "synoptic.png"
    target.write_bytes(b"old-bytes")
    _mark_mtime(target, datetime.now(timezone.utc) - timedelta(minutes=5))

    def fake_generate(_url: str, _svg_id: str, output: Path, **_kwargs) -> Path:
        output.write_bytes(b"new-bytes")
        return output

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    result = synoptic._load_synoptic_image_bytes(settings, target)

    assert result == b"new-bytes"


def test_load_serves_cached_when_regeneration_fails(monkeypatch, tmp_path):
    settings = _make_settings(synoptic_max_age_minutes=1)
    target = tmp_path / "synoptic.png"
    target.write_bytes(b"stale-but-present")
    _mark_mtime(target, datetime.now(timezone.utc) - timedelta(minutes=10))

    def fake_generate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    result = synoptic._load_synoptic_image_bytes(settings, target)

    assert result == b"stale-but-present"


def test_load_missing_and_regeneration_fails(monkeypatch, tmp_path):
    settings = _make_settings()
    target = tmp_path / "missing.png"

    def fake_generate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    result = synoptic._load_synoptic_image_bytes(settings, target)

    assert result is None


def test_regeneration_uses_session(monkeypatch, tmp_path):
    settings = _make_settings(synoptic_max_age_minutes=1)
    target = tmp_path / "synoptic.png"
    target.write_bytes(b"needs-refresh")
    _mark_mtime(target, datetime.now(timezone.utc) - timedelta(minutes=30))

    captured = {}

    def fake_generate(_url: str, _svg_id: str, output: Path, **kwargs) -> Path:
        captured["session"] = kwargs.get("session")
        output.write_bytes(b"new-bytes")
        return output

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    synoptic._load_synoptic_image_bytes(settings, target)

    assert isinstance(captured["session"], requests.Session)
