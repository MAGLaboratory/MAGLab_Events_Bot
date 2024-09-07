import discord
from discord.ext import tasks, commands
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import pandas as pd
import os
from scrape_synoptic_view_and_crop_scale_for_discord_events import generate_scaled_cropped_synoptic_view_image


# Read the Discord bot token from discord_token.txt
def get_discord_token():
    try:
        with open('discord_token.txt', 'r') as token_file:
            return token_file.read().strip()  # Read and remove any extra whitespace
    except FileNotFoundError:
        print("Error: 'discord_token.txt' not found in the current directory.")
        return None


TOKEN = get_discord_token()  # Get the token from the file
if not TOKEN:
    raise SystemExit("Discord token is missing. Please provide a valid token in 'discord_token.txt'.")

GUILD_ID = 697971426799517774  # Set to the provided Guild ID

# Discord Bot Setup
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)


# Scrape lab status and sensor data from the webpage
def fetch_lab_status_and_sensors(url):
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for HTTP errors
    except requests.exceptions.RequestException as e:
        print(f"Error fetching the webpage: {e}")
        return None

    # Parse HTML
    soup = BeautifulSoup(response.text, 'html.parser')
    page_text = soup.get_text().lower()

    # Determine the lab status
    if 'open' in page_text and 'closed' not in page_text:
        lab_status = "We are OPEN"
    elif 'closed' in page_text:
        lab_status = "We are CLOSED"
    else:
        lab_status = "Status could not be determined"

    # Scrape sensor data
    sensor_data = []
    sensor_table = soup.find('table')
    if sensor_table:
        for row in sensor_table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) == 4:  # Ensure proper row structure
                sensor_name = cells[0].get_text(strip=True)
                status = cells[1].get_text(strip=True)
                # Only display Fahrenheit for temperatures and replace "No Movement" with "No Motion"
                status = truncate_status(status)
                if sensor_name not in ["Page Loaded", "Auto Refresh"]:
                    sensor_data.append({
                        'Sensor': sensor_name,
                        'Status': status,
                        'Last Update': format_last_update(cells[3].get_text(strip=True))
                    })

    scrape_timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    return lab_status, sensor_data, scrape_timestamp


# Function to truncate the status to only show Fahrenheit and replace "No Movement" with "No Motion"
def truncate_status(status):
    if "°F" in status:
        return status.split("/")[1].strip()  # Keep only the Fahrenheit value
    return status.replace("No Movement", "No Motion")  # Replace "No Movement" with "No Motion"


# Format the time since the last update in short form
def format_last_update(timestamp_str):
    timestamp_str = timestamp_str.rsplit(' ', 1)[0]  # Remove timezone
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
        else:
            return f"{time_diff.days} days ago"

    except Exception as e:
        return f"Error parsing timestamp: {e}"


# Format the scraped sensor data for the event description
def format_sensor_data(lab_status, sensor_data, scrape_timestamp, url):
    df = pd.DataFrame(sensor_data)
    table_string = df.to_string(index=False)
    return (f"**Lab Status:** {lab_status}\n"
            f"**Data Scraped on:** {scrape_timestamp}\n"
            f"[Source: {url}]\n\n"
            f"**Sensor Data:**\n```\n{table_string}\n```")


# Convert image to raw binary data for Discord
def get_image_as_binary(image_path):
    with open(image_path, 'rb') as img_file:
        return img_file.read()


# Function to find an existing "We are" event
async def find_lab_status_event(guild):
    for event in guild.scheduled_events:
        if "We are" in event.name:
            return event
    return None


# Check if there's another active event
async def check_for_other_active_events(guild):
    now = datetime.now().astimezone()
    for event in guild.scheduled_events:
        if event.start_time <= now <= event.end_time and "We are" not in event.name:
            return True
    return False


# Task to post or update lab status event every 5 minutes
@tasks.loop(minutes=5)
async def post_lab_status():
    url = "https://www.maglaboratory.org/hal"
    scaled_png_file = 'maglab_synoptic_view_scaled.png'

    # Scrape lab status and sensor data
    lab_status, sensor_data, scrape_timestamp = fetch_lab_status_and_sensors(url)

    # Call the generate_scaled_cropped_synoptic_view_image function
    generate_scaled_cropped_synoptic_view_image(output_png_file=scaled_png_file)

    if lab_status and sensor_data:
        formatted_message = format_sensor_data(lab_status, sensor_data, scrape_timestamp, url)

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print(f"Guild with ID {GUILD_ID} not found")
            return

        existing_event = await find_lab_status_event(guild)
        event_title = f"{lab_status}"
        event_description = formatted_message
        image_binary = get_image_as_binary(scaled_png_file)

        other_active_event = await check_for_other_active_events(guild)

        if other_active_event:
            if existing_event:
                await existing_event.delete()
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %I:%M %p %Z')}] Ended 'We are' event due to another active event.")
            return

        if existing_event:
            await existing_event.edit(
                name=event_title,
                description=event_description,
                end_time=datetime.now().astimezone() + timedelta(minutes=10),
                image=image_binary
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %I:%M %p %Z')}] Updated event: {existing_event.name}")
        else:
            await guild.create_scheduled_event(
                name=event_title,
                description=event_description,
                start_time=datetime.now().astimezone() + timedelta(seconds=10),
                end_time=datetime.now().astimezone() + timedelta(minutes=10),
                entity_type=discord.EntityType.external,
                location="MAG Laboratory",
                privacy_level=discord.PrivacyLevel.guild_only,
                image=image_binary
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %I:%M %p %Z')}] Created new event: {event_title}")


# Bot ready event
@bot.event
async def on_ready():
    print(f'Bot {bot.user.name} has connected to Discord')
    post_lab_status.start()


# Run the bot
bot.run(TOKEN)
