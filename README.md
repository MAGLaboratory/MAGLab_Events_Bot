# MAGLab Events Bot

Unified Discord bot that keeps MAG Laboratory's scheduled events aligned with real-world status:
- Mirrors Google Calendar events into Discord scheduled events with cancellation handling.
- Publishes Grafana's live open-switch status as a rolling "We are" event.
- Enforces a single synoptic status image across all scheduled events.

![image](https://github.com/user-attachments/assets/d533bfe2-d30d-4550-8d32-40458fe55b72)

## Getting Started
1. Install dependencies with Poetry: `poetry install` (generate `requirements.txt` later with `poetry export` if another environment needs pip).
2. Copy `.env.example` to `.env` and populate the Discord token plus any overrides.
3. Run the bot: `poetry run maglab-events-bot run-bot` (or `python -m maglab_events_bot`).

## Project Layout
```
src/maglab_events_bot/
├── bot.py                  # Discord bot wiring and startup
├── cli.py                  # Command-line utilities (run bot, generate synoptic image)
├── cogs/
│   ├── calendar_sync.py    # Google Calendar → Discord events sync loop
│   └── open_status.py      # Grafana open-switch polling loop
├── config.py               # Centralized settings via environment variables
├── logging.py              # Logging configuration helpers
├── models/                 # Dataclasses for HAL and calendar data
├── services/
│   ├── calendar.py         # ICS ingestion and normalization
│   ├── discord_api.py      # Scheduled-event helpers
│   ├── hal.py              # HAL scraping and parsing
│   └── synoptic.py         # Synoptic image rendering
├── tasks/                  # Shared background-task helpers
└── utils/                  # Formatting and HTTP utilities
```

Supporting resources live in `docs/` (architecture, operations, calendar mapping) and `tests/` for automated coverage scaffolding. The historical `scripts/` entry points were removed—run tasks through the CLI (`poetry run maglab-events-bot ...`) instead.

## Configuration
| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Bot token with permission to manage scheduled events | required |
| `GUILD_ID` | Discord guild/server ID | `697971426799517774` |
| `HAL_STATUS_URL` | Source of HAL sensor details | `https://www.maglaboratory.org/hal` |
| `OPEN_STATUS_INTERVAL_MINUTES` | Open-switch polling frequency | `5` |
| `ICS_URLS` | Comma-separated Google Calendar ICS feeds | default public calendars |
| `SYNC_DAYS` | Number of future days to sync | `7` |
| `CALENDAR_SYNC_INTERVAL_HOURS` | Calendar sync cadence | `1` |
| `TIMEZONE` | Display timezone | `America/Los_Angeles` |
| `GRAFANA_BASE_URL` | Base URL for Grafana (needs intranet reachability) | `https://jane.maglab` |
| `GRAFANA_DATASOURCE_ID` | Numeric ID of the InfluxDB datasource | `1` |
| `GRAFANA_DATABASE` | InfluxDB database containing the switch | `maglab` |
| `GRAFANA_MEASUREMENT` | InfluxDB measurement containing the switch | `maglab` |
| `GRAFANA_OPEN_SWITCH_FIELD` | Field used as the authoritative open state | `Open Switch` |
| `GRAFANA_MAX_SAMPLE_AGE_MINUTES` | Reject switch readings older than this | `15` |
| `GRAFANA_USERNAME` / `GRAFANA_PASSWORD` | Credentials for Grafana basic auth | none |
| `GRAFANA_VERIFY_SSL` | Whether to validate Grafana TLS certificates | `true` |
| `SYNOPTIC_MAX_AGE_MINUTES` | Minutes before regenerating the cached synoptic image | `15` |
| `SYNOPTIC_HTTP_TIMEOUT_SECONDS` | HTTP timeout for fetching the synoptic SVG | `20` |

### Intranet Connectivity
- The Grafana datasource proxy lives on the internal network (`https://jane.maglab` at `10.110.0.52`). Connect to the `maglab` WireGuard profile (e.g., `nmcli connection up maglab`) before running the bot.
- Ensure the hostname resolves in the runtime environment. If DNS doesn’t provide it, add `10.110.0.52 jane.maglab` to `/etc/hosts` for both the host and any containers running the bot.
- Grafana uses the MAGLab Root CA. Either import that certificate into the system trust store or set `GRAFANA_VERIFY_SSL=false` (less secure) to skip verification.
- These requirements apply equally to CI/servers—document how the network is reached wherever the bot is deployed.

### Health Check
Run the health probe before daemonizing or after changing VPN/DNS credentials:

```bash
poetry run maglab-events-bot health-check
# or: PYTHONPATH=src python -m maglab_events_bot.cli health-check
```

It pings the HAL page and reads the live Grafana open switch once, failing fast if either is unreachable, unauthorized, or the switch sample is stale. Fix connectivity issues (VPN, `/etc/hosts`, credentials, TLS trust) until this command reports success.

## Development
- Run all checks: `poetry run nox`
- Individual tasks: `poetry run nox -s lint`, `poetry run nox -s typecheck`, `poetry run nox -s tests`
- Logs are written to `logs/maglab_events_bot.log`

## Deployment Notes
- Ensure the bot has `Manage Events` permission in the target guild.
- For containerized deployments, mount a writable `logs/` directory.
- Rotate tokens and update `.env` when credentials change.
