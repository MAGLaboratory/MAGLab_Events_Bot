from maglab_events_bot.models.events import HalSensorReading
from maglab_events_bot.utils.formatting import (
    clean_description,
    format_hal_sensor_table,
    truncate_description,
)


def test_format_hal_sensor_table_outputs_table() -> None:
    reading = HalSensorReading(name="Sensor A", status="OK", last_update_display="Just now")
    result = format_hal_sensor_table(
        status_text="We are OPEN",
        sensors=[reading],
        scraped_at_display="2024-09-01 10:00",
        source_url="https://example.com",
    )
    assert "**Lab Status:** We are OPEN" in result
    assert "Sensor A" in result
    assert "```" in result


def test_description_cleaning_strips_markup() -> None:
    dirty = "<b>Hello</b> &amp; welcome"
    cleaned = clean_description(dirty)
    assert cleaned == "Hello & welcome"


def test_truncate_description_limits_length(monkeypatch) -> None:
    text = "A" * 2000
    truncated = truncate_description(text, max_length=100)
    assert len(truncated) == 100
