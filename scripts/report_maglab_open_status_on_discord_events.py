import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from typing import List, Optional

import discord
from discord.ext import tasks, commands
import requests
from bs4 import BeautifulSoup
import pytz
import pandas as pd

from scrape_synoptic_view_and_crop_scale_for_discord_events import (
    generate_scaled_cropped_synoptic_view_image,
)

"""
MAGLAB Open-Status Bot (OPEN-only) — Single Synoptic Image Policy
-----------------------------------------------------------------
- Only reports when the shop is OPEN.
- **Single-image policy**: Ensure there is exactly one event carrying the
  synoptic status image — the current event if any, otherwise the next
  upcoming event. Runs every 5 minutes.
- Robust status parsing, timestamp fixes, shard handlers retained.
"""

# Constants
TOKEN_FILE = 'discord_token.txt'
GUILD_ID = 697971426799517774
LAB_URL = "https://www.maglaboratory.org/hal"
SCALED_PNG_FILE = 'maglab_synoptic_view_scaled.png'
PACIFIC_TZ = pytz.timezone('America/Los_Angeles')

# Configure logging
logger = logging.getLogger('discord_bot')
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

file_handler = RotatingFileHandler(
    'open_status_switch.log', maxBytes=5 * 1024 * 1024, backupCount=5
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Initialize the Discord bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)


def get_discord_token() -> Optional[str]:
    try:
        with open(TOKEN_FILE, 'r') as token_file:
            return token_file.read().strip()
    except FileNotFoundError:
        logger.error(f"'{TOKEN_FILE}' not found.")
        return None


TOKEN = get_discord_token()
if not TOKEN:
    logger.critical("Discord token is missing. Exiting the bot.")
    raise SystemExit("Discord token is missing.")


def fetch_lab_status_and_sensors(url: str):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching the webpage: {e}")
        return None, None, None

    soup = BeautifulSoup(response.text, 'html.parser')
    page_text_lower = soup.get_text().lower()

    if 'we are open' in page_text_lower:
        lab_status = 'We are OPEN'
    elif 'we are closed' in page_text_lower:
        lab_status = 'We are CLOSED'
    else:
        lab_status = 'We are OPEN' if ('open' in page_text_lower and 'closed' not in page_text_lower) else 'We are CLOSED'

    sensor_data = []
    sensor_table = soup.find('table')
    if sensor_table:
        for row in sensor_table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) == 4:
                sensor_name = cells[0].get_text(strip=True)
                status = truncate_status(cells[1].get_text(strip=True))
                if sensor_name not in ["Page Loaded", "Auto Refresh"]:
                    sensor_data.append(
                        {
                            'Sensor': sensor_name,
                            'Status': status,
                            'Last Update': format_last_update(
                                cells[3].get_text(strip=True)
                            ),
                        }
                    )

    scrape_timestamp = datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d %I:%M %p %Z")
    return lab_status, sensor_data, scrape_timestamp


def truncate_status(status: str) -> str:
    if "°F" in status:
        parts = status.split("/")
        if len(parts) > 1:
            return parts[1].strip()
    return status.replace("No Movement", "No Motion")


def format_last_update(timestamp_str: str) -> str:
    timestamp_str = timestamp_str.rsplit(' ', 1)[0]
    timestamp_format = "%b %d, %Y, %I:%M %p"
    try:
        timestamp = datetime.strptime(timestamp_str, timestamp_format)
        localized_timestamp = PACIFIC_TZ.localize(timestamp)
        time_diff = datetime.now(PACIFIC_TZ) - localized_timestamp

        if time_diff < timedelta(minutes=1):
            return "Just now"
        elif time_diff < timedelta(hours=1):
            return f"{int(time_diff.total_seconds() // 60)} min ago"
        elif time_diff < timedelta(days=1):
            return f"{int(time_diff.total_seconds() // 3600)} hr ago"
        return f"{time_diff.days} days ago"
    except Exception as e:
        logger.error(f"Error parsing timestamp: {e}")
        return "Unknown"


