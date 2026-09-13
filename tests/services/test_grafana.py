import asyncio
from datetime import datetime, timedelta, timezone

from maglab_events_bot.services import grafana


def _payload(value, sampled_at=None):
    sampled_at = sampled_at or datetime.now(timezone.utc)
    timestamp_ms = int(sampled_at.timestamp() * 1000)
    return {
        "results": [
            {
                "statement_id": 0,
                "series": [
                    {
                        "name": "maglab",
                        "columns": ["time", "value"],
                        "values": [[timestamp_ms, value]],
                    }
                ],
            }
        ]
    }


def _run_fetch(monkeypatch, payload, **kwargs):
    async def fake_fetch(_url, _params, _session, _auth):
        return payload

    kwargs.setdefault("base_url", "https://example.com")
    kwargs.setdefault("datasource_id", 1)
    kwargs.setdefault("database", "maglab")
    kwargs.setdefault("measurement", "maglab")
    kwargs.setdefault("field", "Open Switch")
    kwargs.setdefault("max_age_minutes", 15)

    monkeypatch.setattr(grafana, "_fetch_query_payload", fake_fetch)
    return asyncio.run(grafana.fetch_grafana_open_status(**kwargs))


def test_fetch_grafana_open_status_maps_one_to_open(monkeypatch):
    assert _run_fetch(monkeypatch, _payload(1)) is True


def test_fetch_grafana_open_status_maps_zero_to_closed(monkeypatch):
    assert _run_fetch(monkeypatch, _payload(0)) is False


def test_fetch_grafana_open_status_rejects_stale_sample(monkeypatch):
    sampled_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    assert _run_fetch(monkeypatch, _payload(1, sampled_at)) is None


def test_fetch_grafana_open_status_returns_none_when_sample_missing(monkeypatch):
    assert _run_fetch(monkeypatch, {"results": [{}]}) is None


def test_fetch_grafana_open_status_returns_none_for_unknown_value(monkeypatch):
    assert _run_fetch(monkeypatch, _payload(2)) is None


def test_build_last_values_query_quotes_identifiers():
    query = grafana._build_last_values_query('lab"status', ["Open Switch"])

    assert query == 'SELECT last("Open Switch") AS "value" FROM "lab\\"status"'


def test_extract_latest_samples_maps_statement_ids():
    sampled_at = datetime.now(timezone.utc)
    timestamp_ms = int(sampled_at.timestamp() * 1000)
    payload = {
        "results": [
            {
                "statement_id": 0,
                "series": [{"columns": ["time", "value"], "values": [[timestamp_ms, 1]]}],
            },
            {
                "statement_id": 1,
                "series": [{"columns": ["time", "value"], "values": [[timestamp_ms, 30125]]}],
            },
        ]
    }

    samples = grafana._extract_latest_samples(payload, ["Open Switch", "Bay Temp"])

    assert samples["Open Switch"].value == 1
    assert samples["Bay Temp"].value == 30125
