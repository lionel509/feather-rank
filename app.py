from feather_rank.rules import valid_set, match_winner
from feather_rank.db import insert_pending_match_points
import discord
from discord import app_commands
from dotenv import load_dotenv
import os
import asyncio
import aiosqlite
from collections import defaultdict
from feather_rank.logging_config import setup_logging, get_logger
from feather_rank.db import (
    init_db, get_or_create_player, update_player, insert_match, top_players, recent_matches, DB_PATH,
    has_accepted_tos, set_tos_accepted, get_match, get_match_participant_ids, get_signatures, set_match_status, add_signature,
    insert_pending_match, list_pending_for_user
)
from feather_rank.mmr import apply_team_match

# Load environment variables and setup logging early
load_dotenv()
setup_logging()  # respects LOG_LEVEL env var; default INFO
log = get_logger(__name__)
TOKEN = os.getenv('DISCORD_TOKEN')
TEST_MODE = os.getenv('TEST_MODE', '0') in ('1', 'true', 'TRUE', 'yes')
TEST_GUILD_ID = os.getenv('TEST_GUILD_ID')
TEST_GUILD_ID = int(TEST_GUILD_ID) if TEST_GUILD_ID and TEST_GUILD_ID.isdigit() else None
EPHEMERAL_DB = os.getenv('EPHEMERAL_DB', '0') in ('1', 'true', 'TRUE', 'yes')

# Configuration
K_FACTOR = int(os.getenv("K_FACTOR", "32"))
# When TEST_MODE is on, default to a separate DB unless explicitly overridden
DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "./test_feather_rank.sqlite" if TEST_MODE else "./smashcord.sqlite",
)
# Force in-memory DB if EPHEMERAL_DB is set
if EPHEMERAL_DB:
    DATABASE_PATH = "file::memory:?cache=shared"
# Point-based match rules
POINTS_TARGET = int(os.getenv("POINTS_TARGET", "21"))
POINTS_WIN_BY = int(os.getenv("POINTS_WIN_BY", "2"))
POINTS_CAP = int(os.getenv("POINTS_CAP", "30"))

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

# Terms of Service text
TOS_TEXT = (
    "By using this bot you agree to fair-play. "
    "False reports may be rejected or reverted. "
    "Your Discord ID and chosen display name are stored for match and verification records. "
    "Type /agree_tos to continue."
)

@bot.event
async def on_ready():
    # Initialize database
    await init_db(DATABASE_PATH)
    # Warn users if running with in-memory (non-persistent) database
    if DATABASE_PATH.startswith("file::memory:") or DATABASE_PATH == ":memory:":
        log.warning("Ephemeral DB mode active: data will NOT be saved between restarts")
    # Sync application commands
    if TEST_MODE and TEST_GUILD_ID:
        # Fast sync for a specific guild during testing
        guild = discord.Object(id=TEST_GUILD_ID)
        await tree.sync(guild=guild)
        log.info("Commands synced to test guild %s only", TEST_GUILD_ID)
    else:
        await tree.sync()
        log.info("Commands synced globally")
    # Set bot status to "Playing Badminton"
    status = "Badminton 🏸 [TEST MODE]" if TEST_MODE else "Badminton 🏸"
    await bot.change_presence(activity=discord.Game(name=status))
    log.info(
        "Bot ready as %s (guilds=%s) | mode=%s | db=%s",
        bot.user,
        len(bot.guilds),
        "TEST" if TEST_MODE else "PROD",
        DATABASE_PATH,
    )
    log.debug("Presence set and startup complete")

@tree.command(name="ping", description="Replies with pong")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong")

# --- ToS Agreement Helper ---
async def require_tos(interaction: discord.Interaction) -> bool:
    if not await has_accepted_tos(interaction.user.id):
        await interaction.response.send_message(
            "❗ Please run /agree_tos first to accept the Terms of Service.",
            ephemeral=True
        )
        return False
    return True

