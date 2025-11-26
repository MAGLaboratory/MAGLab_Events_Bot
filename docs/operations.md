# Operations Runbook

## Requirements
- Python 3.10+
- Dependencies installed via `poetry install` or `pip install -r requirements.txt`
- Keep security patches current: after editing dependencies run `poetry lock --no-update` (or `poetry lock`) to refresh `poetry.lock` before deploying.

## Configuration
1. Copy `.env.example` to `.env`.
2. Set `DISCORD_TOKEN` and other values for your environment.
3. Optional: override intervals with environment overrides (e.g. `OPEN_STATUS_INTERVAL_MINUTES`).
4. Optional: tune `SYNOPTIC_MAX_AGE_MINUTES` if the cached synoptic PNG needs to refresh more or less often. Increase `SYNOPTIC_HTTP_TIMEOUT_SECONDS` if the HAL page is slow to respond.

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
- Discord scheduled events should reflect HAL status and Google Calendar events within the configured intervals.

## Troubleshooting
- Enable `requests` debugging by setting `LOGLEVEL=DEBUG` before running.
- Validate HAL connectivity by running `poetry run maglab-events-bot generate-synoptic`.
- Ensure the bot has `Manage Events` permission in the target guild.
