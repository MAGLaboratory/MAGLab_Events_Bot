"""Render MAGLab's synoptic view from live Grafana sensor samples."""

from __future__ import annotations

import logging
import math
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Tuple
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from maglab_events_bot.services.grafana import (
    GrafanaSample,
    coerce_grafana_bool,
    sample_is_fresh,
)

logger = logging.getLogger(__name__)

# Configure Cairo on Windows if needed
if platform.system() == "Windows":
    os.environ.setdefault(
        "PATH",
        os.environ.get("PATH", "") + r";C:\\Program Files\\UniConvertor-2.0rc5\\dlls",
    )

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "data/static/maglab_synoptic_template.svg"
DEFAULT_SIZE = (800, 320)
DISCORD_VIEW_BOX = "-40 72 1187.5 475"
TECH_BAD_AFTER_MINUTES = 15
MOTION_ACTIVE_MINUTES = 20
MAGLAB_TIMEZONE = ZoneInfo("America/Los_Angeles")
OPEN_COLOR = "#006400"
CLOSED_COLOR = "#c62828"
UNKNOWN_COLOR = "#555555"

SYNOPTIC_FIELDS = (
    "Open Switch",
    "Privacy_Switch",
    "Front Door",
    "Pod Bay Door",
    "Office Motion",
    "Shop Motion",
    "ConfRm Motion",
    "ElecRm Motion",
    "ShopB Motion",
    "Bay Temp",
    "Outdoor Temp",
    "ShopB Temp",
    "ConfRm Temp",
    "ElecRm Temp",
)

DOORS = {
    "Pod Bay Door": "Pod-Bay-Door",
    "Front Door": "Front-Door",
}
MOTION_SENSORS = {
    "Shop Motion": "Shop",
    "Office Motion": "Office",
    "ShopB Motion": "ShopB",
    "ElecRm Motion": "ElecRm",
    "ConfRm Motion": "ConfRm",
}
TEMPERATURE_SENSORS = {
    "Bay Temp": "Bay",
    "ShopB Temp": "ShopB",
    "ElecRm Temp": "ElecRm",
    "ConfRm Temp": "ConfRm",
    "Outdoor Temp": "Outdoor",
}


def _elements_by_id(root: ElementTree.Element) -> dict[str, ElementTree.Element]:
    return {
        element_id: element
        for element in root.iter()
        if (element_id := element.get("id")) is not None
    }


def _set_visibility(
    elements: Mapping[str, ElementTree.Element], element_id: str, show: bool
) -> None:
    elements[element_id].set("visibility", "visible" if show else "hidden")


def _set_style(
    elements: Mapping[str, ElementTree.Element],
    element_id: str,
    property_name: str,
    value: str,
) -> None:
    element = elements[element_id]
    declarations: dict[str, str] = {}
    for declaration in element.get("style", "").split(";"):
        if ":" not in declaration:
            continue
        name, current_value = declaration.split(":", 1)
        declarations[name.strip()] = current_value.strip()
    declarations[property_name] = value
    element.set("style", ";".join(f"{name}:{current}" for name, current in declarations.items()))


def _set_text(elements: Mapping[str, ElementTree.Element], element_id: str, value: str) -> None:
    element = elements[element_id]
    child = next(iter(element), None)
    if child is None:
        element.text = value
    else:
        child.text = value


def _is_tech_bad(
    samples: Mapping[str, GrafanaSample],
    now: datetime,
) -> bool:
    if not samples:
        return True
    latest = max(sample.sampled_at for sample in samples.values())
    age_seconds = (now - latest).total_seconds()
    return age_seconds > TECH_BAD_AFTER_MINUTES * 60


def _active_binary(
    samples: Mapping[str, GrafanaSample],
    field: str,
    max_age_minutes: int | None = None,
    now: datetime | None = None,
) -> bool:
    sample = samples.get(field)
    if sample is None:
        return False
    if max_age_minutes is not None and not sample_is_fresh(
        sample.sampled_at,
        max_age_minutes,
        now,
    ):
        return False
    return coerce_grafana_bool(sample.value) is True


