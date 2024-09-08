import discord
import requests
import datetime
import re
import html
import pendulum
from icalendar import Calendar
from dateutil.rrule import rrulestr
from discord.ext import tasks, commands
import traceback

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

def get_timestamp():
    """Get the current local time in the format '[YYYY-MM-DD HH:MM AM/PM]'."""
    return f"[{pendulum.now(LA_TZ).format('YYYY-MM-DD hh:mm A')}]"

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
    """Ensure RRULE UNTIL is in UTC if DTSTART is timezone-aware, and avoid invalid 'Z' insertions."""
    if 'UNTIL' in rrule_str and start.timezone is not None:
        rrule_parts = rrule_str.split(';')
        for i, part in enumerate(rrule_parts):
            if part.startswith('UNTIL='):
                until_value = part.split('=')[1]
                try:
                    until_dt = pendulum.parse(until_value)
                    if until_dt.timezone is not None:
                        until_value = until_dt.in_tz('UTC').strftime('%Y%m%dT%H%M%SZ')
                        rrule_parts[i] = f'UNTIL={until_value}'
                except pendulum.parsing.exceptions.ParserError:
                    print(f"{get_timestamp()} Error parsing UNTIL value: {until_value}")
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
    try:
        now = pendulum.now('UTC')
        future = now.add(days=SYNC_DAYS)

        for url in ICS_URLS:
            try:
                calendar = Calendar.from_ical(requests.get(url).content)
                for component in calendar.walk():
                    if component.name == "VEVENT":
                        start = normalize_date(component.get('dtstart').dt)
                        end = normalize_date(component.get('dtend').dt)
                        timezone = start.timezone if start.timezone else pendulum.timezone('UTC')  # Use local timezone if available
                        start = start.in_tz(timezone)
                        end = end.in_tz(timezone)

                        if component.get('rrule'):
                            rrule_str = adjust_rrule_for_utc(component.get('rrule').to_ical().decode('utf-8'), start)
                            try:
                                rule = rrulestr(rrule_str, dtstart=start)
                                occurrences = rule.between(now.in_tz(timezone), future.in_tz(timezone))
                            except ValueError as e:
                                print(f"{get_timestamp()} RRULE error in {component.get('summary')}: {e}")
                                continue
                            for occ in occurrences:
                                events.append({
                                    'name': component.get('summary'),
                                    'description': truncate_description(component.get('description', 'No description provided')),
                                    'start_time': pendulum.instance(occ, tz='UTC'),
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
            except Exception as e:
                print(f"{get_timestamp()} Error fetching events from {url}: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"{get_timestamp()} Error in fetch_calendar_events: {e}")
        traceback.print_exc()
    return events

def find_matching_discord_event(discord_events, cal_event):
    """Find a matching Discord event by both name and start time."""
    try:
        for event in discord_events:
            if event.name == cal_event['name'] and event.start_time == cal_event['start_time']:
                return event
    except Exception as e:
        print(f"{get_timestamp()} Error finding matching event: {e}")
        traceback.print_exc()
    return None

async def sync_discord_events(guild):
    """Sync calendar events with Discord events."""
    try:
        existing_events = guild.scheduled_events
        calendar_events = fetch_calendar_events()
        calendar_event_names = [event['name'] for event in calendar_events]

        for cal_event in calendar_events:
            discord_event = find_matching_discord_event(existing_events, cal_event)  # Match by name and start time
            start_time = cal_event['start_time']
            end_time = cal_event['end_time']
            la_time = start_time.in_tz(LA_TZ).to_datetime_string()  # Convert to LA time

            try:
                if discord_event:
                    if pendulum.now('UTC') < discord_event.start_time:
                        print(f"{get_timestamp()} Updating Discord event: {cal_event['name']} (LA time: {la_time})")
                        await discord_event.edit(
                            name=cal_event['name'],
                            description=cal_event['description'],
                            start_time=start_time,
                            end_time=end_time,
                            location=cal_event['location']
                        )
                    else:
                        print(f"{get_timestamp()} Updating ongoing Discord event (except start time): {cal_event['name']} (LA time: {la_time})")
                        await discord_event.edit(
                            name=cal_event['name'],
                            description=cal_event['description'],
                            end_time=end_time,
                            location=cal_event['location']
                        )
                else:
                    print(f"{get_timestamp()} Creating new Discord event: {cal_event['name']} (LA time: {la_time})")
                    await guild.create_scheduled_event(
                        name=cal_event['name'],
                        description=cal_event['description'],
                        start_time=start_time,
                        end_time=end_time,
                        entity_type=discord.EntityType.external,
                        location=cal_event['location'],
                        privacy_level=discord.PrivacyLevel.guild_only
                    )
            except Exception as e:
                print(f"{get_timestamp()} Error syncing event {cal_event['name']}: {e}")
                traceback.print_exc()

        for discord_event in existing_events:
            try:
                if discord_event.name not in calendar_event_names and "We are" not in discord_event.name:
                    print(f"{get_timestamp()} Removing Discord event: {discord_event.name} (Not found in calendar)")
                    await discord_event.delete()
            except Exception as e:
                print(f"{get_timestamp()} Error removing Discord event {discord_event.name}: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"{get_timestamp()} Error in sync_discord_events: {e}")
        traceback.print_exc()

@tasks.loop(hours=1)
async def sync_events_task():
    """Sync events every hour, ensuring recovery from errors."""
    try:
        guild = discord.utils.get(client.guilds, id=GUILD_ID)
        if guild:
            await sync_discord_events(guild)
        else:
            print(f"{get_timestamp()} Guild not found!")
    except Exception as e:
        print(f"{get_timestamp()} Error in sync_events_task: {e}")
        traceback.print_exc()

@client.event
async def on_ready():
    """Start syncing once the bot is ready."""
    print(f'{get_timestamp()} Logged in as {client.user}')
    sync_events_task.start()

try:
    client.run(DISCORD_TOKEN)
except Exception as e:
    print(f"{get_timestamp()} Error running Discord client: {e}")
    traceback.print_exc
