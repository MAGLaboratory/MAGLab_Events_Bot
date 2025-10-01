# MAGLab Events Bot Architecture

## Components
- **Discord Bot (`maglab_events_bot.bot`)** – central entry point that wires Discord intents and loads cogs.
- **Open Status Cog (`maglab_events_bot.cogs.open_status`)** – polls the HAL status page and keeps the "We are" scheduled event current.
- **Calendar Sync Cog (`maglab_events_bot.cogs.calendar_sync`)** – reconciles Google Calendar ICS feeds with Discord scheduled events.
- **Services Layer** – pure functions/classes for external integrations:
  - `services.hal` scrapes HAL status and formats sensor readings.
  - `services.calendar` expands ICS feeds, recurrences, and cancellations.
  - `services.synoptic` renders the synoptic SVG to a Discord-friendly PNG.
  - `services.discord_api` wraps Discord scheduled-event operations.
- **Utilities** – shared helpers for HTTP sessions and formatting.
- **Tasks** – high-level helpers around shared background responsibilities (e.g. synoptic image generation).

## Data Flow
1. Background loop fires (open status every few minutes, calendar hourly).
2. Cohesive service fetches external data (HAL or ICS feed).
3. Models layer normalises domain objects (`HalStatus`, `CalendarEvent`).
4. Discord service applies changes: creates/updates/deletes events, enforces single synoptic image policy.
5. Logging is centralised and emitted via rotating file + console.

## Extension Points
- Add new cogs for future sync tasks and register them in `MagLabBot.setup_hook`.
- Extend `services/discord_api.py` for more granular Discord interactions.
- Add persistent storage for audit trails if necessary (e.g., SQLite repository module).
