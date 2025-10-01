# Operations Runbook

## Requirements
- Python 3.10+
- Dependencies installed via `poetry install` or `pip install -r requirements.txt`

## Configuration
1. Copy `.env.example` to `.env`.
2. Set `DISCORD_TOKEN` and other values for your environment.
3. Optional: override intervals with environment overrides (e.g. `OPEN_STATUS_INTERVAL_MINUTES`).

## Running the Bot
```bash
poetry run maglab-run-bot
# or
python -m maglab_events_bot
# and for subcommands:
poetry run maglab-events-bot run-bot
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
- Validate HAL connectivity by running `python scripts/scrape_synoptic_view_and_crop_scale_for_discord_events.py`.
- Ensure the bot has `Manage Events` permission in the target guild.
