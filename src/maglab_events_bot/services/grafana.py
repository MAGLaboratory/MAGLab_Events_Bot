"""Utilities for reading the live MAGLab open switch through Grafana."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from maglab_events_bot.utils.http import build_aiohttp_client

logger = logging.getLogger(__name__)


def _build_query_url(base_url: str, datasource_id: int) -> str:
    return f"{base_url.rstrip('/')}/api/datasources/proxy/{datasource_id}/query"


def _quote_influx_identifier(identifier: str) -> str:
    escaped = identifier.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_last_value_query(measurement: str, field: str) -> str:
    return (
        f'SELECT last({_quote_influx_identifier(field)}) AS "open_switch" '
        f"FROM {_quote_influx_identifier(measurement)}"
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


def _extract_latest_sample(payload: Any) -> Optional[tuple[int, Any]]:
    if not isinstance(payload, dict):
        return None

    results = payload.get("results")
    if not isinstance(results, list):
        return None

    for result in results:
        if not isinstance(result, dict):
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
                value_index = columns.index("open_switch")
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
            return timestamp_ms, value
    return None


def _sample_is_fresh(timestamp_ms: int, max_age_minutes: int) -> bool:
    sampled_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - sampled_at).total_seconds()
    return -60 <= age_seconds <= max_age_minutes * 60


def _switch_value_to_bool(value: Any) -> Optional[bool]:
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

    if not base_url:
        logger.error("grafana.base_url_missing")
        return None

    url = _build_query_url(base_url, datasource_id)
    params = {
        "db": database,
        "q": _build_last_value_query(measurement, field),
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

        sample = _extract_latest_sample(payload)
        if sample is None:
            logger.warning("grafana.open_switch_sample_missing", extra={"url": url})
            return None

        timestamp_ms, value = sample
        if not _sample_is_fresh(timestamp_ms, max_age_minutes):
            logger.warning(
                "grafana.open_switch_sample_stale",
                extra={"timestamp_ms": timestamp_ms, "max_age_minutes": max_age_minutes},
            )
            return None

        result = _switch_value_to_bool(value)
        if result is None:
            logger.warning("grafana.open_switch_value_unknown", extra={"value": value})
        return result
    finally:
        if owns_session:
            await session.close()