def _temperature_text(sample: GrafanaSample | None) -> Tuple[str, str]:
    if sample is None or not isinstance(sample.value, (int, float)):
        return "XX°C", "XX°F"
    celsius = sample.value / 1000
    fahrenheit = (celsius * 9 / 5) + 32
    rounded_celsius = math.floor(celsius + 0.5)
    rounded_fahrenheit = math.floor(fahrenheit + 0.5)
    return f"{rounded_celsius}°C", f"{rounded_fahrenheit}°F"


def _space_state(
    samples: Mapping[str, GrafanaSample],
    tech_bad: bool,
    now: datetime,
) -> Tuple[str, str, str]:
    if tech_bad:
        return "#e0d5d5", UNKNOWN_COLOR, "Unknown"
    if _active_binary(samples, "Open Switch", MOTION_ACTIVE_MINUTES, now):
        return "#ffffff", OPEN_COLOR, "Open"
    return "#e0e0d5", CLOSED_COLOR, "Closed"


def _render_openness(
    elements: Mapping[str, ElementTree.Element],
    samples: Mapping[str, GrafanaSample],
    tech_bad: bool,
    now: datetime,
) -> None:
    floor, color, label = _space_state(samples, tech_bad, now)
    _set_style(elements, "Space-Floor", "fill", floor)
    _set_style(elements, "Space_Openness", "fill", color)
    _set_text(elements, "Space_Openness", label)


def _render_doors(
    elements: Mapping[str, ElementTree.Element],
    samples: Mapping[str, GrafanaSample],
    tech_bad: bool,
    privacy_enabled: bool,
) -> None:
    for field, prefix in DOORS.items():
        is_open = not privacy_enabled and _active_binary(samples, field)
        if prefix == "Pod-Bay-Door":
            open_ids = (f"{prefix}_Open-0", f"{prefix}_Open-1")
        else:
            open_ids = (f"{prefix}_Open",)
        for open_id in open_ids:
            _set_visibility(elements, open_id, not tech_bad and is_open)
            _set_style(elements, open_id, "stroke", OPEN_COLOR)
        _set_visibility(elements, f"{prefix}_Closed", not tech_bad and not is_open)
        _set_style(elements, f"{prefix}_Closed", "stroke", CLOSED_COLOR)
        _set_visibility(elements, f"{prefix}_Fail", tech_bad)


def _render_motion(
    elements: Mapping[str, ElementTree.Element],
    samples: Mapping[str, GrafanaSample],
    tech_bad: bool,
    privacy_enabled: bool,
    now: datetime,
) -> None:
    for field, prefix in MOTION_SENSORS.items():
        active = not privacy_enabled and _active_binary(
            samples,
            field,
            MOTION_ACTIVE_MINUTES,
            now,
        )
        _set_visibility(elements, f"{prefix}-Motion_Motion", not tech_bad and active)
        _set_visibility(elements, f"{prefix}-Motion_Fail", tech_bad)
        _set_style(
            elements,
            f"{prefix}-Motion_Enclosure",
            "stroke",
            "#ff0000" if tech_bad else "#000000",
        )


def _render_computers(
    elements: Mapping[str, ElementTree.Element],
    tech_bad: bool,
) -> None:
    for prefix in ("HAL", "Daisy"):
        _set_visibility(elements, f"{prefix}_Fail", tech_bad)
        _set_style(
            elements,
            f"{prefix}_Enclosure",
            "stroke",
            "#ff0000" if tech_bad else "#000000",
        )


