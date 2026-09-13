from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

from maglab_events_bot.services.grafana import GrafanaSample
from maglab_events_bot.services.synoptic import (
    SYNOPTIC_FIELDS,
    render_synoptic_svg,
    synoptic_image_state_key,
)


def _samples(now, **values):
    defaults = dict.fromkeys(SYNOPTIC_FIELDS, 0)
    defaults.update(
        {
            "Open Switch": 1,
            "Front Door": 1,
            "Office Motion": 1,
            "Bay Temp": 30125,
            "Outdoor Temp": 34812,
            "ShopB Temp": 34562,
            "ConfRm Temp": 28875,
            "ElecRm Temp": 33625,
        }
    )
    defaults.update(values)
    return {field: GrafanaSample(value=value, sampled_at=now) for field, value in defaults.items()}


def _by_id(svg):
    root = ElementTree.fromstring(svg)
    return root, {element.get("id"): element for element in root.iter() if element.get("id")}


def _text(element):
    return "".join(element.itertext()).strip()


def test_render_applies_open_door_motion_and_temperature_conditions():
    now = datetime.now(timezone.utc)

    root, elements = _by_id(render_synoptic_svg(_samples(now), now=now))

    assert root.get("width") == "800"
    assert root.get("height") == "320"
    assert root.get("viewBox") == "-40 72 1187.5 475"
    assert _text(elements["Space_Openness"]) == "Open"
    assert "fill:#ffffff" in elements["Space-Floor"].get("style", "")
    assert elements["Front-Door_Open"].get("visibility") == "visible"
    assert elements["Front-Door_Closed"].get("visibility") == "hidden"
    assert "stroke:#2ecc40" in elements["Front-Door_Open"].get("style", "")
    assert "stroke:#c62828" in elements["Front-Door_Closed"].get("style", "")
    assert elements["Office-Motion_Motion"].get("visibility") == "visible"
    assert _text(elements["Bay-Temp_Temperature"]) == "30°C"
    assert _text(elements["Outdoor-Temp_Temperature"]) == "35°C"
    assert elements["Bay-Temp_Symbol"].get("visibility") == "hidden"
    assert elements["Bay-Temp_Vector-Symbol"] is not None
    summary = _text(elements["Discord-Safe-Area-Summary"])
    assert summary.startswith("SpaceOpenPod Bay DoorClosedFront DoorOpenUpdated ")
    assert "/" in summary
    assert summary.endswith("PDT")


def test_render_privacy_masks_door_and_motion_activity():
    now = datetime.now(timezone.utc)

    _, elements = _by_id(render_synoptic_svg(_samples(now, Privacy_Switch=1), now=now))

    assert elements["Front-Door_Open"].get("visibility") == "hidden"
    assert elements["Front-Door_Closed"].get("visibility") == "visible"
    assert elements["Office-Motion_Motion"].get("visibility") == "hidden"


def test_calendar_override_forces_only_space_status_open():
    now = datetime.now(timezone.utc)

    _, elements = _by_id(
        render_synoptic_svg(
            _samples(now, **{"Open Switch": 0, "Front Door": 0}),
            now=now,
            space_is_open_override=True,
        )
    )

    assert _text(elements["Space_Openness"]) == "Open"
    assert "SpaceOpen" in _text(elements["Discord-Safe-Area-Summary"])
    assert elements["Front-Door_Closed"].get("visibility") == "visible"


def test_render_marks_entire_view_failed_when_latest_sample_is_stale():
    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(minutes=16)

    _, elements = _by_id(render_synoptic_svg(_samples(stale_time), now=now))

    assert _text(elements["Space_Openness"]) == "Unknown"
    assert elements["Front-Door_Fail"].get("visibility") == "visible"
    assert elements["Office-Motion_Fail"].get("visibility") == "visible"
    assert elements["HAL_Fail"].get("visibility") == "visible"
    assert _text(elements["Bay-Temp_Temperature"]) == "XX°C"
    assert "Unknown" in _text(elements["Discord-Safe-Area-Summary"])


def test_synoptic_state_key_ignores_sample_timestamp_but_tracks_visible_changes():
    now = datetime.now(timezone.utc)
    original = _samples(now)
    newer_same_values = _samples(now + timedelta(minutes=1))
    changed_temperature = _samples(now + timedelta(minutes=1), **{"Bay Temp": 31125})

    original_key = synoptic_image_state_key(original, now=now)

    assert synoptic_image_state_key(newer_same_values, now=now) == original_key
    assert synoptic_image_state_key(changed_temperature, now=now) != original_key
