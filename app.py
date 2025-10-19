from feather_rank.rules import valid_set, match_winner
import json
import fmt
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
    insert_pending_match, list_pending_for_user, latest_pending_for_user
)
from feather_rank.mmr import apply_team_match
import db as db

# Load environment variables and setup logging early
load_dotenv()
setup_logging()  # respects LOG_LEVEL env var; default INFO
log = get_logger(__name__)
TOKEN = os.getenv('DISCORD_TOKEN')
TEST_MODE = os.getenv('TEST_MODE', '0') in ('1', 'true', 'TRUE', 'yes')
TEST_GUILD_ID = os.getenv('TEST_GUILD_ID')
TEST_GUILD_ID = int(TEST_GUILD_ID) if TEST_GUILD_ID and TEST_GUILD_ID.isdigit() else None
EPHEMERAL_DB = os.getenv('EPHEMERAL_DB', '0') in ('1', 'true', 'TRUE', 'yes')

# --- Discord config and intents ---
EMOJI_APPROVE = os.getenv("EMOJI_APPROVE", "✅")
EMOJI_REJECT  = os.getenv("EMOJI_REJECT",  "❌")
MENTIONS_PING = os.getenv("MENTIONS_PING","1").lower() in ("1","true","yes")
ALLOWED_MENTIONS = discord.AllowedMentions(users=MENTIONS_PING, roles=False, everyone=False)

intents = discord.Intents.default()
intents.reactions = True
# use these intents when constructing Bot(...)

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
intents.reactions = True  # Needed for on_raw_reaction_add

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

@tree.command(name="agree_tos", description="Agree to the Terms and add your signed name")
@app_commands.describe(name="Your name as you want it recorded")
async def agree_tos(interaction: discord.Interaction, name: str):
    name = (name or "").strip()[:60]
    await set_tos_accepted(interaction.user.id, version="v1", signed_name=name)
    await interaction.response.send_message(
        f"**ToS accepted.** Recorded name: `{name}`",
        ephemeral=True
    )
    log.info("User %s accepted ToS with name", interaction.user.id)

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
        # Build compact summary
        def disp(u: discord.User) -> str:
            return getattr(u, 'display_name', None) or u.name
        title = fmt.bold(f"Match #{match_id} — Pending Verification")
        teams = f"{disp(a1)}/{disp(a2)} vs {disp(b1)}/{disp(b2)}"
        sets_line = fmt.score_sets(set_scores)
        help_line = "Use " + fmt.code("/verify") + " to approve or " + fmt.code('/verify decision:reject') + " to reject."
        msg = f"{title}\n{teams}\n{sets_line}\n{help_line}"
        await interaction.followup.send(msg, ephemeral=True)
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
        # Build compact summary
        def disp(u: discord.User) -> str:
            return getattr(u, 'display_name', None) or u.name
        title = fmt.bold(f"Match #{match_id} — Pending Verification")
        teams = f"{disp(a)} vs {disp(b)}"
        sets_line = fmt.score_sets(set_scores)
        help_line = "Use " + fmt.code("/verify") + " to approve or " + fmt.code('/verify decision:reject') + " to reject."
        msg = f"{title}\n{teams}\n{sets_line}\n{help_line}"
        await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        log.exception("Failed to create match for user=%s", interaction.user.id)
        await interaction.followup.send(f"❌ Failed to create match: {e}", ephemeral=True)