def _render_temperatures(
    elements: Mapping[str, ElementTree.Element],
    samples: Mapping[str, GrafanaSample],
    tech_bad: bool,
) -> None:
    for field, prefix in TEMPERATURE_SENSORS.items():
        temperature_id = f"{prefix}-Temp_Temperature"
        enclosure_id = f"{prefix}-Temp_Enclosure"
        celsius_text, fahrenheit_text = (
            ("XX°C", "XX°F") if tech_bad else _temperature_text(samples.get(field))
        )
        _set_text(elements, temperature_id, celsius_text)
        temperature = elements[temperature_id]
        _set_style(elements, temperature_id, "font-size", "28px")
        text_child = next(iter(temperature), None)
        if text_child is not None:
            text_child.set("x", "23")
            text_child.set("y", "29")
        fahrenheit_line = ElementTree.SubElement(
            temperature,
            f"{{{SVG_NAMESPACE}}}tspan",
            {"x": "23", "y": "59"},
        )
        fahrenheit_line.text = fahrenheit_text

        # The upstream SVG uses a Unicode thermometer emoji, which CairoSVG's
        # server font renders as a missing-glyph square. Draw a portable vector
        # thermometer instead.
        _set_visibility(elements, f"{prefix}-Temp_Symbol", False)
        sensor_group = elements[f"{prefix}-Temp"]
        icon_color = "#ffffff" if prefix == "Outdoor" else "#000000"
        icon = ElementTree.SubElement(
            sensor_group,
            f"{{{SVG_NAMESPACE}}}g",
            {"id": f"{prefix}-Temp_Vector-Symbol"},
        )
        ElementTree.SubElement(
            icon,
            f"{{{SVG_NAMESPACE}}}path",
            {
                "d": "M 10,11 V 47",
                "fill": "none",
                "stroke": icon_color,
                "stroke-width": "3",
                "stroke-linecap": "round",
            },
        )
        ElementTree.SubElement(
            icon,
            f"{{{SVG_NAMESPACE}}}circle",
            {"cx": "10", "cy": "53", "r": "6", "fill": icon_color},
        )

        elements[enclosure_id].set("width", "96")
        elements[enclosure_id].set("height", "68")
        elements[f"{prefix}-Temp_Bk-Slash"].set("d", "m 0,0 96,68")
        elements[f"{prefix}-Temp_Fw-Slash"].set("d", "m 96,0 -96,68")
        _set_visibility(elements, f"{prefix}-Temp_Fail", tech_bad)
        healthy_color = "#ffffff" if prefix == "Outdoor" else "#000000"
        _set_style(
            elements,
            enclosure_id,
            "stroke",
            "#ff0000" if tech_bad else healthy_color,
        )

    # The upstream positions assume short Celsius-only labels. Move the wider
    # dual-unit enclosures into clear areas of their corresponding rooms.
    temperature_positions = {
        "Outdoor-Temp": "translate(160,80)",
        "Bay-Temp": "translate(300,225)",
        "ConfRm-Temp": "translate(821,210)",
        "ShopB-Temp": "translate(625,289)",
        "ElecRm-Temp": "translate(858,325)",
    }
    for element_id, transform in temperature_positions.items():
        elements[element_id].set("transform", transform)


def _door_state(
    samples: Mapping[str, GrafanaSample],
    field: str,
    tech_bad: bool,
    privacy_enabled: bool,
) -> Tuple[str, str]:
    if tech_bad:
        return "Unknown", UNKNOWN_COLOR
    if not privacy_enabled and _active_binary(samples, field):
        return "Open", OPEN_COLOR
    return "Closed", CLOSED_COLOR


def _last_updated_text(samples: Mapping[str, GrafanaSample]) -> Tuple[str, str]:
    if not samples:
        return "Updated: unavailable", ""
    latest = max(sample.sampled_at for sample in samples.values()).astimezone(MAGLAB_TIMEZONE)
    hour = latest.strftime("%I").lstrip("0") or "0"
    return (
        f"Updated {latest.month}/{latest.day}/{latest:%y}",
        f"{hour}:{latest:%M %p %Z}",
    )


