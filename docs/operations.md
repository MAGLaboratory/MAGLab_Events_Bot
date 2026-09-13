# Operations Runbook

## Requirements
- Python 3.10+
- Dependencies installed via `poetry install` or `pip install -r requirements.txt`
- Keep security patches current: after editing dependencies run `poetry lock --no-update` (or `poetry lock`) to refresh `poetry.lock` before deploying.

## Configuration
1. Copy `.env.example` to `.env`.
2. Set `DISCORD_TOKEN` and other values for your environment.
3. Optional: override intervals with environment overrides (e.g. `OPEN_STATUS_INTERVAL_MINUTES`).
4. Configure the Grafana connection; the bot renders the synoptic image locally from each live sensor snapshot.

## Running the Bot
```bash
poetry run maglab-events-bot run-bot
# or
python -m maglab_events_bot
```

## Generating Synoptic Image Manually
```bash
poetry run maglab-events-bot generate-synoptic --output synoptic.png
```

## Monitoring
- Logs are written to `logs/maglab_events_bot.log` and stdout.
- Discord scheduled events should reflect Grafana's live open-switch status and Google Calendar events within the configured intervals.

## Troubleshooting
- Enable `requests` debugging by setting `LOGLEVEL=DEBUG` before running.
- Validate Grafana connectivity and rendering with `poetry run maglab-events-bot generate-synoptic`.
- Run `poetry run maglab-events-bot health-check` to validate the live Grafana switch, its freshness, and HAL sensor connectivity.
- Ensure the bot has `Manage Events` permission in the target guild.