@tree.command(name="leaderboard", description="Show the top players by rating")
@app_commands.describe(limit="Number of players to show (default: 20)")
async def leaderboard(interaction: discord.Interaction, limit: int = 20):
    # Validate limit
    if limit < 1:
        await interaction.response.send_message("❌ Limit must be at least 1")
        return
    if limit > 100:
        await interaction.response.send_message("❌ Limit cannot exceed 100")
        return

    # Get top players
    players = await top_players(guild_id=interaction.guild_id or 0, limit=limit)

    if not players:
        await interaction.response.send_message("📊 No players found. Play some matches to get started!")
        return

    title = f"🏆 Leaderboard (Top {limit})"
    lines: list[str] = []
    for idx, player in enumerate(players, start=1):
        uid_val = player.get('user_id')
        uid = int(uid_val) if uid_val is not None else 0
        stored_username = str(player.get('username', f'User{uid or "?"}'))
        name = await fmt.display_name_or_cached(bot, interaction.guild, uid, fallback=stored_username) if uid else stored_username
        mention_str = f"@silent {fmt.mention(uid)}" if uid else stored_username
        wl = f"{player.get('wins', 0)}-{player.get('losses', 0)}"
        line = f"{idx}. {mention_str} — {name} — {player.get('rating', 0):.1f} ({wl})"
        lines.append(line)

    content = "**" + title + "**\n" + "\n".join(lines)
    await interaction.response.send_message(content=content, allowed_mentions=ALLOWED_MENTIONS)
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
        await interaction.followup.send(f"📊 @silent {user.mention} has no games recorded yet.")
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

    # Build KV section with bold keys and inline code numbers
    kv_lines = [
        f"{fmt.bold('Rating')}: {fmt.code(f'{player['rating']:.1f}')}",
        f"{fmt.bold('Record')}: {fmt.code(f"{player['wins']}-{player['losses']}")} ({fmt.code(f'{win_rate:.1f}%')})",
        f"{fmt.bold('Total Matches')}: {fmt.code(str(total_matches))}",
    ]

    # Recent matches table (short columns)
    recent_block = ""
    if matches:
        headers = ["Mode", "Team", "Sets", "Result"]
        rows: list[list[str]] = []
        for match in matches:
            mode = str(match.get('mode', ''))
            team_a_ids = [int(x) for x in match.get('team_a', '').split(',') if x]
            team_b_ids = [int(x) for x in match.get('team_b', '').split(',') if x]
            winner = match.get('winner')
            # Sets: prefer set_scores formatting, fallback to set_winners
            sets_str = ""
            try:
                set_scores = json.loads(match.get('set_scores') or '[]')
                if set_scores:
                    sets_str = fmt.score_sets(set_scores)
            except Exception:
                sets_str = ""
            if not sets_str:
                sets_str = str(match.get('set_winners') or '')

            user_team = 'A' if user.id in team_a_ids else 'B'
            result = 'WIN' if (winner == user_team) else 'LOSS'
            rows.append([mode, f"Team {user_team}", sets_str, result])
        recent_block = fmt.mono_table(rows, headers=headers)
    else:
        recent_block = "*No recent matches found.*"

    message = (
        f"## 📊 Stats for {user.mention}\n\n"
        + "\n".join(kv_lines)
        + (f"\n\n**Recent Matches (Last {len(matches)}):**\n" + recent_block if matches else f"\n\n{recent_block}")
    )

    await interaction.followup.send(message, allowed_mentions=ALLOWED_MENTIONS)
    log.debug("Sent stats for user=%s guild=%s", user.id, interaction.guild_id)


