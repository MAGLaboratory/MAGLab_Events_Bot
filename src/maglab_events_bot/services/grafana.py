"""Utilities for determining open status via Grafana alerting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Optional

import requests
from requests.auth import HTTPBasicAuth

from maglab_events_bot.utils.http import build_session

logger = logging.getLogger(__name__)

ALERTING_STATES = {"alerting", "firing", "active", "on"}
RESOLVED_STATES = {"ok", "normal", "resolved", "inactive", "off"}
SUPPRESSED_STATES = {"pending", "no_data", "paused"}


def _build_alerts_url(base_url: str, endpoint: str) -> str:
    endpoint = endpoint or "/api/alerts"
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    base = base_url.rstrip("/")
    path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    return f"{base}{path}"


async def _fetch_alert_payload(
    url: str,
    session: requests.Session,
    auth: Optional[HTTPBasicAuth],
    verify_tls: bool,
) -> Optional[Any]:
    loop = asyncio.get_running_loop()

    def _make_request() -> Optional[Any]:
        response = session.get(url, auth=auth, verify=verify_tls)
        response.raise_for_status()
        return response.json()

    try:
        return await loop.run_in_executor(None, _make_request)
    except requests.RequestException as exc:
        logger.warning("grafana.fetch_failed", extra={"url": url, "error": str(exc)})
    except ValueError as exc:  # JSON decode error
        logger.error("grafana.invalid_response", extra={"url": url, "error": str(exc)})
    return None


def _iter_alerts(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(payload, dict):
        # Unified alerting returns an object containing "alerts".
        for key in ("alerts", "data", "items", "values"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                for item in maybe:
                    if isinstance(item, dict):
                        yield item
                return
        # Payload might already represent a single alert entry.
        if payload:
            yield payload


def _candidate_names(alert: dict[str, Any]) -> Iterable[str]:
    labels = alert.get("labels")
    if not isinstance(labels, dict):
        labels = {}

    annotations = alert.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}

    candidates = [
        alert.get("name"),
        alert.get("title"),
        alert.get("alertRuleName"),
        alert.get("alertName"),
        labels.get("alertname") if isinstance(labels, dict) else None,
        labels.get("rule") if isinstance(labels, dict) else None,
        annotations.get("summary") if isinstance(annotations, dict) else None,
    ]

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            yield candidate.strip()


def _match_alert(payload: Any, alert_name: str) -> Optional[dict[str, Any]]:
    if not alert_name:
        return None
    needle = alert_name.casefold()
    for alert in _iter_alerts(payload):
        for candidate in _candidate_names(alert):
            if candidate.casefold() == needle:
                return alert
    return None


def _coerce_state(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("state", "status", "value"):
            state_value = value.get(key)
            if isinstance(state_value, str) and state_value.strip():
                return state_value.strip()
    return None


def _extract_state(alert: dict[str, Any]) -> Optional[str]:
    for key in ("state", "status", "value"):
        if key in alert:
            state = _coerce_state(alert.get(key))
            if state:
                return state

    eval_data = alert.get("evalData")
    if isinstance(eval_data, dict):
        state = _coerce_state(eval_data.get("state"))
        if state:
            return state
    return None


def _state_to_bool(state: str) -> Optional[bool]:
    normalized = state.strip().lower()
    if normalized in ALERTING_STATES:
        return True
    if normalized in RESOLVED_STATES:
        return False
    if normalized in SUPPRESSED_STATES:
        return False
    return None


async def fetch_grafana_open_status(
    base_url: str,
    alert_name: str,
    alerts_endpoint: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_tls: bool = True,
    session: Optional[requests.Session] = None,
) -> Optional[bool]:
    """Return True if the alert is firing, False if resolved, or None on failure."""

    if not base_url:
        logger.error("grafana.base_url_missing")
        return None

    url = _build_alerts_url(base_url, alerts_endpoint)
    auth = HTTPBasicAuth(username, password) if username and password else None
    session = session or build_session()

    payload = await _fetch_alert_payload(url, session, auth, verify_tls)
    if payload is None:
        return None

    alert = _match_alert(payload, alert_name)
    if alert is None:
        logger.warning(
            "grafana.alert_not_found",
            extra={"url": url, "alert_name": alert_name},
        )
        return None

    state = _extract_state(alert)
    if not state:
        logger.warning(
            "grafana.state_missing",
            extra={"alert_name": alert_name},
        )
        return None

    result = _state_to_bool(state)
    if result is None:
        logger.warning(
            "grafana.unknown_state",
            extra={"alert_name": alert_name, "state": state},
        )
    return result
