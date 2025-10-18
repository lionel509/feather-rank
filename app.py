import discord
from discord import app_commands
from dotenv import load_dotenv
import os
import asyncio
import aiosqlite
from collections import defaultdict
from db import init_db, get_or_create_player, update_player, insert_match, top_players, recent_matches, DB_PATH
from mmr import apply_team_match

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Configuration
K_FACTOR = int(os.getenv("K_FACTOR", "32"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "./smashcord.sqlite")

# Guild-based locks for database writes
guild_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

def get_guild_lock(guild_id: int | None) -> asyncio.Lock:
    """Get or create a lock for the specified guild."""
    return guild_locks[guild_id or 0]

# Setup minimal intents (no privileged intents needed)
intents = discord.Intents.none()
intents.guilds = True  # Required for guild commands

# Create bot instance
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    # Initialize database
    await init_db(DATABASE_PATH)
    # Sync application commands
    await tree.sync()
    # Set bot status to "Playing Badminton"
    await bot.change_presence(activity=discord.Game(name="Badminton 🏸"))
    print(f'Logged in as {bot.user}')
    print(f'Status: Playing Badminton 🏸')

@tree.command(name="ping", description="Replies with pong")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong")

@tree.command(name="match_doubles", description="Record a 2v2 badminton match")
@app_commands.describe(
    a1="Team A - Player 1",
    a2="Team A - Player 2",
    b1="Team B - Player 1",
    b2="Team B - Player 2",
    set1="Set 1 winner (A or B)",
    set2="Set 2 winner (A or B)",
    set3="Set 3 winner (A or B, optional)"
)
async def match_doubles(
    interaction: discord.Interaction,
    a1: discord.User,
    a2: discord.User,
    b1: discord.User,
    b2: discord.User,
    set1: str,
    set2: str,
    set3: str | None = None
):
    await interaction.response.defer()
    
    # Validate set winners
    valid_winners = {"A", "a", "B", "b"}
    if set1.upper() not in {"A", "B"} or set2.upper() not in {"A", "B"}:
        await interaction.followup.send("❌ Set winners must be 'A' or 'B'")
        return
    
    if set3 and set3.upper() not in {"A", "B"}:
        await interaction.followup.send("❌ Set 3 winner must be 'A' or 'B'")
        return
    
    # Check for duplicate players
    all_players = [a1.id, a2.id, b1.id, b2.id]
    if len(set(all_players)) != 4:
        await interaction.followup.send("❌ All four players must be different")
        return
    
    # Normalize set winners
    set_winners = [set1.upper(), set2.upper()]
    if set3:
        set_winners.append(set3.upper())
    
    # Determine match winner (best of 3)
    a_sets = set_winners.count("A")
    b_sets = set_winners.count("B")
    
    if a_sets > b_sets:
        winner = "A"
    elif b_sets > a_sets:
        winner = "B"
    else:
        await interaction.followup.send("❌ Invalid match result - no clear winner")
        return
    
    # Use guild lock for all database writes
    guild_id = interaction.guild_id or 0
    async with get_guild_lock(guild_id):
        # Get or create all players with base rating of 1200
        player_a1 = await get_or_create_player(a1.id, a1.name, base_rating=1200)
        player_a2 = await get_or_create_player(a2.id, a2.name, base_rating=1200)
        player_b1 = await get_or_create_player(b1.id, b1.name, base_rating=1200)
        player_b2 = await get_or_create_player(b2.id, b2.name, base_rating=1200)
        
        # Get current ratings
        team_a_ratings = [player_a1['rating'], player_a2['rating']]
        team_b_ratings = [player_b1['rating'], player_b2['rating']]
        
        # Calculate new ratings using MMR system
        new_team_a_ratings, new_team_b_ratings = apply_team_match(
            team_a_ratings, team_b_ratings, winner, k=K_FACTOR
        )
        
        # Update all players
        await update_player(a1.id, new_team_a_ratings[0], won=(winner == "A"))
        await update_player(a2.id, new_team_a_ratings[1], won=(winner == "A"))
        await update_player(b1.id, new_team_b_ratings[0], won=(winner == "B"))
        await update_player(b2.id, new_team_b_ratings[1], won=(winner == "B"))
        
        # Insert match record
        match_id = await insert_match(
            guild_id=guild_id,
            mode="2v2",
            team_a=[a1.id, a2.id],
            team_b=[b1.id, b2.id],
            set_winners=set_winners,
            winner=winner,
            created_by=interaction.user.id
        )
    
    # Calculate rating changes
    delta_a = new_team_a_ratings[0] - team_a_ratings[0]
    delta_b = new_team_b_ratings[0] - team_b_ratings[0]
    
    # Build summary message
    summary = f"## 🏸 Match Recorded (ID: {match_id})\n\n"
    summary += f"**Team A:** {a1.mention} & {a2.mention}\n"
    summary += f"**Team B:** {b1.mention} & {b2.mention}\n\n"
    summary += f"**Sets:** {' - '.join(set_winners)}\n"
    summary += f"**Winner:** Team {winner} 🎉\n\n"
    summary += "**Rating Changes:**\n"
    
    if winner == "A":
        summary += f"Team A: {team_a_ratings[0]:.1f} → {new_team_a_ratings[0]:.1f} (+{delta_a:.1f}) ✅\n"
        summary += f"Team B: {team_b_ratings[0]:.1f} → {new_team_b_ratings[0]:.1f} ({delta_b:.1f})\n"
    else:
        summary += f"Team A: {team_a_ratings[0]:.1f} → {new_team_a_ratings[0]:.1f} ({delta_a:.1f})\n"
        summary += f"Team B: {team_b_ratings[0]:.1f} → {new_team_b_ratings[0]:.1f} (+{delta_b:.1f}) ✅\n"
    
    await interaction.followup.send(summary)

@tree.command(name="leaderboard", description="Show the top players by rating")
@app_commands.describe(limit="Number of players to show (default: 20)")
async def leaderboard(interaction: discord.Interaction, limit: int = 20):
    await interaction.response.defer()
    
    # Validate limit
    if limit < 1:
        await interaction.followup.send("❌ Limit must be at least 1")
        return
    if limit > 100:
        await interaction.followup.send("❌ Limit cannot exceed 100")
        return
    
    # Get top players
    players = await top_players(guild_id=interaction.guild_id or 0, limit=limit)
    
    if not players:
        await interaction.followup.send("📊 No players found. Play some matches to get started!")
        return
    
    # Build leaderboard table
    header = "```\n"
    header += "Rank  Player                Rating    W-L\n"
    header += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    lines = []
    for idx, player in enumerate(players, start=1):
        rank = f"{idx}."
        name = player['username'][:18]  # Truncate long names
        rating = f"{player['rating']:.1f}"
        wl = f"{player['wins']}-{player['losses']}"
        
        # Format line with proper spacing
        line = f"{rank:<5} {name:<20} {rating:<9} {wl}"
        lines.append(line)
    
    footer = "```"
    
    leaderboard_text = header + "\n".join(lines) + "\n" + footer
    
    # Check if message is too long
    if len(leaderboard_text) > 2000:
        await interaction.followup.send("❌ Leaderboard is too long. Try a smaller limit.")
        return
    
    await interaction.followup.send(f"## 🏆 Leaderboard (Top {len(players)})\n{leaderboard_text}")

@tree.command(name="stats", description="Show player statistics")
@app_commands.describe(user="The user to show stats for")
async def stats(interaction: discord.Interaction, user: discord.User):
    await interaction.response.defer()
    
    # Try to get existing player (don't create)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE user_id = ?", (user.id,)
        ) as cursor:
            row = await cursor.fetchone()
            player = dict(row) if row else None
    
    if not player:
        await interaction.followup.send(f"📊 {user.mention} has no games recorded yet.")
        return
    
    # Get player stats
    total_matches = player['wins'] + player['losses']
    win_rate = (player['wins'] / total_matches * 100) if total_matches > 0 else 0
    
    # Get recent matches
    matches = await recent_matches(
        guild_id=interaction.guild_id or 0, 
        user_id=user.id, 
        limit=5
    )
    
    # Build stats message
    stats_msg = f"## 📊 Stats for {user.mention}\n\n"
    stats_msg += f"**Rating:** {player['rating']:.1f}\n"
    stats_msg += f"**Record:** {player['wins']}-{player['losses']} ({win_rate:.1f}% wins)\n"
    stats_msg += f"**Total Matches:** {total_matches}\n"
    
    if matches:
        stats_msg += f"\n**Recent Matches (Last {len(matches)}):**\n```\n"
        for match in matches:
            # Parse match data
            mode = match['mode']
            team_a_ids = [int(x) for x in match['team_a'].split(',')]
            team_b_ids = [int(x) for x in match['team_b'].split(',')]
            winner = match['winner']
            set_winners = match['set_winners']
            
            # Determine if user was on team A or B
            if user.id in team_a_ids:
                user_team = "A"
                result = "✅ WIN" if winner == "A" else "❌ LOSS"
            else:
                user_team = "B"
                result = "✅ WIN" if winner == "B" else "❌ LOSS"
            
            stats_msg += f"{mode} | Team {user_team} | {set_winners} | {result}\n"
        
        stats_msg += "```"
    else:
        stats_msg += "\n*No recent matches found.*"
    
    await interaction.followup.send(stats_msg)

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)