@tree.command(name="verify", description="Verify a pending match")
@app_commands.describe(
    decision="approve or reject",
    name="Your name as you want it recorded (optional)",
    match_id="Match ID to verify (optional; defaults to latest pending)"
)
@app_commands.choices(decision=[
    app_commands.Choice(name="approve", value="approve"),
    app_commands.Choice(name="reject", value="reject")
])
async def verify(
    inter: discord.Interaction,
    decision: str,
    name: str | None = None,
    match_id: int | None = None,
):
    # Check ToS acceptance first
    if not await db.has_accepted_tos(inter.user.id):
        await inter.response.send_message(
            "Please run /agree_tos name:<Your Name> first.",
            ephemeral=True
        )
        return

    await inter.response.defer(ephemeral=True)

    try:
        # Auto-select latest pending match if match_id not provided
        selected_id: int
        if match_id is None:
            row = await db.latest_pending_for_user(inter.guild_id or 0, inter.user.id)
            if not row:
                await inter.followup.send("No pending matches to verify.", ephemeral=True)
                return
            selected_id = row["id"]
        else:
            selected_id = match_id

        # Default name to user's display name if not provided
        if name is None:
            name = (inter.user.display_name or inter.user.name)[:60]
        else:
            name = name.strip()[:60]

        # Validate match and permissions
        match = await get_match(selected_id)
        if not match:
            await inter.followup.send(f"❌ Match ID {selected_id} not found.", ephemeral=True)
            log.warning("Verify failed: match not found id=%s user=%s", selected_id, inter.user.id)
            return
        participants = await get_match_participant_ids(selected_id)
        if inter.user.id not in participants:
            await inter.followup.send("❌ You are not a participant in this match.", ephemeral=True)
            log.warning("Verify blocked: non-participant user=%s match=%s", inter.user.id, selected_id)
            return
        if inter.user.id == match.get("reporter"):
            await inter.followup.send("❌ The reporter cannot verify their own match.", ephemeral=True)
            log.warning("Verify blocked: reporter self-verify user=%s match=%s", inter.user.id, selected_id)
            return

        # Record signature
        await db.add_signature(selected_id, inter.user.id, decision, name)

        # Try to finalize (handles rejection and approval finalization)
        await try_finalize_match(selected_id)

        # Send confirmation using fmt helpers
        hint = fmt.block("/verify decision:approve name:YourName\n/verify decision:reject name:YourName", "md")
        msg = (
            f"{fmt.bold('Verification recorded')}\n"
            f"Match: {fmt.code(str(selected_id))}\n"
            f"Decision: {fmt.code(decision)}\n"
            f"Name: {fmt.code(name)}"
        )
        await inter.followup.send(msg + "\n\n" + hint, ephemeral=True, allowed_mentions=ALLOWED_MENTIONS)
        log.info("Verify recorded: match=%s user=%s decision=%s name=%s", selected_id, inter.user.id, decision, name)

    except Exception as e:
        log.exception("Error in /verify for user=%s", inter.user.id)
        try:
            await inter.followup.send(f"❌ Failed to verify: {e}", ephemeral=True)
        except Exception:
            pass

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

    # Filter out already-signed matches for this user
    unsigned_matches = []
    for match in matches:
        match_id = match['id']
        signatures = await get_signatures(match_id)
        if any(sig['user_id'] == user_id for sig in signatures):
            continue
        unsigned_matches.append((match, signatures))

    if not unsigned_matches:
        await interaction.followup.send("You have no pending matches to verify!", ephemeral=True)
        return

    # Resolve usernames for participants
    name_cache: dict[int, str] = {}
    async def name_of(uid: int) -> str:
        if uid in name_cache:
            return name_cache[uid]
        # Prefer guild display name if available
        member = None
        if interaction.guild:
            member = interaction.guild.get_member(uid)
        if member is not None:
            name_cache[uid] = f"@{member.display_name}"
            return name_cache[uid]
        try:
            user = await bot.fetch_user(uid)
            name_cache[uid] = f"@{getattr(user, 'display_name', None) or user.name}"
        except Exception:
            name_cache[uid] = f"@{uid}"
        return name_cache[uid]

    headers = ["Match", "Mode", "Teams", "Sets"]
    rows: list[list[str]] = []
    for match, _sigs in unsigned_matches:
        mid = match['id']
        mode = match.get('mode', '')
        team_a_ids = [int(x) for x in match.get('team_a', '').split(',') if x]
        team_b_ids = [int(x) for x in match.get('team_b', '').split(',') if x]
        # Build team strings with mentions
        a_mentions = [f"@silent {fmt.mention(uid)}" for uid in team_a_ids]
        b_mentions = [f"@silent {fmt.mention(uid)}" for uid in team_b_ids]
        teams = f"{'/'.join(a_mentions)} vs {'/'.join(b_mentions)}"
        # Sets: parse set_scores and format using fmt.score_sets; fallback to N/A
        try:
            set_scores = json.loads(match.get('set_scores') or '[]')
        except Exception:
            set_scores = []
        sets_str = fmt.score_sets(set_scores) if set_scores else "N/A"
        rows.append([f"#{mid}", str(mode), teams, sets_str])

    table = fmt.mono_table(rows, headers=headers)
    # Autofill name with user's display name or username
    autofill_name = interaction.user.display_name if hasattr(interaction.user, 'display_name') and interaction.user.display_name else interaction.user.name
    approve_box = fmt.block(f"/verify match_id:<ID> decision:approve name:{autofill_name}", "md")
    reject_box = fmt.block(f"/verify match_id:<ID> decision:reject name:{autofill_name}", "md")
    instructions = f"Approve:\n{approve_box}\nReject:\n{reject_box}"
    await interaction.followup.send(table + "\n" + instructions, ephemeral=True, allowed_mentions=ALLOWED_MENTIONS)
    log.debug("Listed %s pending matches for user=%s guild=%s", len(rows), user_id, guild_id)

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
    # Build formatted message with mentions and display names
    guild_id = match.get('guild_id')
    guild = bot.get_guild(guild_id) if guild_id else None
    
    # Build Markdown message
    title = fmt.bold(f"Please verify Match #{match_id}")
    
    # Teams as "A vs B" string using fmt.mention for each user id
    team_a_ids = [int(x) for x in (match.get('team_a') or '').split(',') if x]
    team_b_ids = [int(x) for x in (match.get('team_b') or '').split(',') if x]
    team_a_mentions = [fmt.mention(uid) for uid in team_a_ids]
    team_b_mentions = [fmt.mention(uid) for uid in team_b_ids]
    teams = f"{'/'.join(team_a_mentions)} vs {'/'.join(team_b_mentions)}"
    
    # Sets using fmt.score_sets
    try:
        set_scores = json.loads(match.get('set_scores') or '[]')
    except Exception:
        set_scores = []
    sets = fmt.score_sets(set_scores) if set_scores else "N/A"
    
    # Tip block
    tip = fmt.block("/verify decision:approve name:YourName\n/verify decision:reject name:YourName", "md")
    
    # Complete message
    msg = f"{title}\n{teams}\n{sets}\nReact {EMOJI_APPROVE} to approve or {EMOJI_REJECT} to reject.\n\n{tip}"
    
    # For each non-reporter participant
    for user_id in non_reporters:
        try:
            user = await bot.fetch_user(user_id)
            dm = await user.send(msg, allowed_mentions=ALLOWED_MENTIONS)
            await dm.add_reaction(EMOJI_APPROVE)
            await dm.add_reaction(EMOJI_REJECT)
            await db.record_verification_message(dm.id, match_id, guild_id, user_id)
        except discord.Forbidden:
            # Fallback in the reporting channel/thread
            try:
                channel = None
                if guild and getattr(guild, "system_channel", None):
                    channel = guild.system_channel
                # Find first text channel we can send in
                if channel is None:
                    for ch in getattr(guild, "channels", []) or []:
                        if isinstance(ch, discord.TextChannel) and ch.permissions_for(ch.guild.me).send_messages:
                            channel = ch
                            break
                if channel and isinstance(channel, (discord.TextChannel, discord.Thread)):
                    post = await channel.send(msg, allowed_mentions=ALLOWED_MENTIONS)
                    await post.add_reaction(EMOJI_APPROVE)
                    await post.add_reaction(EMOJI_REJECT)
                    await db.record_verification_message(post.id, match_id, guild_id, user_id)
                else:
                    log.warning("No suitable channel to post verification for user=%s match=%s", user_id, match_id)
            except Exception:
                log.warning("Channel fallback failed for user=%s match=%s", user_id, match_id, exc_info=True)
        except Exception:
            log.warning("Failed to notify user=%s for verification of match=%s", user_id, match_id, exc_info=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not bot.user or payload.user_id == bot.user.id: return
    row = await db.get_verification_message(payload.message_id)
    if not row: return
    if payload.user_id != row["user_id"]: return

    emoji = str(payload.emoji)
    if   emoji == EMOJI_APPROVE: decision = "approve"
    elif emoji == EMOJI_REJECT:  decision = "reject"
    else: return

    if not await db.has_accepted_tos(payload.user_id):
        ch = await bot.fetch_channel(payload.channel_id)
        try:
            msg = await ch.fetch_message(payload.message_id)
            await msg.reply("Please run `/agree_tos name:<Your Name>` first, then react again.", mention_author=False, allowed_mentions=ALLOWED_MENTIONS)
        except: pass
        return

    tos = await db.get_tos(payload.user_id)
    signed_name = (tos["signed_name"] if tos and tos.get("signed_name") else None)
    if not signed_name:
        # fallback to current display name if available
        guild = bot.get_guild(row["guild_id"]) if row["guild_id"] else None
        member = guild.get_member(payload.user_id) if guild else None
        signed_name = (member.display_name if member else "Unknown")[:60]

    await db.add_signature(row["match_id"], payload.user_id, decision, signed_name)
    await db.delete_verification_message(payload.message_id)

    # Acknowledge
    try:
        ch = await bot.fetch_channel(payload.channel_id)
        msg = await ch.fetch_message(payload.message_id)
        await msg.reply(f"Verification recorded as `{signed_name}` ({decision}).", mention_author=False, allowed_mentions=ALLOWED_MENTIONS)
    except: pass

    await try_finalize_match(row["match_id"])

async def try_finalize_match(match_id: int):
    """
    Try to finalize a match based on verification signatures.
    - If any signature is 'reject': set status='rejected' and notify all parties
    - Determine required approvers based on mode (singles: 1, doubles: 3)
    - If all required approvers have approved: call finalize_points() and set status='verified'
    - Otherwise: do nothing (still pending)
    """
    import json
    from feather_rank.rules import match_winner
    from feather_rank.mmr import team_points_update
    from feather_rank.db import finalize_points
    
    # Load match, participants, reporter, and signatures
    match = await get_match(match_id)
    if not match:
        log.error("try_finalize: match not found id=%s", match_id)
        return
    
    participants = await get_match_participant_ids(match_id)
    reporter = match.get("reporter")
    sigs = await get_signatures(match_id)
    guild_id = match.get('guild_id')
    guild = bot.get_guild(guild_id) if guild_id else None
    
    # Parse teams
    team_a_ids = [int(x) for x in (match.get('team_a') or '').split(',') if x]
    team_b_ids = [int(x) for x in (match.get('team_b') or '').split(',') if x]
    mode = match.get('mode', '2v2')
    
    # If any signature.decision == 'reject': set status='rejected' and notify
    if any(s.get("decision") == "reject" for s in sigs):
        await set_match_status(match_id, "rejected")
        log.info("Match #%s rejected by participant(s)", match_id)
        
        # Build rejection summary with Markdown
        title = fmt.bold(f"Match #{match_id} Rejected")
        teams_line = f"{'/'.join(fmt.mention(uid) for uid in team_a_ids)} vs {'/'.join(fmt.mention(uid) for uid in team_b_ids)}"
        
        # Include who rejected
        rejectors = [s.get("user_id") for s in sigs if s.get("decision") == "reject"]
        rejector_mentions = ", ".join(fmt.mention(uid) for uid in rejectors if uid)
        
        rejection_msg = f"{title}\n{teams_line}\nRejected by: {rejector_mentions}"
        
        # Notify reporter
        if reporter:
            try:
                user = await bot.fetch_user(reporter)
                await user.send(rejection_msg, allowed_mentions=ALLOWED_MENTIONS)
            except Exception:
                log.debug("Failed to DM reporter=%s for rejected match=%s", reporter, match_id)
        
        # Notify all participants
        for pid in participants:
            if pid != reporter:  # Don't double-notify reporter
                try:
                    user = await bot.fetch_user(pid)
                    await user.send(rejection_msg, allowed_mentions=ALLOWED_MENTIONS)
                except Exception:
                    log.debug("Failed to DM participant=%s for rejected match=%s", pid, match_id)
        
        return
    
    # Determine required approvers based on mode
    non_reporters = [pid for pid in participants if pid != reporter]
    
    if mode == "1v1":
        # Singles: opponent only (1 non-reporter)
        required_approvers = non_reporters[:1]  # Should be exactly 1
    else:
        # Doubles: all 3 non-reporters
        required_approvers = non_reporters
    
    # Check if all required have decision='approve'
    approved_users = {s.get("user_id") for s in sigs if s.get("decision") == "approve"}
    all_approved = all(uid in approved_users for uid in required_approvers)
    
    if not all_approved:
        # Still pending
        log.debug("Match #%s still pending approval", match_id)
        return
    
    # All required approvers have approved - finalize the match
    log.info("Match #%s: all required approvals received, finalizing", match_id)
    
    # Load set_scores
    try:
        set_scores = json.loads(match.get('set_scores') or '[]')
    except Exception:
        set_scores = []
        log.exception("Failed to parse set_scores for match=%s", match_id)
    
    # Compute winner and points
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
    
    # Call finalize_points then set status='verified'
    await finalize_points(match_id, winner, set_scores, points_a, points_b)
    await set_match_status(match_id, "verified")
    
    log.info("Match #%s finalized: winner=%s points A=%s B=%s", match_id, winner, points_a, points_b)
    
    # Post public summary (channel/thread) with mentions and display names
    title = fmt.bold(f"🏸 Match #{match_id} Verified!")
    
    # Build team lines with mentions + display names
    team_a_lines = []
    for uid in team_a_ids:
        display = await fmt.display_name_or_cached(bot, guild, uid, fallback=f"User{uid}")
        team_a_lines.append(f"{fmt.mention(uid)} — {display}")
    
    team_b_lines = []
    for uid in team_b_ids:
        display = await fmt.display_name_or_cached(bot, guild, uid, fallback=f"User{uid}")
        team_b_lines.append(f"{fmt.mention(uid)} — {display}")
    
    teams_section = f"**Team A:**\n" + "\n".join(team_a_lines) + f"\n\n**Team B:**\n" + "\n".join(team_b_lines)
    
    # Set scores
    sets_section = "**Set Scores:**\n"
    for idx, s in enumerate(set_scores, 1):
        sets_section += f"  Set {idx}: A {s.get('A', 0)} - B {s.get('B', 0)}\n"
    
    # Winner and points
    winner_line = f"**Winner:** Team {winner}"
    points_line = f"**Total Points:** A {points_a} - B {points_b}"
    
    summary = f"{title}\n{teams_section}\n\n{sets_section}\n{winner_line}\n{points_line}"
    
    # Try to post in channel/thread
    channel_id = match.get('channel_id')
    sent = False
    if channel_id:
        try:
            channel = await bot.fetch_channel(channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(summary, allowed_mentions=ALLOWED_MENTIONS)
                sent = True
        except Exception:
            log.warning("Failed to post summary to channel_id=%s for match=%s", channel_id, match_id, exc_info=True)
    
    # Fallback: DM reporter
    if not sent and reporter:
        try:
            user = await bot.fetch_user(reporter)
            await user.send(summary, allowed_mentions=ALLOWED_MENTIONS)
        except Exception:
            log.warning("Failed to DM reporter=%s for match=%s", reporter, match_id, exc_info=True)

# Run the bot
if __name__ == "__main__":
    if not TOKEN:
        log.error("DISCORD_TOKEN not set. Please provide it via environment or .env file.")
        raise SystemExit(1)
    log.info("Starting bot… (mode=%s)", "TEST" if TEST_MODE else "PROD")
    bot.run(TOKEN)