def _append_safe_area_summary(
    root: ElementTree.Element,
    samples: Mapping[str, GrafanaSample],
    tech_bad: bool,
    privacy_enabled: bool,
    now: datetime,
) -> None:
    """Put essential state inside Discord's shallow list-card center crop."""

    floor_color, space_color, space_label = _space_state(samples, tech_bad, now)
    front_label, front_color = _door_state(samples, "Front Door", tech_bad, privacy_enabled)
    pod_bay_label, pod_bay_color = _door_state(samples, "Pod Bay Door", tech_bad, privacy_enabled)

    group = ElementTree.SubElement(
        root,
        f"{{{SVG_NAMESPACE}}}g",
        {"id": "Discord-Safe-Area-Summary"},
    )
    ElementTree.SubElement(
        group,
        f"{{{SVG_NAMESPACE}}}rect",
        {
            "x": "-25",
            "y": "185",
            "width": "275",
            "height": "245",
            "fill": floor_color,
            "stroke": "#000000",
            "stroke-width": "2",
        },
    )

    def add_text(x: str, y: str, value: str, size: str, color: str, weight: str = "normal") -> None:
        node = ElementTree.SubElement(
            group,
            f"{{{SVG_NAMESPACE}}}text",
            {
                "x": x,
                "y": y,
                "font-family": "DejaVu Sans, sans-serif",
                "font-size": size,
                "font-weight": weight,
                "fill": color,
            },
        )
        node.text = value

    add_text("-8", "215", "Space", "28", "#000000")
    add_text("-8", "250", space_label, "36", space_color)
    ElementTree.SubElement(
        group,
        f"{{{SVG_NAMESPACE}}}path",
        {"d": "M -10,262 H 235", "stroke": "#000000", "stroke-width": "1"},
    )
    add_text("-8", "285", "Pod Bay Door", "24", "#000000")
    add_text("-8", "312", pod_bay_label, "30", pod_bay_color)
    add_text("-8", "337", "Front Door", "24", "#000000")
    add_text("-8", "364", front_label, "30", front_color)
    ElementTree.SubElement(
        group,
        f"{{{SVG_NAMESPACE}}}path",
        {"d": "M -10,374 H 235", "stroke": "#000000", "stroke-width": "1"},
    )
    updated_date, updated_time = _last_updated_text(samples)
    add_text("-8", "397", updated_date, "24", "#000000")
    add_text("-8", "420", updated_time, "24", "#000000")


def render_synoptic_svg(
    samples: Mapping[str, GrafanaSample],
    *,
    now: datetime | None = None,
    template_path: Path = TEMPLATE_PATH,
) -> bytes:
    """Apply the website backend's conditions to the local SVG template."""

    root = ElementTree.parse(template_path).getroot()
    root.set("viewBox", DISCORD_VIEW_BOX)
    root.set("width", str(DEFAULT_SIZE[0]))
    root.set("height", str(DEFAULT_SIZE[1]))
    elements = _elements_by_id(root)

    current_time = now or datetime.now(timezone.utc)
    tech_bad = _is_tech_bad(samples, current_time)
    privacy_enabled = _active_binary(samples, "Privacy_Switch", now=current_time)

    _render_openness(elements, samples, tech_bad, current_time)
    _render_doors(elements, samples, tech_bad, privacy_enabled)
    _render_motion(elements, samples, tech_bad, privacy_enabled, current_time)
    _render_computers(elements, tech_bad)
    _render_temperatures(elements, samples, tech_bad)
    _append_safe_area_summary(root, samples, tech_bad, privacy_enabled, current_time)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def generate_synoptic_image(
    samples: Mapping[str, GrafanaSample],
    output_path: Path,
    target_size: Tuple[int, int] = DEFAULT_SIZE,
) -> Path | None:
    """Render a Discord-sized PNG from a Grafana snapshot."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Keep the optional native rendering stack out of bot startup so a
        # broken Cairo installation does not stop calendar/status polling.
        import cairosvg

        svg_content = render_synoptic_svg(samples)
        cairosvg.svg2png(
            bytestring=svg_content,
            write_to=str(output_path),
            output_width=target_size[0],
            output_height=target_size[1],
        )
        return output_path
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to render synoptic PNG: %s", exc)
        return None


def generate_synoptic_image_if_needed(
    samples: Mapping[str, GrafanaSample],
    output_path: Path,
    dependencies: Iterable[Path] | None = None,
) -> Path | None:
    if dependencies and not all(dependency.exists() for dependency in dependencies):
        logger.warning("Dependencies missing; skipping synoptic generation")
        return None
    if output_path.exists():
        return output_path
    return generate_synoptic_image(samples, output_path)
