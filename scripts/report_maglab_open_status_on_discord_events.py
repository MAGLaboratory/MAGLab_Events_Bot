import discord
from discord.ext import tasks, commands
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import pandas as pd
import os


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


# Function to fetch lab status and sensor data from the website
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
                if sensor_name not in ["Page Loaded", "Auto Refresh"]:
                    sensor_data.append({
                        'Sensor': sensor_name,
                        'Status': cells[1].get_text(strip=True),
                        'Last Update': calculate_last_update(cells[3].get_text(strip=True))
                    })

    scrape_timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    return lab_status, sensor_data, scrape_timestamp


# Function to calculate the time since the last update
def calculate_last_update(timestamp_str):
    timestamp_str = timestamp_str.rsplit(' ', 1)[0]  # Remove timezone
    timestamp_format = "%b %d, %Y, %I:%M %p"  # Example: "Sep 4, 2024, 12:04 AM"

    try:
        timestamp = datetime.strptime(timestamp_str, timestamp_format)
        pacific = pytz.timezone('America/Los_Angeles')
        localized_timestamp = pacific.localize(timestamp)
        time_diff = datetime.now(pacific) - localized_timestamp

        if time_diff < timedelta(minutes=1):
            return "less than a minute ago"
        elif time_diff < timedelta(hours=1):
            return f"{int(time_diff.total_seconds() // 60)} minutes ago"
        elif time_diff < timedelta(days=1):
            return f"{int(time_diff.total_seconds() // 3600)} hours ago"
        else:
            return f"{time_diff.days} days ago"

    except Exception as e:
        return f"Error parsing timestamp: {e}"


# Function to format the scraped sensor data for the event description
def format_sensor_data(lab_status, sensor_data, scrape_timestamp, url):
    df = pd.DataFrame(sensor_data)
    table_string = df.to_string(index=False)
    return (f"**Lab Status:** {lab_status}\n"
            f"**Data Scraped on:** {scrape_timestamp}\n"
            f"[Source: {url}]\n\n"
            f"**Sensor Data:**\n```\n{table_string}\n```")


# Function to find an existing "We are" event
async def find_lab_status_event(guild):
    events = guild.scheduled_events  # Get all scheduled events
    for event in events:
        if "We are" in event.name:  # Check for "We are" in the event title
            return event
    return None  # No existing "We are" event found


# Function to check if there is any other active event that does not have "We are" in the title
async def check_for_other_active_events(guild):
    now = datetime.now().astimezone()
    for event in guild.scheduled_events:
        if event.start_time <= now <= event.end_time and "We are" not in event.name:
            return True  # Another active event exists
    return False  # No other active events


# Task to post or update lab status event every 1 minute
@tasks.loop(minutes=1)
async def post_lab_status():
    url = "https://www.maglaboratory.org/hal"
    lab_status, sensor_data, scrape_timestamp = fetch_lab_status_and_sensors(url)

    if lab_status and sensor_data:
        # Format the sensor data for the event description
        formatted_message = format_sensor_data(lab_status, sensor_data, scrape_timestamp, url)

        # Get the guild (server)
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print(f"Guild with ID {GUILD_ID} not found")
            return

        # Check if there's an existing "We are" event
        existing_event = await find_lab_status_event(guild)
        event_title = f"{lab_status}"
        event_description = formatted_message

        # Check for any other active events that do not have "We are" in the title
        other_active_event = await check_for_other_active_events(guild)

        if other_active_event:
            # If another active event exists, end the "We are" event (if any)
            if existing_event:
                await existing_event.delete()
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %I:%M %p %Z')}] Ended 'We are' event due to another active event.")
            return

        if existing_event:
            # Update the existing "We are" event
            await existing_event.edit(
                name=event_title,
                description=event_description,
                end_time=datetime.now().astimezone() + timedelta(minutes=5)
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %I:%M %p %Z')}] Updated event: {existing_event.name}")
        else:
            # Create a new "We are" event if no other event is active
            await guild.create_scheduled_event(
                name=event_title,
                description=event_description,
                start_time=datetime.now().astimezone() + timedelta(seconds=10),
                end_time=datetime.now().astimezone() + timedelta(minutes=5),
                entity_type=discord.EntityType.external,
                location="MAG Laboratory",
                privacy_level=discord.PrivacyLevel.guild_only
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %I:%M %p %Z')}] Created new event: {event_title}")


# Event handler for when the bot is ready
@bot.event
async def on_ready():
    print(f'Bot {bot.user.name} has connected to Discord')
    post_lab_status.start()  # Start the loop task


# Run the bot
bot.run(TOKEN)
