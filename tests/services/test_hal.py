import asyncio
from textwrap import dedent

import pendulum

from maglab_events_bot.services import hal

OPEN_HTML = dedent(
    """
    <html>
      <body>
        <h1>We are OPEN!</h1>
        <table>
          <tr><th>Sensor</th><th>Status</th><th>Last Update</th></tr>
          <tr><td>Door</td><td>Closed</td><td>Sep 30, 2024, 08:15 PM PDT</td></tr>
          <tr><td>Motion</td><td>No Movement</td><td>Sep 30, 2024, 08:14 PM PDT</td></tr>
        </table>
      </body>
    </html>
    """
)

CLOSED_HTML = "<html><body><p>We are CLOSED.</p></body></html>"

FRAGMENTED_OPEN_HTML = dedent(
    """
    <html>
      <body>
        <div id="activity_panel">
          <div id="openness" class="alert alert-success">
            <h1>We are <strong>OPEN</strong></h1>
          </div>
        </div>
        <svg>
          <g id="pod-bay-door">
            <path id="pod-bay-door_closed" />
          </g>
        </svg>
      </body>
    </html>
    """
)


def test_fetch_hal_status_parses_open_state(monkeypatch):
    async def fake_fetch(_url: str, _session) -> str:
        return OPEN_HTML

    monkeypatch.setattr(hal, "_fetch_hal_page", fake_fetch)
    tz = pendulum.timezone("America/Los_Angeles")

    result = asyncio.run(hal.fetch_hal_status("https://example.com", tz, session=object()))

    assert result is not None
    assert result.is_open
    assert len(result.sensors) == 2
    assert result.sensors[0].name == "Door"
    assert result.sensors[1].status == "No Motion"


def test_fetch_hal_status_handles_closed_without_table(monkeypatch):
    async def fake_fetch(_url: str, _session) -> str:
        return CLOSED_HTML

    monkeypatch.setattr(hal, "_fetch_hal_page", fake_fetch)
    tz = pendulum.timezone("America/Los_Angeles")

    result = asyncio.run(hal.fetch_hal_status("https://example.com", tz, session=object()))

    assert result is not None
    assert not result.is_open
    assert result.sensors == []


def test_fetch_hal_status_handles_fragmented_banner(monkeypatch):
    async def fake_fetch(_url: str, _session) -> str:
        return FRAGMENTED_OPEN_HTML

    monkeypatch.setattr(hal, "_fetch_hal_page", fake_fetch)
    tz = pendulum.timezone("America/Los_Angeles")

    result = asyncio.run(hal.fetch_hal_status("https://example.com", tz, session=object()))

    assert result is not None
    assert result.is_open


def test_find_sensor_table_matches_expected_structure():
    soup = hal.BeautifulSoup(OPEN_HTML, "html.parser")
    table = hal._find_sensor_table(soup)
    assert table is not None
    headers = [cell.get_text(strip=True) for cell in table.find_all("th")]
    assert headers == ["Sensor", "Status", "Last Update"]
