"""Guarded Google Business Profile hours publisher."""

from __future__ import annotations

from datetime import date
from typing import Any

import requests

from maglab_events_bot.services.business_hours import BusinessHoursPlan

_API_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1/"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _read_date(value: dict[str, Any]) -> date:
    return date(int(value["year"]), int(value["month"]), int(value["day"]))


def merge_special_periods(
    existing: list[dict], planned: list[dict], *, first_date: date, last_date: date
) -> list[dict]:
    """Replace only dates managed by the calendar window."""
    retained = []
    for period in existing:
        start = _read_date(period["startDate"])
        end = _read_date(period.get("endDate", period["startDate"]))
        if start < first_date <= end:
            raise ValueError("An existing special-hours period crosses the managed window")
        if not (first_date <= start <= last_date):
            retained.append(period)
    return retained + planned


def publish_business_hours(
    plan: BusinessHoursPlan,
    *,
    first_date: date,
    last_date: date,
    location: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    session: requests.Session | None = None,
) -> None:
    """Validate then update only hours fields; never change a listing without regular hours."""
    location_id = location.removeprefix("locations/")
    if not location.startswith("locations/") or not location_id or "/" in location_id:
        raise ValueError("Location must be a Business Profile resource like locations/12345")
    if not plan.regular_periods:
        raise ValueError("Cannot publish special hours without recurring regular hours")

    client = session or requests.Session()
    token_response = client.post(
        _TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    token_response.raise_for_status()
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    url = _API_BASE + location
    current_response = client.get(
        url,
        params={"readMask": "regularHours,specialHours"},
        headers=headers,
        timeout=30,
    )
    current_response.raise_for_status()
    existing_hours = current_response.json().get("specialHours") or {}
    existing = existing_hours.get("specialHourPeriods", [])
    payload = plan.as_location_patch()
    payload["name"] = location
    payload["specialHours"]["specialHourPeriods"] = merge_special_periods(
        existing, plan.special_periods, first_date=first_date, last_date=last_date
    )
    params = {"updateMask": "regularHours,specialHours"}
    validation = client.patch(
        url,
        params={**params, "validateOnly": "true"},
        json=payload,
        headers=headers,
        timeout=30,
    )
    validation.raise_for_status()
    published = client.patch(url, params=params, json=payload, headers=headers, timeout=30)
    published.raise_for_status()
