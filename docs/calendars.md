# Google Calendar Mapping

The bot syncs scheduled events from multiple Google Calendar ICS feeds into Discord scheduled events.

| Calendar | ICS URL | Notes |
|----------|---------|-------|
| MAGLab Events | https://calendar.google.com/calendar/ical/c_3keov3j3lc5qscq754mb4n38b4%40group.calendar.google.com/public/basic.ics | Public-facing events |
| Curator Schedule | https://calendar.google.com/calendar/ical/bjpkvaeg1rjq9u3c6utecq1jos%40group.calendar.google.com/public/basic.ics | Staff & curator shifts |

## Adding a Calendar
1. Expose the Google Calendar as a public ICS feed.
2. Append the ICS URL to the `ICS_URLS` environment variable (comma-separated).
3. Restart the bot to pick up the change.
