from pathlib import Path

import pytest

from maglab_events_bot import cli


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    cli.get_settings.cache_clear()
    yield
    cli.get_settings.cache_clear()


def test_generate_synoptic_cli_success(monkeypatch, tmp_path, capsys):
    output_path = tmp_path / "syn.png"
    monkeypatch.setenv("DISCORD_TOKEN", "token")

    def fake_generate(_url: str, _svg_id: str, output, **_kwargs) -> Path:
        path = Path(output)
        path.write_bytes(b"")
        return path

    monkeypatch.setattr(cli, "generate_synoptic_image", fake_generate)

    exit_code = cli.main(["generate-synoptic", "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Synoptic image written to" in captured.out
    assert output_path.exists()
