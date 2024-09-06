import discord
import requests
import datetime
import re
import html
from icalendar import Calendar
from dateutil.rrule import rrulestr
from discord.ext import tasks, commands
import os

# Read the Discord token from a file named 'discord_token.txt'
def get_discord_token():
    with open('discord_token.txt', 'r') as file:
        return file.read().strip()

DISCORD_TOKEN = get_discord_token()
GUILD_ID = 697971426799517774  # Replace with your actual Guild ID

# Updated ICS URLs
ICS_URLS = [
    'https://calendar.google.com/calendar/ical/c_3keov3j3lc5qscq754mb4n38b4%40group.calendar.google.com/public/basic.ics',
    'https://calendar.google.com/calendar/ical/bjpkvaeg1rjq9u3c6utecq1jos%40group.calendar.google.com/public/basic.ics',
]

SYNC_DAYS = 7
VERBOSE_MODE = False
DESCRIPTION_MAX_LENGTH = 1000

intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)

def normalize_date(dt):
    """Ensure dates are returned as datetime with UTC."""
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        return datetime.datetime.combine(dt, datetime.time(0, 0, 0), tzinfo=datetime.timezone.utc)
    return dt

def adjust_rrule_for_utc(rrule_str, start):
    """Ensure RRULE UNTIL is in UTC if DTSTART is timezone-aware."""
    if 'UNTIL' in rrule_str and start.tzinfo is not None:
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
    now = datetime.datetime.now(datetime.timezone.utc)
    future = now + datetime.timedelta(days=SYNC_DAYS)

    for url in ICS_URLS:
        calendar = Calendar.from_ical(requests.get(url).content)
        for component in calendar.walk():
            if component.name == "VEVENT":
                start = normalize_date(component.get('dtstart').dt)
                if component.get('rrule'):
                    rrule_str = adjust_rrule_for_utc(component.get('rrule').to_ical().decode('utf-8'), start)
                    try:
                        occurrences = rrulestr(rrule_str, dtstart=start).between(now, future)
                    except ValueError as e:
                        print(f"RRULE error in {component.get('summary')}: {e}")
                        continue
                    for occ in occurrences:
                        events.append({
                            'name': component.get('summary'),
                            'description': truncate_description(component.get('description', 'No description provided')),
                            'start_time': occ,
                            'end_time': occ + (normalize_date(component.get('dtend').dt) - start),
                            'location': component.get('location', 'MAG Laboratory')
                        })
                elif now <= start <= future:
                    events.append({
                        'name': component.get('summary'),
                        'description': truncate_description(component.get('description', 'No description provided')),
                        'start_time': start,
                        'end_time': normalize_date(component.get('dtend').dt),
                        'location': component.get('location', 'MAG Laboratory')
                    })
    return events

async def sync_discord_events(guild):
    """Sync calendar events with Discord events."""
    existing_events = guild.scheduled_events
    calendar_events = fetch_calendar_events()
    calendar_event_names = [event['name'] for event in calendar_events]

    for cal_event in calendar_events:
        discord_event = next((event for event in existing_events if event.name == cal_event['name']), None)
        start_time = cal_event['start_time'].replace(tzinfo=datetime.timezone.utc)
        end_time = cal_event['end_time'].replace(tzinfo=datetime.timezone.utc)

        if discord_event:
            if datetime.datetime.now(datetime.timezone.utc) < discord_event.start_time:
                print(f"Updating Discord event: {cal_event['name']}")
                await discord_event.edit(
                    name=cal_event['name'],
                    description=cal_event['description'],
                    start_time=start_time,
                    end_time=end_time,
                    location=cal_event['location']
                )
            else:
                print(f"Updating ongoing Discord event (except start time): {cal_event['name']}")
                await discord_event.edit(
                    name=cal_event['name'],
                    description=cal_event['description'],
                    end_time=end_time,
                    location=cal_event['location']
                )
        else:
            print(f"Creating new Discord event: {cal_event['name']}")
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