@tree.command(name="agree_tos", description="Agree to the Terms of Service to use the bot")
async def agree_tos(interaction: discord.Interaction):
    await set_tos_accepted(interaction.user.id)
    await interaction.response.send_message(
        f"{TOS_TEXT}\n\n✅ You have agreed to the Terms of Service. You may now use all features.",
        ephemeral=True
    )
    log.info("User %s accepted ToS", interaction.user.id)

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
    if not await require_tos(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    # Validate set winners
    valid_winners = {"A", "a", "B", "b"}
    if set1.upper() not in {"A", "B"} or set2.upper() not in {"A", "B"}:
        await interaction.followup.send("❌ Set winners must be 'A' or 'B'", ephemeral=True)
        return
    if set3 and set3.upper() not in {"A", "B"}:
        await interaction.followup.send("❌ Set 3 winner must be 'A' or 'B'", ephemeral=True)
        return
    all_players = [a1.id, a2.id, b1.id, b2.id]
    if len(set(all_players)) != 4:
        await interaction.followup.send("❌ All four players must be different", ephemeral=True)
        return
    set_winners = [set1.upper(), set2.upper()]
    if set3:
        set_winners.append(set3.upper())
    a_sets = set_winners.count("A")
    b_sets = set_winners.count("B")
    if a_sets > b_sets:
        winner = "A"
    elif b_sets > a_sets:
        winner = "B"
    else:
        await interaction.followup.send("❌ Invalid match result - no clear winner", ephemeral=True)
        return
    guild_id = interaction.guild_id or 0
    async with get_guild_lock(guild_id):
        match_id = await insert_pending_match(
            guild_id=guild_id,
            mode="2v2",
            team_a=[a1.id, a2.id],
            team_b=[b1.id, b2.id],
            set_winners=set_winners,
            winner=winner,
            reporter=interaction.user.id
        )
    log.info("Created pending match #%s (2v2) guild=%s reporter=%s", match_id, guild_id, interaction.user.id)
    await notify_verification(match_id)
    await interaction.followup.send(f"Match #{match_id} created. Waiting for approvals.", ephemeral=True)

# --- Doubles match with points ---
@tree.command(name="match_doubles_points", description="Record a 2v2 badminton match (point-based)")
@app_commands.describe(
    a1="Team A - Player 1",
    a2="Team A - Player 2",
    b1="Team B - Player 1",
    b2="Team B - Player 2",
    s1a="Set 1: points for A",
    s1b="Set 1: points for B",
    s2a="Set 2: points for A",
    s2b="Set 2: points for B",
    s3a="Set 3: points for A (optional)",
    s3b="Set 3: points for B (optional)"
)
async def match_doubles_points(
    interaction: discord.Interaction,
    a1: discord.User,
    a2: discord.User,
    b1: discord.User,
    b2: discord.User,
    s1a: int,
    s1b: int,
    s2a: int,
    s2b: int,
    s3a: int | None = None,
    s3b: int | None = None
):
    if not await require_tos(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    all_players = [a1.id, a2.id, b1.id, b2.id]
    if len(set(all_players)) != 4:
        await interaction.followup.send("❌ All four players must be different.", ephemeral=True)
        return
    set_scores = [
        {"A": s1a, "B": s1b},
        {"A": s2a, "B": s2b}
    ]
    if s3a is not None and s3b is not None:
        set_scores.append({"A": s3a, "B": s3b})
    # Validate each set
    for i, s in enumerate(set_scores):
        if not valid_set(s["A"], s["B"], POINTS_TARGET, POINTS_WIN_BY, POINTS_CAP):
            await interaction.followup.send(f"❌ Set {i+1} is not valid.", ephemeral=True)
            return
    winner, sets_a, sets_b, points_a, points_b = match_winner(set_scores, POINTS_TARGET, POINTS_WIN_BY, POINTS_CAP)
    if not winner:
        await interaction.followup.send("❌ No winner could be determined from the set scores.", ephemeral=True)
        return
    guild_id = interaction.guild_id or 0
    try:
        async with get_guild_lock(guild_id):
            match_id = await insert_pending_match_points(
                guild_id=guild_id,
                mode="2v2",
                team_a=[a1.id, a2.id],
                team_b=[b1.id, b2.id],
                set_scores=set_scores,
                reporter=interaction.user.id
            )
        await notify_verification(match_id)
        await interaction.followup.send(f"Match #{match_id} pending verification.", ephemeral=True)
    except Exception as e:
        log.exception("Failed to create match for user=%s", interaction.user.id)
        await interaction.followup.send(f"❌ Failed to create match: {e}", ephemeral=True)

# --- Singles match with points ---
@tree.command(name="match_singles", description="Record a 1v1 badminton match (point-based)")
@app_commands.describe(
    a="Player A",
    b="Player B",
    s1a="Set 1: points for A",
    s1b="Set 1: points for B",
    s2a="Set 2: points for A",
    s2b="Set 2: points for B",
    s3a="Set 3: points for A (optional)",
    s3b="Set 3: points for B (optional)"
)
async def match_singles(
    interaction: discord.Interaction,
    a: discord.User,
    b: discord.User,
    s1a: int,
    s1b: int,
    s2a: int,
    s2b: int,
    s3a: int | None = None,
    s3b: int | None = None
):
    if not await require_tos(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    if a.id == b.id:
        await interaction.followup.send("❌ Players must be different.", ephemeral=True)
        return
    set_scores = [
        {"A": s1a, "B": s1b},
        {"A": s2a, "B": s2b}
    ]
    if s3a is not None and s3b is not None:
        set_scores.append({"A": s3a, "B": s3b})
    # Validate each set
    for i, s in enumerate(set_scores):
        if not valid_set(s["A"], s["B"], POINTS_TARGET, POINTS_WIN_BY, POINTS_CAP):
            await interaction.followup.send(f"❌ Set {i+1} is not valid.", ephemeral=True)
            return
    winner, sets_a, sets_b, points_a, points_b = match_winner(set_scores, POINTS_TARGET, POINTS_WIN_BY, POINTS_CAP)
    if not winner:
        await interaction.followup.send("❌ No winner could be determined from the set scores.", ephemeral=True)
        return
    guild_id = interaction.guild_id or 0
    try:
        async with get_guild_lock(guild_id):
            match_id = await insert_pending_match_points(
                guild_id=guild_id,
                mode="1v1",
                team_a=[a.id],
                team_b=[b.id],
                set_scores=set_scores,
                reporter=interaction.user.id
            )
        await notify_verification(match_id)
        await interaction.followup.send(f"Match #{match_id} pending verification.", ephemeral=True)
    except Exception as e:
        log.exception("Failed to create match for user=%s", interaction.user.id)
        await interaction.followup.send(f"❌ Failed to create match: {e}", ephemeral=True)

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
    log.debug("Sent leaderboard for guild=%s, limit=%s", interaction.guild_id, limit)

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
    log.debug("Sent stats for user=%s guild=%s", user.id, interaction.guild_id)

@tree.command(name="verify", description="Verify a match result")
@app_commands.describe(
    match_id="The match ID to verify",
    decision="Approve or reject the match",
    name="Your name (optional, for signature)"
)
@app_commands.choices(decision=[
    app_commands.Choice(name="Approve", value="approve"),
    app_commands.Choice(name="Reject", value="reject")
])
async def verify(
    interaction: discord.Interaction,
    match_id: int,
    decision: app_commands.Choice[str],
    name: str | None = None
):
    if not await require_tos(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    match = await get_match(match_id)
    if not match:
        await interaction.followup.send(f"❌ Match ID {match_id} not found.")
        log.warning("Verify failed: match not found id=%s user=%s", match_id, interaction.user.id)
        return
    participants = await get_match_participant_ids(match_id)
    if interaction.user.id not in participants:
        await interaction.followup.send("❌ You are not a participant in this match.")
        log.warning("Verify blocked: non-participant user=%s match=%s", interaction.user.id, match_id)
        return
    if interaction.user.id == match.get("reporter"):
        await interaction.followup.send("❌ The reporter cannot verify their own match.")
        log.warning("Verify blocked: reporter self-verify user=%s match=%s", interaction.user.id, match_id)
        return
    # Insert or update signature
    await add_signature(match_id, interaction.user.id, decision.value, name)
    # Check all signatures
    signatures = await get_signatures(match_id)
    # If any reject, set status and notify reporter
    if any(sig["decision"] == "reject" for sig in signatures):
        await set_match_status(match_id, "rejected")
        log.info("Match #%s rejected by user=%s", match_id, interaction.user.id)
        # Try to notify reporter
        reporter_id = match.get("reporter")
        if reporter_id:
            try:
                user = await bot.fetch_user(reporter_id)
                await user.send(f"❌ Your match (ID: {match_id}) was rejected by a participant.")
            except Exception:
                pass
        await interaction.followup.send("❌ Match rejected. Reporter has been notified.")
        return
    # If all non-reporters have approved, finalize
    non_reporters = [pid for pid in participants if pid != match.get("reporter")]
    if all(any(sig["user_id"] == pid and sig["decision"] == "approve" for sig in signatures) for pid in non_reporters):
        await finalize_match(match_id)
        log.info("Match #%s finalized (all signatures collected)", match_id)
        await interaction.followup.send("✅ All participants approved. Match finalized.")
        return
    # Otherwise, show current signature state
    sig_table = "\n".join([
        f"{sig['signed_name'] or '[No Name]'}: {sig['decision']} at {sig['signed_at']}"
        for sig in signatures
    ])
    await interaction.followup.send(f"📝 Signature state for match {match_id}:\n```\n{sig_table}\n```")
    log.debug("Signature state requested for match=%s", match_id)

@tree.command(name="pending", description="List your matches awaiting your verification")
async def pending(interaction: discord.Interaction):
    if not await require_tos(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild_id or 0
    user_id = interaction.user.id
    matches = await list_pending_for_user(user_id, guild_id)
    if not matches:
        await interaction.followup.send("You have no pending matches to verify!", ephemeral=True)
        log.debug("No pending matches for user=%s guild=%s", user_id, guild_id)
        return
    lines = []
    for match in matches:
        match_id = match['id']
        # Check if user has already signed
        signatures = await get_signatures(match_id)
        if any(sig['user_id'] == user_id for sig in signatures):
            continue
        # Get player IDs
        team_a = [int(x) for x in match['team_a'].split(',') if x]
        team_b = [int(x) for x in match['team_b'].split(',') if x]
        all_players = team_a + team_b
        player_list = ', '.join(str(uid) for uid in all_players)
        lines.append(f"Match #{match_id}: Players: {player_list}\nUse /verify match_id:{match_id} decision:approve|reject name:<optional>")
    if not lines:
        await interaction.followup.send("You have no pending matches to verify!", ephemeral=True)
        return
    await interaction.followup.send("\n\n".join(lines), ephemeral=True)
    log.debug("Listed %s pending matches for user=%s guild=%s", len(lines), user_id, guild_id)

# --- Finalize match stub ---
async def finalize_match(match_id: int):
    import json
    from feather_rank.rules import match_winner
    from feather_rank.mmr import team_points_update
    match = await get_match(match_id)
    if not match:
        log.error("Finalize failed: match not found id=%s", match_id)
        return
    team_a_ids = [int(x) for x in match['team_a'].split(',') if x]
    team_b_ids = [int(x) for x in match['team_b'].split(',') if x]
    mode = match.get('mode')
    # Load set_scores
    try:
        set_scores = json.loads(match.get('set_scores') or '[]')
    except Exception:
        set_scores = []
        log.exception("Failed to parse set_scores for match=%s", match_id)
    # Compute winner, points
    winner, _, _, points_a, points_b = match_winner(
        set_scores,
        POINTS_TARGET,
        POINTS_WIN_BY,
        POINTS_CAP
    )
    share_a = points_a / max(1, (points_a + points_b))
    # Get player info
    players_a = [await get_or_create_player(uid, f"User{uid}") for uid in team_a_ids]
    players_b = [await get_or_create_player(uid, f"User{uid}") for uid in team_b_ids]
    ratings_a = [p['rating'] for p in players_a]
    ratings_b = [p['rating'] for p in players_b]
    # Update ratings using points share
    new_ratings_a, new_ratings_b = team_points_update(ratings_a, ratings_b, share_a, k=K_FACTOR)
    # Update players' ratings and W/L
    for i, p in enumerate(players_a):
        await update_player(p['user_id'], new_ratings_a[i], won=(winner == 'A'))
    for i, p in enumerate(players_b):
        await update_player(p['user_id'], new_ratings_b[i], won=(winner == 'B'))
    # Finalize match in DB
    from feather_rank.db import finalize_points
    await finalize_points(match_id, winner, set_scores, points_a, points_b)
    log.info("Match #%s finalized: winner=%s points A=%s B=%s", match_id, winner, points_a, points_b)
    # Build summary with per-set scores
    summary = f"🏸 Match Verified!\nMatch ID: {match_id}\nMode: {mode}\nWinner: Team {winner}\n"
    summary += f"Team A: {', '.join(str(uid) for uid in team_a_ids)}\n"
    summary += f"Team B: {', '.join(str(uid) for uid in team_b_ids)}\n"
    summary += "Set Scores:\n"
    for idx, s in enumerate(set_scores, 1):
        summary += f"  Set {idx}: A {s.get('A', 0)} - B {s.get('B', 0)}\n"
    summary += f"Total Points: A {points_a} - B {points_b}\n"
    # Try to post in channel (if available)
    channel_id = match.get('channel_id')
    sent = False
    if channel_id:
        try:
            channel = await bot.fetch_channel(channel_id)
            # Only send to channels that support .send (TextChannel, Thread)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(summary)
            else:
                raise TypeError(f"Unsupported channel type: {type(channel)}")
            sent = True
        except Exception:
            log.warning("Failed to post summary to channel_id=%s for match=%s", channel_id, match_id, exc_info=True)
    # Fallback: DM reporter
    if not sent:
        reporter_id = match.get('reporter')
        if reporter_id:
            try:
                user = await bot.fetch_user(reporter_id)
                await user.send(summary)
            except Exception:
                log.warning("Failed to DM reporter=%s for match=%s", reporter_id, match_id, exc_info=True)

async def notify_verification(match_id: int):
    match = await get_match(match_id)
    if not match:
        log.error("Notify failed: match not found id=%s", match_id)
        return
    participants = await get_match_participant_ids(match_id)
    reporter = match.get("reporter")
    non_reporters = [uid for uid in participants if uid != reporter]
    summary = f"You have a match to verify!\n"
    summary += f"Match ID: {match_id}\n"
    summary += f"Mode: {match.get('mode')}\n"
    summary += f"Teams: {match.get('team_a')} vs {match.get('team_b')}\n"
    summary += f"Winner: {match.get('winner')}\n"
    summary += f"\nTo approve or reject, use:\n/verify match_id:{match_id} decision:approve|reject name:<optional>"
    for user_id in non_reporters:
        try:
            user = await bot.fetch_user(user_id)
            await user.send(summary)
        except Exception:
            log.warning("Failed to DM user=%s for verification of match=%s", user_id, match_id, exc_info=True)

# Run the bot
if __name__ == "__main__":
    if not TOKEN:
        log.error("DISCORD_TOKEN not set. Please provide it via environment or .env file.")
        raise SystemExit(1)
    log.info("Starting bot… (mode=%s)", "TEST" if TEST_MODE else "PROD")
    bot.run(TOKEN)
