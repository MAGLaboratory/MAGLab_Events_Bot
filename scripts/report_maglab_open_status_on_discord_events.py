import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

import discord
from discord.ext import tasks, commands
import requests
from bs4 import BeautifulSoup
import pytz
import pandas as pd

from scrape_synoptic_view_and_crop_scale_for_discord_events import generate_scaled_cropped_synoptic_view_image

# Constants
TOKEN_FILE = 'discord_token.txt'
GUILD_ID = 697971426799517774  # Replace with your actual Guild ID
LAB_URL = "https://www.maglaboratory.org/hal"
SCALED_PNG_FILE = 'maglab_synoptic_view_scaled.png'

# Configure logging
logger = logging.getLogger('discord_bot')
logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture all levels

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Create console handler and set level to INFO
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# Create file handler with rotation, set level to DEBUG
file_handler = RotatingFileHandler(
    'bot.log', maxBytes=5*1024*1024, backupCount=5
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Initialize the Discord bot
intents = discord.Intents.default()
intents.guilds = True  # Ensure guild-related intents are enabled
bot = commands.Bot(command_prefix='!', intents=intents)


def get_discord_token():
    """Retrieve the Discord bot token from a file."""
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


def current_time_str():
    """Get the current local time as a formatted string."""
    return datetime.now().strftime("[%Y-%m-%d %I:%M %p]")


def fetch_lab_status_and_sensors(url):
    """Scrape lab status and sensor data from the webpage."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching the webpage: {e}")
        return None, None, None

    soup = BeautifulSoup(response.text, 'html.parser')
    page_text = soup.get_text().lower()

    # Determine lab status
    lab_status = (
        "We are OPEN"
        if 'open' in page_text and 'closed' not in page_text
        else "We are CLOSED"
    )

    # Parse sensor data
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

    scrape_timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    return lab_status, sensor_data, scrape_timestamp


def truncate_status(status):
    """Show only Fahrenheit and replace 'No Movement' with 'No Motion'."""
    if "°F" in status:
        parts = status.split("/")
        if len(parts) > 1:
            return parts[1].strip()
    return status.replace("No Movement", "No Motion")


def format_last_update(timestamp_str):
    """Format the time since the last update."""
    timestamp_str = timestamp_str.rsplit(' ', 1)[0]
    timestamp_format = "%b %d, %Y, %I:%M %p"
    try:
        timestamp = datetime.strptime(timestamp_str, timestamp_format)
        pacific = pytz.timezone('America/Los_Angeles')
        localized_timestamp = pacific.localize(timestamp)
        time_diff = datetime.now(pacific) - localized_timestamp

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


def format_sensor_data(lab_status, sensor_data, scrape_timestamp, url):
    """Format the scraped sensor data for the Discord event description."""
    df = pd.DataFrame(sensor_data)
    table_string = df.to_string(index=False)
    return (
        f"**Lab Status:** {lab_status}\n"
        f"**Data Scraped on:** {scrape_timestamp}\n"
        f"[Source: {url}]\n\n"
        f"**Sensor Data:**\n```\n{table_string}\n```"
    )


def get_image_as_binary(image_path):
    """Convert image to raw binary data for Discord."""
    try:
        with open(image_path, 'rb') as img_file:
            return img_file.read()
    except FileNotFoundError:
        logger.error(f"Image file '{image_path}' not found.")
        return None


async def find_lab_status_events(guild):
    """Find all existing 'We are' events."""
    try:
        we_are_events = [
            event for event in guild.scheduled_events if "We are" in event.name
        ]
        return we_are_events
    except Exception as e:
        logger.error(f"Error finding events: {e}")
        return []


async def ensure_single_we_are_event(guild):
    """
    Ensure that there is only one 'We are' event.
    If multiple are found, retain the most recent or active one and delete the others.
    Returns the single event to be managed.
    """
    we_are_events = await find_lab_status_events(guild)
    if len(we_are_events) <= 1:
        return we_are_events[0] if we_are_events else None

    # Sort events by end_time descending to retain the latest one
    we_are_events_sorted = sorted(
        we_are_events, key=lambda event: event.end_time, reverse=True
    )

    # Retain the first event and delete the rest
    primary_event = we_are_events_sorted[0]
    events_to_delete = we_are_events_sorted[1:]

    for event in events_to_delete:
        try:
            await event.delete(reason="Cleanup duplicate 'We are' events.")
            logger.info(f"Deleted duplicate event: {event.name} (ID: {event.id})")
        except discord.errors.Forbidden:
            logger.error(f"Forbidden: Cannot delete event {event.name} (ID: {event.id})")
        except Exception as e:
            logger.error(f"Error deleting event {event.name} (ID: {event.id}): {e}")

    return primary_event


@tasks.loop(minutes=5)
async def post_lab_status():
    """Task to post or update lab status event every 5 minutes."""
    try:
        # Scrape lab status and sensor data
        lab_status, sensor_data, scrape_timestamp = fetch_lab_status_and_sensors(
            LAB_URL
        )
        if lab_status is None or not sensor_data:
            logger.warning("Failed to scrape lab status or sensor data.")
            return

        # Generate and save the scaled and cropped synoptic view image
        generate_scaled_cropped_synoptic_view_image(
            output_png_file=SCALED_PNG_FILE
        )

        formatted_message = format_sensor_data(
            lab_status, sensor_data, scrape_timestamp, LAB_URL
        )

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            logger.error(f"Guild with ID {GUILD_ID} not found.")
            return

        # Ensure only one 'We are' event exists
        existing_event = await ensure_single_we_are_event(guild)
        image_binary = get_image_as_binary(SCALED_PNG_FILE)
        if image_binary is None:
            logger.error("Image binary data is None. Skipping event update.")
            return

        now = datetime.now().astimezone()
        event_end_time = now + timedelta(minutes=10)

        if existing_event:
            # Check if the existing event is still active
            if existing_event.end_time > now:
                try:
                    await existing_event.edit(
                        name=lab_status,
                        description=formatted_message,
                        end_time=event_end_time,
                        image=image_binary,
                    )
                    logger.info(f"Updated event: {existing_event.name} (ID: {existing_event.id})")
                except discord.errors.Forbidden as e:
                    logger.error(f"Cannot update event: {e}")
                    # Since the event cannot be updated, do not create a new one as per user instruction
                except Exception as e:
                    logger.error(f"Error updating event: {e}", exc_info=True)
            else:
                # Event has ended, create a new one
                try:
                    new_event = await guild.create_scheduled_event(
                        name=lab_status,
                        description=formatted_message,
                        start_time=now + timedelta(seconds=10),
                        end_time=event_end_time,
                        entity_type=discord.EntityType.external,
                        location="MAG Laboratory",
                        privacy_level=discord.PrivacyLevel.guild_only,
                        image=image_binary,
                    )
                    logger.info(f"Created new event: {lab_status} (ID: {new_event.id})")
                except discord.errors.Forbidden as e:
                    logger.error(f"Cannot create event: {e}")
                except Exception as e:
                    logger.error(f"Error creating event: {e}", exc_info=True)
        else:
            # No existing event, create a new one
            try:
                new_event = await guild.create_scheduled_event(
                    name=lab_status,
                    description=formatted_message,
                    start_time=now + timedelta(seconds=10),
                    end_time=event_end_time,
                    entity_type=discord.EntityType.external,
                    location="MAG Laboratory",
                    privacy_level=discord.PrivacyLevel.guild_only,
                    image=image_binary,
                )
                logger.info(f"Created new event: {lab_status} (ID: {new_event.id})")
            except discord.errors.Forbidden as e:
                logger.error(f"Cannot create event: {e}")
            except Exception as e:
                logger.error(f"Error creating event: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in post_lab_status: {e}", exc_info=True)


@post_lab_status.before_loop
async def before_post_lab_status():
    """Wait until the bot is ready before starting the loop."""
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    """Event handler when the bot is ready."""
    logger.info(f"Bot {bot.user.name} has connected to Discord.")
    if not post_lab_status.is_running():
        post_lab_status.start()


@bot.event
async def on_disconnect():
    """Event handler when the bot disconnects."""
    logger.warning(
        f"Bot {bot.user.name} has disconnected, attempting to reconnect..."
    )


@bot.event
async def on_resumed():
    """Event handler when the bot resumes after a disconnect."""
    logger.info(f"Bot {bot.user.name} has reconnected to Discord.")
    if not post_lab_status.is_running():
        post_lab_status.start()


@bot.event
async def on_shard_disconnect(shard_id):
    """Event handler for shard disconnections."""
    logger.warning(f"Shard {shard_id} disconnected.")


@bot.event
async def on_shard_connect(shard_id):
    """Event handler for shard reconnections."""
    logger.info(f"Shard {shard_id} reconnected.")
    if not post_lab_status.is_running():
        post_lab_status.start()


@bot.event
async def on_error(event_method, *args, **kwargs):
    """Global error handler."""
    logger.error(
        f"Error in {event_method}: {args}, {kwargs}", exc_info=True
    )


# Run the bot
try:
    bot.run(TOKEN)
except Exception as e:
    logger.critical(f"Critical error running the bot: {e}", exc_info=True)
