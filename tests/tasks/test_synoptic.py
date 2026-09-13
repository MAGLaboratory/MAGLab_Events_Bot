import asyncio
from datetime import datetime, timezone
from pathlib import Path

from maglab_events_bot.services.grafana import GrafanaSample
from maglab_events_bot.tasks import synoptic


def _samples():
    return {
        "Open Switch": GrafanaSample(
            value=1,
            sampled_at=datetime.now(timezone.utc),
        )
    }


def test_render_writes_fresh_image(monkeypatch, tmp_path):
    target = tmp_path / "synoptic.png"
    target.write_bytes(b"old-bytes")

    def fake_generate(samples, output: Path) -> Path:
        assert samples["Open Switch"].value == 1
        output.write_bytes(b"new-bytes")
        return output

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    result = synoptic.get_synoptic_image_bytes(_samples(), target)

    assert result == b"new-bytes"


def test_render_serves_cached_when_regeneration_fails(monkeypatch, tmp_path):
    target = tmp_path / "synoptic.png"
    target.write_bytes(b"cached-bytes")
    monkeypatch.setattr(synoptic, "generate_synoptic_image", lambda *_args: None)

    result = synoptic.get_synoptic_image_bytes(_samples(), target)

    assert result == b"cached-bytes"


def test_render_missing_and_regeneration_fails(monkeypatch, tmp_path):
    target = tmp_path / "missing.png"
    monkeypatch.setattr(synoptic, "generate_synoptic_image", lambda *_args: None)

    result = synoptic.get_synoptic_image_bytes(_samples(), target)

    assert result is None


def test_async_render_returns_bytes(monkeypatch, tmp_path):
    target = tmp_path / "synoptic.png"

    def fake_generate(_samples, output: Path) -> Path:
        output.write_bytes(b"async-bytes")
        return output

    monkeypatch.setattr(synoptic, "generate_synoptic_image", fake_generate)

    result = asyncio.run(synoptic.get_synoptic_image_bytes_async(_samples(), target))

    assert result == b"async-bytes"
