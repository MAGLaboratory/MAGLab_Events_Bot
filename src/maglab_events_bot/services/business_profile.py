"""Guarded Google Business Profile hours publisher."""

from __future__ import annotations

from datetime import date
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from maglab_events_bot.services.business_hours import BusinessHoursPlan

_API_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1/"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_RETRYABLE_STATUSES = (429, 500, 502, 503, 504)


def _build_retrying_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        allowed_methods=frozenset({"GET", "POST", "PATCH"}),
        status_forcelist=_RETRYABLE_STATUSES,
        backoff_factor=0.5,
        backoff_jitter=0.25,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


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
        if start < first_date <= end or start <= last_date < end:
            raise ValueError("An existing special-hours period crosses the managed window")
        if not (first_date <= start <= last_date):
            retained.append(period)
    return retained + planned


def _canonical_time(value: dict[str, Any] | None) -> tuple[int, int, int, int]:
    source = value or {}
    return (
        int(source.get("hours", 0)),
        int(source.get("minutes", 0)),
        int(source.get("seconds", 0)),
        int(source.get("nanos", 0)),
    )


def _canonical_date(value: dict[str, Any]) -> tuple[int, int, int]:
    return (int(value["year"]), int(value["month"]), int(value["day"]))


def _canonical_regular_periods(periods: list[dict]) -> tuple[tuple, ...]:
    return tuple(
        sorted(
            (
                period.get("openDay", ""),
                _canonical_time(period.get("openTime")),
                period.get("closeDay", ""),
                _canonical_time(period.get("closeTime")),
            )
            for period in periods
        )
    )


def _canonical_special_periods(periods: list[dict]) -> tuple[tuple, ...]:
    canonical = []
    for period in periods:
        start = _canonical_date(period["startDate"])
        closed = bool(period.get("closed", False))
        canonical.append(
            (
                start,
                start if closed else _canonical_date(period.get("endDate", period["startDate"])),
                closed,
                (0, 0, 0, 0) if closed else _canonical_time(period.get("openTime")),
                (0, 0, 0, 0) if closed else _canonical_time(period.get("closeTime")),
            )
        )
    return tuple(sorted(canonical))


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
) -> bool:
    """Validate then update only hours fields; never change a listing without regular hours."""
    location_id = location.removeprefix("locations/")
    if not location.startswith("locations/") or not location_id or "/" in location_id:
        raise ValueError("Location must be a Business Profile resource like locations/12345")
    if not plan.regular_periods:
        raise ValueError("Cannot publish special hours without recurring regular hours")

    client = session or _build_retrying_session()
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
    current_location = current_response.json()
    existing_regular = (current_location.get("regularHours") or {}).get("periods", [])
    existing_hours = current_location.get("specialHours") or {}
    existing = existing_hours.get("specialHourPeriods", [])
    payload = plan.as_location_patch()
    payload["name"] = location
    payload["specialHours"]["specialHourPeriods"] = merge_special_periods(
        existing, plan.special_periods, first_date=first_date, last_date=last_date
    )
    desired_regular = payload["regularHours"]["periods"]
    desired_special = payload["specialHours"]["specialHourPeriods"]
    if _canonical_regular_periods(existing_regular) == _canonical_regular_periods(
        desired_regular
    ) and _canonical_special_periods(existing) == _canonical_special_periods(desired_special):
        return False

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
    return True
