import discord
import requests
import datetime
import re
import html
import pendulum
from icalendar import Calendar
from dateutil.rrule import rrulestr
from discord.ext import tasks, commands

# Read the Discord token from a file named 'discord_token.txt'
def get_discord_token():
    with open('discord_token.txt', 'r') as file:
        return file.read().strip()

DISCORD_TOKEN = get_discord_token()
GUILD_ID = 697971426799517774  # Guild ID set to 697971426799517774

# Updated ICS URLs
ICS_URLS = [
    'https://calendar.google.com/calendar/ical/c_3keov3j3lc5qscq754mb4n38b4%40group.calendar.google.com/public/basic.ics',
    'https://calendar.google.com/calendar/ical/bjpkvaeg1rjq9u3c6utecq1jos%40group.calendar.google.com/public/basic.ics',
]

SYNC_DAYS = 7
VERBOSE_MODE = False
DESCRIPTION_MAX_LENGTH = 1000

LA_TZ = pendulum.timezone('America/Los_Angeles')  # Timezone for Los Angeles

intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)

def normalize_date(dt):
    """Ensure dates are returned as timezone-aware datetime."""
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        dt = pendulum.datetime(dt.year, dt.month, dt.day, tz='UTC')
    elif dt.tzinfo is None:
        dt = pendulum.instance(dt, tz='UTC')
    else:
        dt = pendulum.instance(dt)
    return dt

def adjust_rrule_for_utc(rrule_str, start):
    """Ensure RRULE UNTIL is in UTC if DTSTART is timezone-aware."""
    if 'UNTIL' in rrule_str and start.timezone is not None:
        rrule_parts = rrule_str.split(';')
        for i, part in enumerate(rrule_parts):
            if part.startswith('UNTIL=') and not part.endswith('Z'):
                until_value = part.split('=')[1] + 'Z'
                rrule_parts[i] = f'UNTIL={until_value}'
        return ';'.join(rrule_parts)
    return rrule_str

def clean_description(description):
    """Remove HTML tags and decode entities in event descriptions."""
    description = re.sub(r'<[^>]+>', '', description)
    return html.unescape(description)

def truncate_description(description):
    """Clean and truncate the description to 1000 characters."""
    clean_desc = clean_description(description)
    return clean_desc[:DESCRIPTION_MAX_LENGTH] if len(clean_desc) > DESCRIPTION_MAX_LENGTH else clean_desc

def fetch_calendar_events():
    """Fetch and return calendar events for the next SYNC_DAYS."""
    events = []
    now = pendulum.now('UTC')
    future = now.add(days=SYNC_DAYS)

    for url in ICS_URLS:
        calendar = Calendar.from_ical(requests.get(url).content)
        for component in calendar.walk():
            if component.name == "VEVENT":
                start = normalize_date(component.get('dtstart').dt)
                end = normalize_date(component.get('dtend').dt)
                timezone = start.timezone if start.timezone else pendulum.timezone('UTC')  # Use local timezone if available
                start = start.in_tz(timezone)  # Convert to local timezone for BYDAY handling
                end = end.in_tz(timezone)

                if component.get('rrule'):
                    rrule_str = adjust_rrule_for_utc(component.get('rrule').to_ical().decode('utf-8'), start)
                    try:
                        # Apply recurrence rules in the local timezone
                        rule = rrulestr(rrule_str, dtstart=start)
                        occurrences = rule.between(now.in_tz(timezone), future.in_tz(timezone))
                    except ValueError as e:
                        print(f"RRULE error in {component.get('summary')}: {e}")
                        continue
                    for occ in occurrences:
                        events.append({
                            'name': component.get('summary'),
                            'description': truncate_description(component.get('description', 'No description provided')),
                            'start_time': pendulum.instance(occ, tz='UTC'),  # Convert to UTC for Discord
                            'end_time': pendulum.instance(occ + (end - start), tz='UTC'),
                            'location': component.get('location', 'MAG Laboratory')
                        })
                elif now <= start <= future:
                    events.append({
                        'name': component.get('summary'),
                        'description': truncate_description(component.get('description', 'No description provided')),
                        'start_time': start.in_tz('UTC'),
                        'end_time': end.in_tz('UTC'),
                        'location': component.get('location', 'MAG Laboratory')
                    })
    return events

def find_matching_discord_event(discord_events, cal_event):
    """Find a matching Discord event by both name and start time."""
    for event in discord_events:
        if event.name == cal_event['name'] and event.start_time == cal_event['start_time']:
            return event
    return None

async def sync_discord_events(guild):
    """Sync calendar events with Discord events."""
    existing_events = guild.scheduled_events
    calendar_events = fetch_calendar_events()
    calendar_event_names = [event['name'] for event in calendar_events]

    for cal_event in calendar_events:
        discord_event = find_matching_discord_event(existing_events, cal_event)  # Match by name and start time
        start_time = cal_event['start_time']
        end_time = cal_event['end_time']
        la_time = start_time.in_tz(LA_TZ).to_datetime_string()  # Convert to LA time

        if discord_event:
            if pendulum.now('UTC') < discord_event.start_time:
                print(f"Updating Discord event: {cal_event['name']} (LA time: {la_time})")
                await discord_event.edit(
                    name=cal_event['name'],
                    description=cal_event['description'],
                    start_time=start_time,
                    end_time=end_time,
                    location=cal_event['location']
                )
            else:
                print(f"Updating ongoing Discord event (except start time): {cal_event['name']} (LA time: {la_time})")
                await discord_event.edit(
                    name=cal_event['name'],
                    description=cal_event['description'],
                    end_time=end_time,
                    location=cal_event['location']
                )
        else:
            print(f"Creating new Discord event: {cal_event['name']} (LA time: {la_time})")
            await guild.create_scheduled_event(
                name=cal_event['name'],
                description=cal_event['description'],
                start_time=start_time,
                end_time=end_time,
                entity_type=discord.EntityType.external,
                location=cal_event['location'],
                privacy_level=discord.PrivacyLevel.guild_only
            )

    for discord_event in existing_events:
        if discord_event.name not in calendar_event_names and "We are" not in discord_event.name:
            print(f"Removing Discord event: {discord_event.name} (Not found in calendar)")
            await discord_event.delete()

@tasks.loop(hours=1)
async def sync_events_task():
    """Sync events every hour."""
    guild = discord.utils.get(client.guilds, id=GUILD_ID)
    if guild:
        await sync_discord_events(guild)
    else:
        print("Guild not found!")

@client.event
async def on_ready():
    """Start syncing once the bot is ready."""
    print(f'Logged in as {client.user}')
    sync_events_task.start()

client.run(DISCORD_TOKEN)
