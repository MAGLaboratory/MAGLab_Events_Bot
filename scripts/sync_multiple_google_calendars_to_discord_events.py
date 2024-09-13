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
import logging

# Setup logging to file and console
logging.basicConfig(
    filename='discord_events_sync.log',
    level=logging.DEBUG,  # Set to DEBUG for detailed logs during troubleshooting
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %I:%M %p'
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)  # Keep console at INFO to reduce verbosity
formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %I:%M %p')
console.setFormatter(formatter)
logging.getLogger().addHandler(console)

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
                    logging.error(f"Error parsing UNTIL value: {until_value}")
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
                response = requests.get(url)
                response.raise_for_status()  # Raise an error for bad status codes
                calendar = Calendar.from_ical(response.content)
                for component in calendar.walk():
                    if component.name == "VEVENT":
                        start = normalize_date(component.get('dtstart').dt)
                        end = normalize_date(component.get('dtend').dt)
                        timezone = start.timezone if start.timezone else pendulum.timezone('UTC')
                        start = start.in_tz(timezone)
                        end = end.in_tz(timezone)

                        if component.get('rrule'):
                            rrule_str = adjust_rrule_for_utc(
                                component.get('rrule').to_ical().decode('utf-8'), start)
                            try:
                                rule = rrulestr(rrule_str, dtstart=start)
                                occurrences = rule.between(
                                    now.in_tz(timezone), future.in_tz(timezone))
                            except ValueError as e:
                                logging.error(f"RRULE error in {component.get('summary')}: {e}")
                                continue
                            for occ in occurrences:
                                events.append({
                                    'name': component.get('summary').strip(),
                                    'description': truncate_description(component.get('description', 'No description provided').strip()),
                                    'start_time': pendulum.instance(occ, tz='UTC').replace(microsecond=0, second=0),
                                    'end_time': pendulum.instance(occ + (end - start), tz='UTC').replace(microsecond=0, second=0),
                                    'location': component.get('location', 'MAG Laboratory').strip()
                                })
                        elif now <= start <= future:
                            events.append({
                                'name': component.get('summary').strip(),
                                'description': truncate_description(component.get('description', 'No description provided').strip()),
                                'start_time': start.in_tz('UTC').replace(microsecond=0, second=0),
                                'end_time': end.in_tz('UTC').replace(microsecond=0, second=0),
                                'location': component.get('location', 'MAG Laboratory').strip()
                            })
            except requests.RequestException as e:
                logging.error(f"HTTP error fetching events from {url}: {e}")
                traceback.print_exc()
            except Exception as e:
                logging.error(f"Error parsing events from {url}: {e}")
                traceback.print_exc()
    except Exception as e:
        logging.error(f"Error in fetch_calendar_events: {e}")
        traceback.print_exc()
    return events

def find_matching_discord_event(discord_events, cal_event):
    """Find a matching Discord event by name, description, start_time, end_time, and location."""
    try:
        cal_start_time = cal_event['start_time'].in_timezone('UTC').replace(microsecond=0, second=0)
        cal_end_time = cal_event['end_time'].in_timezone('UTC').replace(microsecond=0, second=0)
        cal_description = cal_event.get('description', '').strip()
        cal_location = cal_event.get('location', 'MAG Laboratory').strip()

        logging.debug(f"Comparing Calendar Event: Name='{cal_event['name']}', Start='{cal_start_time}', End='{cal_end_time}', Description='{cal_description}', Location='{cal_location}'")

        for event in discord_events:
            event_start_time = pendulum.instance(event.start_time).in_timezone('UTC').replace(microsecond=0, second=0)
            event_end_time = pendulum.instance(event.end_time).in_timezone('UTC').replace(microsecond=0, second=0)
            event_description = (event.description or '').strip()
            event_location = (event.location or 'MAG Laboratory').strip()

            logging.debug(f"Against Discord Event: Name='{event.name}', Start='{event_start_time}', End='{event_end_time}', Description='{event_description}', Location='{event_location}'")

            if (
                event.name.strip() == cal_event['name'] and
                event_description == cal_description and
                event_start_time == cal_start_time and
                event_end_time == cal_end_time and
                event_location == cal_location
            ):
                logging.debug(f"Match found for event '{cal_event['name']}'")
                return event
    except Exception as e:
        logging.error(f"Error finding matching event: {e}")
        traceback.print_exc()
    return None