def format_sensor_data(lab_status: str, sensor_data: List[dict], scrape_timestamp: str, url: str) -> str:
    df = pd.DataFrame(sensor_data)
    table_string = df.to_string(index=False)
    return (
        f"**Lab Status:** {lab_status}\n"
        f"**Data Scraped on:** {scrape_timestamp}\n"
        f"[Source: {url}]\n\n"
        f"**Sensor Data:**\n```\n{table_string}\n```"
    )


def get_image_as_binary(image_path: str) -> Optional[bytes]:
    try:
        with open(image_path, 'rb') as img_file:
            return img_file.read()
    except FileNotFoundError:
        logger.error(f"Image file '{image_path}' not found.")
        return None


async def delete_all_we_are_events(guild: discord.Guild) -> None:
    try:
        existing_events = await guild.fetch_scheduled_events()
        to_delete = [e for e in existing_events if 'We are' in (e.name or '')]
        for event in to_delete:
            try:
                await event.delete()
                logger.info(
                    f"Deleted 'We are' event: {event.name}, Start Time: {event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
                )
            except Exception as e:
                logger.error(f"Error deleting 'We are' event '{event.name}': {e}")
    except Exception as e:
        logger.error(f"Error while listing/deleting 'We are' events: {e}")


async def manage_lab_status_event(
    guild: discord.Guild,
    lab_status: str,
    formatted_message: str,
) -> None:
    """Create/update the short 'We are OPEN' event window (no image here)."""
    try:
        now = datetime.now().astimezone()
        event_end_time = now + timedelta(minutes=10)

        existing_events = await guild.fetch_scheduled_events()
        we_are_events = [e for e in existing_events if 'We are' in (e.name or '')]

        if len(we_are_events) > 1:
            for event in we_are_events[1:]:
                await event.delete()
                logger.info(
                    f"Deleted extra 'We are' event: {event.name}, Start Time: {event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
                )
        existing_event = we_are_events[0] if we_are_events else None

        if existing_event and existing_event.end_time > now:
            try:
                await existing_event.edit(
                    name=lab_status,
                    description=formatted_message,
                    end_time=event_end_time,
                )
                logger.info(
                    f"Updated event: {existing_event.name}, Start Time: {existing_event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
                )
                return
            except discord.errors.Forbidden as e:
                logger.error(f"Cannot update event: {e}")
                try:
                    await existing_event.delete()
                    logger.info(
                        f"Deleted non-updatable 'We are' event: {existing_event.name}, Start Time: {existing_event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
                    )
                except Exception as de:
                    logger.error(f"Error deleting non-updatable event: {de}")
                existing_event = None
        else:
            if existing_event:
                try:
                    await existing_event.delete()
                    logger.info(
                        f"Deleted finished 'We are' event: {existing_event.name}, Start Time: {existing_event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
                    )
                except Exception as de:
                    logger.error(f"Error deleting finished event: {de}")
                existing_event = None

        new_event = await guild.create_scheduled_event(
            name=lab_status,
            description=formatted_message,
            start_time=now + timedelta(seconds=10),
            end_time=event_end_time,
            entity_type=discord.EntityType.external,
            location="MAG Laboratory",
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        logger.info(
            f"Created new event: {new_event.name}, Start Time: {new_event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
        )

    except Exception as e:
        logger.error(f"Error managing 'We are' event: {e}", exc_info=True)


async def pick_target_event_for_synoptic(guild: discord.Guild) -> Optional[discord.ScheduledEvent]:
    """Priority: current non-'We are' ➜ current 'We are' ➜ next upcoming."""
    try:
        now = datetime.now().astimezone()
        events = await guild.fetch_scheduled_events()
        events = [e for e in events if e.status != discord.EventStatus.completed]

        active_non_we = [e for e in events if 'We are' not in (e.name or '') and e.start_time <= now <= e.end_time]
        if active_non_we:
            active_non_we.sort(key=lambda e: e.end_time)
            return active_non_we[0]

        active_we = [e for e in events if 'We are' in (e.name or '') and e.start_time <= now <= e.end_time]
        if active_we:
            active_we.sort(key=lambda e: e.end_time)
            return active_we[0]

        future = [e for e in events if e.start_time > now]
        if future:
            future.sort(key=lambda e: e.start_time)
            return future[0]
    except Exception as e:
        logger.error(f"Error picking target event for synoptic: {e}")
    return None


async def enforce_single_synoptic_image(guild: discord.Guild) -> None:
    try:
        generate_scaled_cropped_synoptic_view_image(output_png_file=SCALED_PNG_FILE)
        image_binary = get_image_as_binary(SCALED_PNG_FILE)
        if image_binary is None:
            logger.warning("Synoptic image not available; skipping image enforcement.")
            return

        target = await pick_target_event_for_synoptic(guild)
        events = await guild.fetch_scheduled_events()

        for e in events:
            if e.status == discord.EventStatus.completed:
                continue
            try:
                if target and e.id == target.id:
                    await e.edit(image=image_binary)
                    logger.info(f"Synoptic image set on: {e.name}, Start Time: {e.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}")
                else:
                    await e.edit(image=None)
            except Exception as ie:
                logger.error(f"Error editing image on '{e.name}': {ie}")
    except Exception as e:
        logger.error(f"Error enforcing single synoptic image: {e}")


async def check_for_other_active_events(guild: discord.Guild) -> bool:
    try:
        now = datetime.now().astimezone()
        events = await guild.fetch_scheduled_events()
        for event in events:
            if event.start_time <= now <= event.end_time and 'We are' not in (event.name or ''):
                logger.info(
                    f"Another active event detected: {event.name}, Start Time: {event.start_time.astimezone().strftime('%Y-%m-%d %I:%M %p')}"
                )
                return True
        return False
    except Exception as e:
        logger.error(f"Error checking for other active events: {e}")
        return False


@tasks.loop(minutes=5)
async def post_lab_status() -> None:
    try:
        lab_status, sensor_data, scrape_timestamp = fetch_lab_status_and_sensors(LAB_URL)
        if lab_status is None:
            logger.warning("Failed to scrape lab status (None returned).")
            return

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            logger.error(f"Guild with ID {GUILD_ID} not found.")
            return

        if lab_status != 'We are OPEN':
            await delete_all_we_are_events(guild)
            await enforce_single_synoptic_image(guild)
            logger.info("Shop is CLOSED — not reporting. Enforced single synoptic image.")
            return

        if not sensor_data:
            logger.warning("Sensor data empty; skipping update.")
            await enforce_single_synoptic_image(guild)
            return

        if await check_for_other_active_events(guild):
            await delete_all_we_are_events(guild)
            await enforce_single_synoptic_image(guild)
            logger.info("Another event is active. Not creating 'We are' event. Image enforced.")
            return

        formatted_message = format_sensor_data(lab_status, sensor_data, scrape_timestamp, LAB_URL)
        await manage_lab_status_event(guild, lab_status, formatted_message)
        await enforce_single_synoptic_image(guild)

    except Exception as e:
        logger.error(f"Error in post_lab_status: {e}", exc_info=True)


@post_lab_status.before_loop
async def before_post_lab_status():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    logger.info(f"Bot {bot.user.name} has connected to Discord.")
    if not post_lab_status.is_running():
        post_lab_status.start()


@bot.event
async def on_disconnect():
    logger.warning(f"Bot {bot.user.name} has disconnected, attempting to reconnect...")


@bot.event
async def on_resumed():
    logger.info(f"Bot {bot.user.name} has reconnected to Discord.")
    if not post_lab_status.is_running():
        post_lab_status.start()


@bot.event
async def on_shard_disconnect(shard_id):
    logger.warning(f"Shard {shard_id} disconnected.")


@bot.event
async def on_shard_connect(shard_id):
    logger.info(f"Shard {shard_id} reconnected.")
    if not post_lab_status.is_running():
        post_lab_status.start()


@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.error(f"Error in {event_method}: {args}, {kwargs}", exc_info=True)


try:
    bot.run(TOKEN)
except Exception as e:
    logger.critical(f"Critical error running the bot: {e}", exc_info=True)
