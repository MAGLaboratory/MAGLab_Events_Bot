import discord
from discord.ext import tasks, commands
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import pandas as pd
import os
import asyncio
import logging
from scrape_synoptic_view_and_crop_scale_for_discord_events import generate_scaled_cropped_synoptic_view_image

# Configure logging
logging.basicConfig(
    filename='bot_activity.log',  # Log file
    filemode='a',  # Append to the file
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %I:%M %p',
    level=logging.INFO
)

# Log to both console and file
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %I:%M %p')
console.setFormatter(formatter)
logging.getLogger().addHandler(console)

# Get the Discord bot token from the 'discord_token.txt' file
def get_discord_token():
    try:
        with open('discord_token.txt', 'r') as token_file:
            return token_file.read().strip()
    except FileNotFoundError:
        logging.error("Error: 'discord_token.txt' not found.")
        return None

TOKEN = get_discord_token()
if not TOKEN:
    raise SystemExit("Discord token is missing.")

GUILD_ID = 697971426799517774

# Initialize the Discord bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

# Function to scrape lab status and sensor data from the webpage
def fetch_lab_status_and_sensors(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching the webpage: {e}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    page_text = soup.get_text().lower()

    # Determine lab status
    lab_status = "We are OPEN" if 'open' in page_text and 'closed' not in page_text else "We are CLOSED"

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
                    sensor_data.append({
                        'Sensor': sensor_name,
                        'Status': status,
                        'Last Update': format_last_update(cells[3].get_text(strip=True))
                    })

    scrape_timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    return lab_status, sensor_data, scrape_timestamp

# Function to show only Fahrenheit and replace "No Movement" with "No Motion"
def truncate_status(status):
    if "°F" in status:
        return status.split("/")[1].strip()
    return status.replace("No Movement", "No Motion")

# Format the time since the last update
def format_last_update(timestamp_str):
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
        logging.error(f"Error parsing timestamp: {e}")
        return f"Error parsing timestamp: {e}"

# Format the scraped sensor data for the Discord event description
def format_sensor_data(lab_status, sensor_data, scrape_timestamp, url):
    df = pd.DataFrame(sensor_data)
    table_string = df.to_string(index=False)
    return (
        f"**Lab Status:** {lab_status}\n"
        f"**Data Scraped on:** {scrape_timestamp}\n"
        f"[Source: {url}]\n\n"
        f"**Sensor Data:**\n```\n{table_string}\n```"
    )

# Convert image to raw binary data for Discord
def get_image_as_binary(image_path):
    with open(image_path, 'rb') as img_file:
        return img_file.read()

# Find an existing "We are" event
async def find_lab_status_event(guild):
    try:
        for event in guild.scheduled_events:
            if "We are" in event.name:
                return event
    except Exception as e:
        logging.error(f"Error finding event: {e}")
    return None

# Check if there's another active event
async def check_for_other_active_events(guild):
    try:
        now = datetime.now().astimezone()
        for event in guild.scheduled_events:
            if event.start_time <= now <= event.end_time and "We are" not in event.name:
                return True
    except Exception as e:
        logging.error(f"Error checking for other active events: {e}")
    return False

# Task to post or update lab status event every 5 minutes
@tasks.loop(minutes=5)
async def post_lab_status():
    try:
        url = "https://www.maglaboratory.org/hal"
        scaled_png_file = 'maglab_synoptic_view_scaled.png'

        # Scrape lab status and sensor data
        lab_status, sensor_data, scrape_timestamp = fetch_lab_status_and_sensors(url)
        if lab_status is None or not sensor_data:
            raise ValueError("Failed to scrape lab status or sensor data")

        # Generate and save the scaled and cropped synoptic view image
        generate_scaled_cropped_synoptic_view_image(output_png_file=scaled_png_file)

        formatted_message = format_sensor_data(lab_status, sensor_data, scrape_timestamp, url)

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            raise ValueError(f"Guild with ID {GUILD_ID} not found")

        existing_event = await find_lab_status_event(guild)
        image_binary = get_image_as_binary(scaled_png_file)

        # Handle active events
        other_active_event = await check_for_other_active_events(guild)
        if other_active_event:
            if existing_event:
                await existing_event.delete()
                logging.info(f"Ended 'We are' event due to another active event.")
            return

        # Update or create event
        if existing_event:
            await existing_event.edit(
                name=lab_status,
                description=formatted_message,
                end_time=datetime.now().astimezone() + timedelta(minutes=10),
                image=image_binary
            )
            logging.info(f"Updated event: {existing_event.name}")
        else:
            await guild.create_scheduled_event(
                name=lab_status,
                description=formatted_message,
                start_time=datetime.now().astimezone() + timedelta(seconds=10),
                end_time=datetime.now().astimezone() + timedelta(minutes=10),
                entity_type=discord.EntityType.external,
                location="MAG Laboratory",
                privacy_level=discord.PrivacyLevel.guild_only,
                image=image_binary
            )
            logging.info(f"Created new event: {lab_status}")
    except Exception as e:
        logging.error(f"Error in post_lab_status: {e}")
    finally:
        await asyncio.sleep(300)  # Ensure the task respects the 5-minute interval

# Bot ready event
@bot.event
async def on_ready():
    logging.info(f"Bot {bot.user.name} has connected to Discord")
    if not post_lab_status.is_running():
        post_lab_status.start()

# Handle bot disconnects and reconnections
@bot.event
async def on_disconnect():
    logging.warning(f"Bot {bot.user.name} has disconnected, attempting to reconnect...")

@bot.event
async def on_resumed():
    logging.info(f"Bot {bot.user.name} has reconnected to Discord")
    if not post_lab_status.is_running():
        post_lab_status.start()

# Handle gateway shard errors and recover
@bot.event
async def on_error(event_method, *args, **kwargs):
    logging.error(f"Error in {event_method}: {args}, {kwargs}")
    if not post_lab_status.is_running():
        post_lab_status.restart()

# Run the bot with automatic reconnection logic
def run_bot():
    try:
        bot.run(TOKEN, reconnect=True)
    except Exception as e:
        logging.error(f"Error running the bot: {e}")
        logging.info("Attempting to restart bot...")
        run_bot()  # Restart the bot on failure

# Start the bot
run_bot()