async def sync_discord_events(guild):
    """Sync calendar events with Discord events."""
    try:
        existing_events = await guild.fetch_scheduled_events()  # Fetch fresh list of scheduled events
        calendar_events = fetch_calendar_events()

        # Create a set of event keys from calendar events for easy lookup
        calendar_event_keys = set()
        for cal_event in calendar_events:
            key = (
                cal_event['name'],
                cal_event.get('description', ''),
                cal_event['start_time'],
                cal_event['end_time'],
                cal_event.get('location', 'MAG Laboratory')
            )
            calendar_event_keys.add(key)

        # Create or update events
        for cal_event in calendar_events:
            discord_event = find_matching_discord_event(existing_events, cal_event)
            start_time = cal_event['start_time']
            end_time = cal_event['end_time']
            la_time = start_time.in_tz(LA_TZ).to_datetime_string()

            try:
                if discord_event:
                    logging.info(
                        f"Exact duplicate found for {cal_event['name']} (LA time: {la_time}). No new event created."
                    )
                else:
                    logging.info(
                        f"Creating new Discord event: {cal_event['name']} (LA time: {la_time})"
                    )
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
                logging.error(f"Error syncing event '{cal_event['name']}': {e}")
                traceback.print_exc()

        # Remove events not in the calendar
        for discord_event in existing_events:
            try:
                event_start_time = pendulum.instance(discord_event.start_time).in_timezone('UTC').replace(microsecond=0, second=0)
                event_end_time = pendulum.instance(discord_event.end_time).in_timezone('UTC').replace(microsecond=0, second=0)
                event_description = (discord_event.description or '').strip()
                event_location = (discord_event.location or 'MAG Laboratory').strip()
                event_key = (
                    discord_event.name.strip(),
                    event_description,
                    event_start_time,
                    event_end_time,
                    event_location
                )
                if event_key not in calendar_event_keys and "We are" not in discord_event.name:
                    la_event_time = pendulum.instance(discord_event.start_time).in_timezone('America/Los_Angeles').to_datetime_string()
                    logging.info(
                        f"Removing Discord event: {discord_event.name} (LA time: {la_event_time})"
                    )
                    await discord_event.delete()
            except Exception as e:
                logging.error(f"Error removing Discord event '{discord_event.name}': {e}")
                traceback.print_exc()
    except Exception as e:
        logging.error(f"Error in sync_discord_events: {e}")
        traceback.print_exc()

@tasks.loop(hours=1)
async def sync_events_task():
    """Sync events every hour, ensuring recovery from errors."""
    try:
        guild = discord.utils.get(client.guilds, id=GUILD_ID)
        if guild:
            await sync_discord_events(guild)
        else:
            logging.info("Guild not found!")
    except Exception as e:
        logging.error(f"Error in sync_events_task: {e}")
        traceback.print_exc()

@sync_events_task.error
async def sync_events_task_error(error):
    logging.error(f"Error in sync_events_task: {error}")
    traceback.print_exc()

@client.event
async def on_ready():
    """Start syncing once the bot is ready."""
    logging.info(f'Logged in as {client.user}')
    if not sync_events_task.is_running():
        sync_events_task.start()
    else:
        logging.info("sync_events_task is already running.")

@client.event
async def on_disconnect():
    logging.warning("Bot disconnected!")

@client.event
async def on_resumed():
    logging.info("Bot resumed connection!")

@client.event
async def on_error(event, *args, **kwargs):
    logging.error(f"Error in event '{event}':")
    traceback.print_exc()

try:
    client.run(DISCORD_TOKEN)
except Exception as e:
    logging.error(f"Error running Discord client: {e}")
    traceback.print_exc()
