import discord
import requests
import datetime
import re
import html
import pendulum
from icalendar import Calendar, Event
from dateutil.rrule import rrulestr
from discord.ext import tasks, commands
import traceback
import logging

# Setup logging to file and console
logging.basicConfig(
    filename='discord_events_sync.log',
    level=logging.INFO,  # Set to INFO for cleaner logs
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %I:%M %p'
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %I:%M %p')
console.setFormatter(formatter)
logging.getLogger().addHandler(console)

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
DESCRIPTION_MAX_LENGTH = 1000

LA_TZ = pendulum.timezone('America/Los_Angeles')  # Timezone for Los Angeles

intents = discord.Intents.default()
intents.guilds = True
#intents.scheduled_events = True  # Ensure the bot has access to scheduled events
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
    """Fetch and return calendar events and canceled events for the next SYNC_DAYS."""
    events = []
    canceled_events = []
    try:
        now = pendulum.now('UTC')
        future = now.add(days=SYNC_DAYS)

        for url in ICS_URLS:
            try:
                response = requests.get(url)
                response.raise_for_status()
                calendar = Calendar.from_ical(response.content)

                # Dictionaries to hold exceptions and cancellations
                exceptions = {}
                cancellations = {}

                for component in calendar.walk():
                    if component.name == "VEVENT":
                        status = str(component.get('status', '')).upper()
                        uid = str(component.get('uid'))
                        recurrence_id = component.get('recurrence-id')
                        if recurrence_id:
                            # This is an exception or cancellation of a recurring event
                            rec_id = normalize_date(recurrence_id.dt)
                            if status == 'CANCELLED':
                                cancellations.setdefault(uid, set()).add(rec_id)
                            else:
                                exceptions.setdefault(uid, []).append(component)
                            continue
                        elif status == 'CANCELLED':
                            # Entire event is cancelled
                            cancellations[uid] = 'ALL'
                            continue

                for component in calendar.walk():
                    if component.name != "VEVENT":
                        continue  # Skip non-VEVENT components

                    uid = str(component.get('uid'))
                    status = str(component.get('status', '')).upper()

                    # Skip entirely canceled events
                    if uid in cancellations and cancellations[uid] == 'ALL':
                        continue

                    start = normalize_date(component.get('dtstart').dt)
                    end = normalize_date(component.get('dtend').dt)
                    timezone = start.timezone if start.timezone else pendulum.timezone('UTC')
                    start = start.in_tz(timezone)
                    end = end.in_tz(timezone)

                    summary = component.get('summary').strip()
                    description = truncate_description(component.get('description', 'No description provided').strip())
                    location = component.get('location', 'MAG Laboratory').strip()

                    if component.get('rrule'):
                        # Handle recurring events
                        rrule_str = adjust_rrule_for_utc(
                            component.get('rrule').to_ical().decode('utf-8'), start)
                        try:
                            rule = rrulestr(rrule_str, dtstart=start)
                            occurrences = rule.between(
                                now.in_tz(timezone), future.in_tz(timezone))
                        except ValueError as e:
                            logging.error(f"RRULE error in {summary}: {e}")
                            continue
                        for occ in occurrences:
                            occ_start = pendulum.instance(occ, tz=timezone).replace(microsecond=0, second=0)
                            occ_end = occ_start + (end - start)
                            rec_id = occ_start

                            # Check for cancellations
                            if uid in cancellations and rec_id in cancellations[uid]:
                                canceled_events.append({
                                    'uid': uid,
                                    'name': summary,
                                    'description': description,
                                    'start_time': occ_start.in_tz('UTC'),
                                    'end_time': occ_end.in_tz('UTC'),
                                    'location': location
                                })
                                continue  # Skip this occurrence as it's cancelled

                            # Apply exceptions
                            if uid in exceptions:
                                for ex in exceptions[uid]:
                                    ex_recurrence_id = normalize_date(ex.get('recurrence-id').dt)
                                    if ex_recurrence_id == occ_start:
                                        # Override with exception event
                                        ex_summary = ex.get('summary', summary).strip()
                                        ex_description = truncate_description(ex.get('description', description).strip())
                                        ex_location = ex.get('location', location).strip()
                                        # Append exception event
                                        events.append({
                                            'uid': uid,
                                            'name': ex_summary,
                                            'description': ex_description,
                                            'start_time': occ_start.in_tz('UTC'),
                                            'end_time': (pendulum.instance(ex.get('dtend').dt, tz=timezone)).in_tz('UTC').replace(microsecond=0, second=0),
                                            'location': ex_location
                                        })
                                        break
                                else:
                                    # No exception matches, use original
                                    events.append({
                                        'uid': uid,
                                        'name': summary,
                                        'description': description,
                                        'start_time': occ_start.in_tz('UTC'),
                                        'end_time': occ_end.in_tz('UTC'),
                                        'location': location
                                    })
                            else:
                                # No exceptions, add event as is
                                events.append({
                                    'uid': uid,
                                    'name': summary,
                                    'description': description,
                                    'start_time': occ_start.in_tz('UTC'),
                                    'end_time': occ_end.in_tz('UTC'),
                                    'location': location
                                })
                    else:
                        # Non-recurring event
                        if now <= end <= future:
                            if status == 'CANCELLED':
                                canceled_events.append({
                                    'uid': uid,
                                    'name': summary,
                                    'description': description,
                                    'start_time': start.in_tz('UTC'),
                                    'end_time': end.in_tz('UTC'),
                                    'location': location
                                })
                                continue  # Skip as it's cancelled

                            events.append({
                                'uid': uid,
                                'name': summary,
                                'description': description,
                                'start_time': start.in_tz('UTC'),
                                'end_time': end.in_tz('UTC'),
                                'location': location
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
    return events, canceled_events

def find_matching_discord_event(discord_events, cal_event):
    """Find a matching Discord event by name, start_time, and location."""
    try:
        cal_start_time = cal_event['start_time']
        cal_name = cal_event['name']
        cal_location = cal_event.get('location', 'MAG Laboratory')

        for event in discord_events:
            # Skip events that have already ended
            if event.status == discord.EventStatus.completed:
                continue

            event_start_time = pendulum.instance(event.start_time).in_timezone('UTC').replace(microsecond=0, second=0)
            event_name = event.name
            event_location = (event.location or 'MAG Laboratory').strip()

            if (
                event_name == cal_name and
                event_start_time == cal_start_time and
                event_location == cal_location
            ):
                return event
    except Exception as e:
        logging.error(f"Error finding matching event: {e}")
        traceback.print_exc()
    return None

async def sync_discord_events(guild):
    """Sync calendar events with Discord events."""
    try:
        existing_events = await guild.fetch_scheduled_events()
        calendar_events, canceled_events = fetch_calendar_events()

        # Create a set of event keys from calendar events for easy lookup
        calendar_event_keys = set()
        for cal_event in calendar_events:
            key = (
                cal_event['name'],
                cal_event['start_time'],
                cal_event.get('location', 'MAG Laboratory')
            )
            calendar_event_keys.add(key)

        # Create or update events
        for cal_event in calendar_events:
            discord_event = find_matching_discord_event(existing_events, cal_event)
            start_time = cal_event['start_time']
            la_time = start_time.in_tz(LA_TZ).to_datetime_string()

            try:
                if discord_event:
                    # Exact duplicate found; log and do not create a new event
                    logging.info(
                        f"Exact duplicate found for '{cal_event['name']}' (Start Time: {la_time}). No new event created."
                    )
                    continue  # Skip creating a new event
                else:
                    # Create new event
                    logging.info(
                        f"Creating event '{cal_event['name']}' at {la_time}"
                    )
                    await guild.create_scheduled_event(
                        name=cal_event['name'],
                        description=cal_event['description'],
                        start_time=start_time,
                        end_time=cal_event['end_time'],
                        entity_type=discord.EntityType.external,
                        location=cal_event['location'],
                        privacy_level=discord.PrivacyLevel.guild_only
                    )
            except Exception as e:
                logging.error(f"Error syncing event '{cal_event['name']}': {e}")
                traceback.print_exc()

        # Remove canceled events
        for cal_event in canceled_events:
            discord_event = find_matching_discord_event(existing_events, cal_event)
            if discord_event:
                start_time = cal_event['start_time']
                la_time = start_time.in_tz(LA_TZ).to_datetime_string()
                try:
                    logging.info(
                        f"Removing canceled event '{cal_event['name']}' scheduled at {la_time}"
                    )
                    await discord_event.delete()
                except Exception as e:
                    logging.error(f"Error deleting event '{cal_event['name']}': {e}")
                    traceback.print_exc()

        # Remove events not in the calendar and not currently occurring
        now = pendulum.now('UTC')
        for discord_event in existing_events:
            try:
                # Skip events that are currently occurring
                event_start_time = pendulum.instance(discord_event.start_time).in_timezone('UTC')
                event_end_time = pendulum.instance(discord_event.end_time).in_timezone('UTC')

                if event_start_time <= now <= event_end_time:
                    continue  # Do not delete ongoing events

                event_name = discord_event.name
                event_location = (discord_event.location or 'MAG Laboratory').strip()
                event_key = (
                    event_name,
                    event_start_time.replace(microsecond=0, second=0),
                    event_location
                )
                if event_key not in calendar_event_keys and "We are" not in event_name:
                    la_event_time = event_start_time.in_tz(LA_TZ).to_datetime_string()
                    logging.info(
                        f"Removing event '{event_name}' scheduled at {la_event_time} not found in calendar"
                    )
                    try:
                        await discord_event.delete()
                    except discord.errors.HTTPException as e:
                        logging.error(f"Error deleting event '{event_name}': {e}")
                        traceback.print_exc()
            except Exception as e:
                logging.error(f"Error processing event '{discord_event.name}': {e}")
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
