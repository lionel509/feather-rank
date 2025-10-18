"""
Minimal test bot without database - for testing Discord connectivity and commands
Run this to verify your bot token and test basic Discord interactions
"""

import discord
from discord import app_commands
from dotenv import load_dotenv
import os
from logging_config import setup_logging, get_logger

# Load environment variables and setup logging
load_dotenv()
setup_logging()  # use LOG_LEVEL=DEBUG for very verbose logs
log = get_logger(__name__)
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    log.error("DISCORD_TOKEN not found in .env file!")
    print("Please create a .env file with your Discord bot token:")
    print("DISCORD_TOKEN=your_token_here")
    exit(1)

# Setup minimal intents
intents = discord.Intents.none()
intents.guilds = True

# Create bot instance
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    # Sync commands
    await tree.sync()
    # Set status
    await bot.change_presence(activity=discord.Game(name="Badminton 🏸 [TEST MODE]"))
    bu = bot.user
    bu_id = getattr(bu, "id", "?")
    log.info("Bot logged in as %s (id=%s) guilds=%s [TEST MODE]", bu, bu_id, len(bot.guilds))
    print("=" * 60)
    print("\n📋 Available test commands:")
    print("  /ping - Simple ping test")
    print("  /test_params - Test command with parameters")
    print("  /test_user - Test user selection")
    print("\n✨ Bot is ready for testing!")

@tree.command(name="ping", description="Simple ping test")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot is working!")

@tree.command(name="test_params", description="Test command with parameters")
@app_commands.describe(
    text="Some text to echo back",
    number="A number to display"
)
async def test_params(interaction: discord.Interaction, text: str, number: int = 42):
    await interaction.response.send_message(
        f"✅ **Parameters received:**\n"
        f"📝 Text: {text}\n"
        f"🔢 Number: {number}\n"
        f"👤 User: {interaction.user.mention}\n"
        f"🏠 Guild: {interaction.guild.name if interaction.guild else 'DM'}"
    )

@tree.command(name="test_user", description="Test user selection")
@app_commands.describe(user="Select a user")
async def test_user(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_message(
        f"✅ **User selected:**\n"
        f"👤 Name: {user.name}\n"
        f"🆔 ID: {user.id}\n"
        f"🤖 Bot: {user.bot}\n"
        f"Mention: {user.mention}"
    )

@tree.command(name="test_choices", description="Test with predefined choices")
@app_commands.describe(winner="Who won?")
@app_commands.choices(winner=[
    app_commands.Choice(name="Team A", value="A"),
    app_commands.Choice(name="Team B", value="B"),
    app_commands.Choice(name="Draw", value="draw")
])
async def test_choices(interaction: discord.Interaction, winner: app_commands.Choice[str]):
    await interaction.response.send_message(
        f"✅ **Choice selected:**\n"
        f"📊 Winner: {winner.name} (value: {winner.value})"
    )

@tree.command(name="test_defer", description="Test deferred response")
async def test_defer(interaction: discord.Interaction):
    # Defer the response (for long-running operations)
    await interaction.response.defer()
    
    # Simulate some work
    import asyncio
    await asyncio.sleep(2)
    
    # Send the actual response
    await interaction.followup.send("✅ Deferred response sent after 2 seconds!")

@bot.event
async def on_guild_join(guild):
    log.info("Joined new server: %s (ID: %s)", guild.name, guild.id)

@bot.event
async def on_guild_remove(guild):
    log.info("Left server: %s (ID: %s)", guild.name, guild.id)

@bot.event
async def on_error(event, *args, **kwargs):
    log.exception("Error in %s", event)

# Run the bot
if __name__ == "__main__":
    print("\n🚀 Starting test bot (NO DATABASE)...")
    print("Press Ctrl+C to stop\n")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped by user")
    except discord.LoginFailure:
        log.error("Invalid Discord token!")
        print("Please check your DISCORD_TOKEN in the .env file")
    except Exception as e:
        log.exception("Unhandled error: %s", e)
