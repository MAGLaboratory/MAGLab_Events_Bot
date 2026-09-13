"""Utilities for reading the live MAGLab open switch through Grafana."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import aiohttp

from maglab_events_bot.utils.http import build_aiohttp_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GrafanaSample:
    """A value and its InfluxDB sample time."""

    value: Any
    sampled_at: datetime


def _build_query_url(base_url: str, datasource_id: int) -> str:
    return f"{base_url.rstrip('/')}/api/datasources/proxy/{datasource_id}/query"


def _quote_influx_identifier(identifier: str) -> str:
    escaped = identifier.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_last_values_query(measurement: str, fields: Sequence[str]) -> str:
    measurement_name = _quote_influx_identifier(measurement)
    return "; ".join(
        f'SELECT last({_quote_influx_identifier(field)}) AS "value" FROM {measurement_name}'
        for field in fields
    )


async def _fetch_query_payload(
    url: str,
    params: dict[str, str],
    session: aiohttp.ClientSession,
    auth: Optional[aiohttp.BasicAuth],
) -> Optional[Any]:
    try:
        async with session.get(url, params=params, auth=auth) as response:
            response.raise_for_status()
            return await response.json()
    except aiohttp.ClientError as exc:
        logger.warning("grafana.fetch_failed", extra={"url": url, "error": str(exc)})
    except ValueError as exc:  # JSON decode error
        logger.error("grafana.invalid_response", extra={"url": url, "error": str(exc)})
    return None


def _extract_latest_samples(
    payload: Any,
    fields: Sequence[str],
) -> dict[str, GrafanaSample]:
    samples: dict[str, GrafanaSample] = {}
    if not isinstance(payload, dict):
        return samples

    results = payload.get("results")
    if not isinstance(results, list):
        return samples

    for result in results:
        if not isinstance(result, dict):
            continue
        statement_id = result.get("statement_id")
        if not isinstance(statement_id, int) or not 0 <= statement_id < len(fields):
            continue
        series_entries = result.get("series")
        if not isinstance(series_entries, list):
            continue
        for series in series_entries:
            if not isinstance(series, dict):
                continue
            columns = series.get("columns")
            values = series.get("values")
            if not isinstance(columns, list) or not isinstance(values, list) or not values:
                continue
            try:
                time_index = columns.index("time")
                value_index = columns.index("value")
            except ValueError:
                continue
            row = values[0]
            if not isinstance(row, list):
                continue
            try:
                timestamp_ms = int(row[time_index])
                value = row[value_index]
            except (IndexError, TypeError, ValueError):
                continue
            try:
                sampled_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
            samples[fields[statement_id]] = GrafanaSample(value=value, sampled_at=sampled_at)
    return samples


def sample_is_fresh(
    sampled_at: datetime,
    max_age_minutes: int,
    now: datetime | None = None,
) -> bool:
    """Return whether a timestamp is recent enough to use."""

    current_time = now or datetime.now(timezone.utc)
    age_seconds = (current_time - sampled_at).total_seconds()
    return -60 <= age_seconds <= max_age_minutes * 60


def coerce_grafana_bool(value: Any) -> Optional[bool]:
    """Convert Grafana's common binary representations to a boolean."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "open", "on"}:
            return True
        if normalized in {"0", "false", "closed", "off"}:
            return False
    return None


async def fetch_grafana_open_status(
    base_url: str,
    datasource_id: int,
    database: str,
    measurement: str,
    field: str,
    max_age_minutes: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_tls: bool = True,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[bool]:
    """Return the live switch state, or None if it cannot be trusted."""

    samples = await fetch_grafana_sensor_samples(
        base_url=base_url,
        datasource_id=datasource_id,
        database=database,
        measurement=measurement,
        fields=[field],
        username=username,
        password=password,
        verify_tls=verify_tls,
        session=session,
    )
    return get_grafana_open_status(samples, field, max_age_minutes)


def get_grafana_open_status(
    samples: Optional[dict[str, GrafanaSample]],
    field: str,
    max_age_minutes: int,
) -> Optional[bool]:
    """Interpret an open-switch sample from an existing Grafana snapshot."""

    if samples is None:
        return None
    sample = samples.get(field)
    if sample is None:
        logger.warning("grafana.open_switch_sample_missing", extra={"field": field})
        return None
    if not sample_is_fresh(sample.sampled_at, max_age_minutes):
        logger.warning(
            "grafana.open_switch_sample_stale",
            extra={
                "sampled_at": sample.sampled_at.isoformat(),
                "max_age_minutes": max_age_minutes,
            },
        )
        return None
    result = coerce_grafana_bool(sample.value)
    if result is None:
        logger.warning("grafana.open_switch_value_unknown", extra={"value": sample.value})
    return result


async def fetch_grafana_sensor_samples(
    base_url: str,
    datasource_id: int,
    database: str,
    measurement: str,
    fields: Sequence[str],
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_tls: bool = True,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[dict[str, GrafanaSample]]:
    """Fetch the latest value and timestamp for each requested field."""

    if not base_url:
        logger.error("grafana.base_url_missing")
        return None
    if not fields:
        return {}

    url = _build_query_url(base_url, datasource_id)
    params = {
        "db": database,
        "q": _build_last_values_query(measurement, fields),
        "epoch": "ms",
    }
    auth = aiohttp.BasicAuth(username, password) if username and password else None
    owns_session = False
    if session is None:
        session = await build_aiohttp_client(verify_ssl=verify_tls)
        owns_session = True

    try:
        payload = await _fetch_query_payload(url, params, session, auth)
        if payload is None:
            return None

        return _extract_latest_samples(payload, fields)
    finally:
        if owns_session:
            await session.close()
