import asyncio

from maglab_events_bot.services import grafana


def _run_fetch(monkeypatch, payload, **kwargs):
    async def fake_fetch(_url, _session, _auth, _verify):
        return payload

    kwargs.setdefault("base_url", "https://example.com")
    kwargs.setdefault("alert_name", "The space is OPEN HAL status open")
    kwargs.setdefault("alerts_endpoint", "/api/alerts")

    monkeypatch.setattr(grafana, "_fetch_alert_payload", fake_fetch)
    return asyncio.run(grafana.fetch_grafana_open_status(**kwargs))


def test_fetch_grafana_open_status_handles_alertmanager_payload(monkeypatch):
    payload = [
        {
            "labels": {"alertname": "The space is OPEN HAL status open"},
            "status": {"state": "active"},
        }
    ]
    result = _run_fetch(
        monkeypatch,
        payload,
        alerts_endpoint="/api/alertmanager/grafana/api/v2/alerts",
    )
    assert result is True


def test_fetch_grafana_open_status_handles_legacy_payload(monkeypatch):
    payload = [
        {
            "name": "The space is OPEN HAL status open",
            "state": "ok",
        }
    ]
    result = _run_fetch(monkeypatch, payload)
    assert result is False


def test_fetch_grafana_open_status_returns_none_when_missing_alert(monkeypatch):
    payload = [{"name": "Another Alert", "state": "alerting"}]
    result = _run_fetch(monkeypatch, payload)
    assert result is None
