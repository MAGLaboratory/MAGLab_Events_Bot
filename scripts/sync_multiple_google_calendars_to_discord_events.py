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
from typing import Optional

from scrape_synoptic_view_and_crop_scale_for_discord_events import (
    generate_scaled_cropped_synoptic_view_image,
)

"""
Google Calendar ➜ Discord Events Sync — Single Synoptic Image Policy
--------------------------------------------------------------------
- Does NOT attach the synoptic image per-event anymore.
- After syncing creates/updates/deletes, it enforces **exactly one** synoptic
  image across all scheduled events — on the current event (if any) or the next
  upcoming event. All others are cleared.
- Keeps cancellation handling, duplicate avoidance, and 'We are' protections.
"""

# Setup logging to file and console
logging.basicConfig(
    filename='discord_events_sync.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %I:%M %p'
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %I:%M %p')
console.setFormatter(formatter)
logging.getLogger().addHandler(console)

# Read the Discord token from a file named 'discord_token.txt'
def get_discord_token():
    with open('discord_token.txt', 'r') as file:
        return file.read().strip()

DISCORD_TOKEN = get_discord_token()
GUILD_ID = 697971426799517774

# ICS URLs to sync
ICS_URLS = [
    'https://calendar.google.com/calendar/ical/c_3keov3j3lc5qscq754mb4n38b4%40group.calendar.google.com/public/basic.ics',
    'https://calendar.google.com/calendar/ical/bjpkvaeg1rjq9u3c6utecq1jos%40group.calendar.google.com/public/basic.ics',
]

SYNC_DAYS = 7
DESCRIPTION_MAX_LENGTH = 1000
LA_TZ = pendulum.timezone('America/Los_Angeles')

SYNOPTIC_PNG_PATH = 'maglab_synoptic_view_scaled.png'

intents = discord.Intents.default()
intents.guilds = True
client = commands.Bot(command_prefix="!", intents=intents)


def normalize_date(dt):
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        dt = pendulum.datetime(dt.year, dt.month, dt.day, tz='UTC')
    elif getattr(dt, 'tzinfo', None) is None:
        dt = pendulum.instance(dt, tz='UTC')
    else:
        dt = pendulum.instance(dt)
    return dt


def adjust_rrule_for_utc(rrule_str, start):
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


def clean_description(description: str) -> str:
    description = re.sub(r'<[^>]+>', '', description)
    return html.unescape(description)


def truncate_description(description: str) -> str:
    clean_desc = clean_description(description)
    return clean_desc[:DESCRIPTION_MAX_LENGTH] if len(clean_desc) > DESCRIPTION_MAX_LENGTH else clean_desc


def fetch_calendar_events():
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

                exceptions = {}
                cancellations = {}

                for component in calendar.walk():
                    if component.name == "VEVENT":
                        status = str(component.get('status', '')).upper()
                        uid = str(component.get('uid'))
                        recurrence_id = component.get('recurrence-id')
                        if recurrence_id:
                            rec_id = normalize_date(recurrence_id.dt)
                            if status == 'CANCELLED':
                                cancellations.setdefault(uid, set()).add(rec_id)
                            else:
                                exceptions.setdefault(uid, []).append(component)
                            continue
                        elif status == 'CANCELLED':
                            cancellations[uid] = 'ALL'
                            continue

                for component in calendar.walk():
                    if component.name != "VEVENT":
                        continue

                    uid = str(component.get('uid'))
                    status = str(component.get('status', '')).upper()

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
                        rrule_str = adjust_rrule_for_utc(component.get('rrule').to_ical().decode('utf-8'), start)
                        try:
                            rule = rrulestr(rrule_str, dtstart=start)
                            occurrences = rule.between(now.in_tz(timezone), future.in_tz(timezone))
                        except ValueError as e:
                            logging.error(f"RRULE error in {summary}: {e}")
                            continue
                        for occ in occurrences:
                            occ_start = pendulum.instance(occ, tz=timezone).replace(microsecond=0, second=0)
                            occ_end = occ_start + (end - start)
                            rec_id = occ_start

                            if uid in cancellations and rec_id in cancellations[uid]:
                                canceled_events.append({
                                    'uid': uid,
                                    'name': summary,
                                    'description': description,
                                    'start_time': occ_start.in_tz('UTC'),
                                    'end_time': occ_end.in_tz('UTC'),
                                    'location': location,
                                })
                                continue

                            if uid in exceptions:
                                matched_exception = None
                                for ex in exceptions[uid]:
                                    ex_recurrence_id = normalize_date(ex.get('recurrence-id').dt)
                                    if ex_recurrence_id == occ_start:
                                        matched_exception = ex
                                        break
                                if matched_exception:
                                    ex_summary = matched_exception.get('summary', summary).strip()
                                    ex_description = truncate_description(matched_exception.get('description', description).strip())
                                    ex_location = matched_exception.get('location', location).strip()
                                    ex_end = pendulum.instance(matched_exception.get('dtend').dt, tz=timezone)
                                    events.append({
                                        'uid': uid,
                                        'name': ex_summary,
                                        'description': ex_description,
                                        'start_time': occ_start.in_tz('UTC'),
                                        'end_time': ex_end.in_tz('UTC').replace(microsecond=0, second=0),
                                        'location': ex_location,
                                    })
                                else:
                                    events.append({
                                        'uid': uid,
                                        'name': summary,
                                        'description': description,
                                        'start_time': occ_start.in_tz('UTC'),
                                        'end_time': occ_end.in_tz('UTC'),
                                        'location': location,
                                    })
                            else:
                                events.append({
                                    'uid': uid,
                                    'name': summary,
                                    'description': description,
                                    'start_time': occ_start.in_tz('UTC'),
                                    'end_time': occ_end.in_tz('UTC'),
                                    'location': location,
                                })
                    else:
                        if now <= end <= future:
                            if status == 'CANCELLED':
                                canceled_events.append({
                                    'uid': uid,
                                    'name': summary,
                                    'description': description,
                                    'start_time': start.in_tz('UTC'),
                                    'end_time': end.in_tz('UTC'),
                                    'location': location,
                                })
                                continue
                            events.append({
                                'uid': uid,
                                'name': summary,
                                'description': description,
                                'start_time': start.in_tz('UTC'),
                                'end_time': end.in_tz('UTC'),
                                'location': location,
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
    try:
        cal_start_time = cal_event['start_time']
        cal_name = cal_event['name']
        cal_location = cal_event.get('location', 'MAG Laboratory')

        for event in discord_events:
            if event.status == discord.EventStatus.completed:
                continue
            event_start_time = pendulum.instance(event.start_time).in_timezone('UTC').replace(microsecond=0, second=0)
            event_name = event.name
            event_location = (event.location or 'MAG Laboratory').strip()
            if event_name == cal_name and event_start_time == cal_start_time and event_location == cal_location:
                return event
    except Exception as e:
        logging.error(f"Error finding matching event: {e}")
        traceback.print_exc()
    return None


def get_synoptic_image_bytes() -> Optional[bytes]:
    try:
        generate_scaled_cropped_synoptic_view_image(SYNOPTIC_PNG_PATH)
        with open(SYNOPTIC_PNG_PATH, 'rb') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Error generating/loading synoptic image: {e}")
        return None


async def pick_target_event_for_synoptic(guild: discord.Guild):
    try:
        now = pendulum.now('UTC').in_timezone('UTC')
        events = await guild.fetch_scheduled_events()
        events = [e for e in events if e.status != discord.EventStatus.completed]

        def to_utc(dt):
            return pendulum.instance(dt).in_timezone('UTC')

        active_non_we = [e for e in events if 'We are' not in (e.name or '') and to_utc(e.start_time) <= now <= to_utc(e.end_time)]
        if active_non_we:
            active_non_we.sort(key=lambda e: to_utc(e.end_time))
            return active_non_we[0]

        active_we = [e for e in events if 'We are' in (e.name or '') and to_utc(e.start_time) <= now <= to_utc(e.end_time)]
        if active_we:
            active_we.sort(key=lambda e: to_utc(e.end_time))
            return active_we[0]

        future = [e for e in events if to_utc(e.start_time) > now]
        if future:
            future.sort(key=lambda e: to_utc(e.start_time))
            return future[0]
    except Exception as e:
        logging.error(f"Error picking target event for synoptic: {e}")
    return None


async def enforce_single_synoptic_image(guild: discord.Guild):
    try:
        image_bytes = get_synoptic_image_bytes()
        if image_bytes is None:
            logging.warning("Synoptic image unavailable; skipping enforcement.")
            return

        target = await pick_target_event_for_synoptic(guild)
        events = await guild.fetch_scheduled_events()

        for e in events:
            if e.status == discord.EventStatus.completed:
                continue
            try:
                if target and e.id == target.id:
                    await e.edit(image=image_bytes)
                    la_time = pendulum.instance(e.start_time).in_timezone(LA_TZ).to_datetime_string()
                    logging.info(f"Synoptic image set on: '{e.name}' at {la_time}")
                else:
                    await e.edit(image=None)
            except Exception as ie:
                logging.error(f"Error editing image on '{e.name}': {ie}")
    except Exception as e:
        logging.error(f"Error enforcing single synoptic image: {e}")


async def sync_discord_events(guild: discord.Guild):
    try:
        existing_events = await guild.fetch_scheduled_events()
        calendar_events, canceled_events = fetch_calendar_events()

        calendar_event_keys = set()
        for cal_event in calendar_events:
            key = (cal_event['name'], cal_event['start_time'], cal_event.get('location', 'MAG Laboratory'))
            calendar_event_keys.add(key)

        for cal_event in calendar_events:
            discord_event = find_matching_discord_event(existing_events, cal_event)
            start_time = cal_event['start_time']
            la_time = start_time.in_tz(LA_TZ).to_datetime_string()
            try:
                if discord_event:
                    logging.info(f"Updating event '{cal_event['name']}' at {la_time}")
                    try:
                        await discord_event.edit(
                            description=cal_event['description'],
                            end_time=cal_event['end_time'],
                        )
                    except Exception as ue:
                        logging.error(f"Error updating event '{cal_event['name']}': {ue}")
                else:
                    logging.info(f"Creating event '{cal_event['name']}' at {la_time}")
                    await guild.create_scheduled_event(
                        name=cal_event['name'],
                        description=cal_event['description'],
                        start_time=start_time,
                        end_time=cal_event['end_time'],
                        entity_type=discord.EntityType.external,
                        location=cal_event['location'],
                        privacy_level=discord.PrivacyLevel.guild_only,
                    )
            except Exception as e:
                logging.error(f"Error syncing event '{cal_event['name']}': {e}")
                traceback.print_exc()

        for cal_event in canceled_events:
            discord_event = find_matching_discord_event(existing_events, cal_event)
            if discord_event:
                start_time = cal_event['start_time']
                la_time = start_time.in_tz(LA_TZ).to_datetime_string()
                try:
                    logging.info(f"Removing canceled event '{cal_event['name']}' scheduled at {la_time}")
                    await discord_event.delete()
                except Exception as e:
                    logging.error(f"Error deleting event '{cal_event['name']}': {e}")
                    traceback.print_exc()

        now = pendulum.now('UTC')
        for discord_event in existing_events:
            try:
                event_start_time = pendulum.instance(discord_event.start_time).in_timezone('UTC')
                event_end_time = pendulum.instance(discord_event.end_time).in_timezone('UTC')
                if event_start_time <= now <= event_end_time:
                    continue
                event_name = discord_event.name
                event_location = (discord_event.location or 'MAG Laboratory').strip()
                event_key = (event_name, event_start_time.replace(microsecond=0, second=0), event_location)
                if event_key not in calendar_event_keys and "We are" not in event_name:
                    la_event_time = event_start_time.in_tz(LA_TZ).to_datetime_string()
                    logging.info(f"Removing event '{event_name}' scheduled at {la_event_time} not found in calendar")
                    try:
                        await discord_event.delete()
                    except discord.errors.HTTPException as e:
                        logging.error(f"Error deleting event '{event_name}': {e}")
                        traceback.print_exc()
            except Exception as e:
                logging.error(f"Error processing event '{discord_event.name}': {e}")
                traceback.print_exc()

        # Enforce single synoptic image policy at the end of the sync pass
        await enforce_single_synoptic_image(guild)

    except Exception as e:
        logging.error(f"Error in sync_discord_events: {e}")
        traceback.print_exc()


@tasks.loop(hours=1)
async def sync_events_task():
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
