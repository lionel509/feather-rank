# app.py
# Discord badminton bot — singles/doubles w/ point-share Elo, verification, dropdown scoring

from __future__ import annotations

import os
import json
import asyncio
from collections import defaultdict
import aiosqlite
import discord
from discord import app_commands

# --- Internal modules (keep your package names) ---
# fmt must provide: bold, code, block, score_sets, display_name_or_cached, mention (optional)
import fmt
from feather_rank import db
from feather_rank.rules import match_winner, valid_set, set_finished
from feather_rank.mmr import team_points_update

# Optional logging util; fall back to std logging if missing
try:
    from feather_rank.logging_config import setup_logging, get_logger  # type: ignore
    setup_logging()
    log = get_logger(__name__)
except Exception:  # pragma: no cover
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("feather_rank.app")

# --- Env / Config ---
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TEST_MODE = os.getenv("TEST_MODE", "0").lower() in ("1", "true", "yes")
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID", "0") or 0) or None
EPHEMERAL_DB = os.getenv("EPHEMERAL_DB", "0").lower() in ("1", "true", "yes")
PIN_SCOREBOARD = os.getenv("PIN_SCOREBOARD", "0").lower() in ("1","true","yes")

K_FACTOR = int(os.getenv("K_FACTOR", "32"))
# Rating for bot/guest players - validate it's positive
try:
    GUEST_RATING = float(os.getenv("GUEST_RATING", "1200"))
    if GUEST_RATING <= 0:
        log.warning("GUEST_RATING must be positive, using default 1200")
        GUEST_RATING = 1200.0
except ValueError:
    log.warning("Invalid GUEST_RATING value, using default 1200")
    GUEST_RATING = 1200.0

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "./test_feather_rank.sqlite" if TEST_MODE else "./smashcord.sqlite",
)
if EPHEMERAL_DB:
    DATABASE_PATH = "file::memory:?cache=shared"

# Scoring knobs
POINTS_TARGET_DEFAULT = int(os.getenv("POINTS_TARGET_DEFAULT", "21"))
POINTS_WIN_BY = int(os.getenv("POINTS_WIN_BY", "2"))
POINTS_CAP_ENV = os.getenv("POINTS_CAP")  # if set, overrides derived cap

def derive_cap(target: int) -> int | None:
    if POINTS_CAP_ENV is not None:
        return int(POINTS_CAP_ENV)
    return 30 if target >= 21 else 15

# Emoji + mentions
EMOJI_APPROVE = os.getenv("EMOJI_APPROVE", "✅")
EMOJI_REJECT  = os.getenv("EMOJI_REJECT",  "❌")
MENTIONS_PING = os.getenv("MENTIONS_PING", "1").lower() in ("1","true","yes")
ALLOWED_MENTIONS = discord.AllowedMentions(users=MENTIONS_PING, roles=False, everyone=False)

# Scoreboard emoji
EMOJI_A_PLUS = os.getenv("EMOJI_A_PLUS", "🟥")   # add point Team A
EMOJI_B_PLUS = os.getenv("EMOJI_B_PLUS", "🟦")   # add point Team B
EMOJI_UNDO   = os.getenv("EMOJI_UNDO",   "↩️")   # undo last rally
EMOJI_SERVE  = os.getenv("EMOJI_SERVE",  "🏸")   # toggle serve indicator (optional)
EMOJI_NEXT   = os.getenv("EMOJI_NEXT",   "⏭️")   # force next set (admin/ref)
EMOJI_DONE   = os.getenv("EMOJI_DONE",   "🏁")   # finalize early

# Intents
intents = discord.Intents.none()
intents.guilds = True
intents.reactions = True   # for on_raw_reaction_add
intents.members = False    # not required for slash commands

# Discord client + tree
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Guild locks
guild_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
def get_guild_lock(guild_id: int | None) -> asyncio.Lock:
    return guild_locks[guild_id or 0]

# Concurrency guard for scoreboard updates (per-scoreboard locks)
_scoreboard_locks: dict[int, asyncio.Lock] = {}
def sb_lock(sb_id: int) -> asyncio.Lock:
    lock = _scoreboard_locks.get(sb_id)
    if not lock:
        lock = asyncio.Lock()
        _scoreboard_locks[sb_id] = lock
    return lock

# ToS text
TOS_TEXT = (
    "By using this bot you agree to fair-play. "
    "False reports may be rejected or reverted. "
    "Your Discord ID and chosen display name are stored for match and verification records. "
    "Type /agree_tos to continue."
)

def _get_bot_id() -> int | None:
    """Get the bot's user ID if available."""
    return bot.user.id if bot.user else None

def _create_guest_player(user_id: int) -> dict:
    """Create a guest player dictionary for the bot with default guest rating."""
    return {
        "user_id": user_id,
        "username": "Guest",
        "rating": GUEST_RATING,
        "wins": 0,
        "losses": 0
    }

# --- Helpers ---
async def has_accepted_tos_safe(user_id: int) -> bool:
    """Check ToS acceptance; if table missing, create schema and retry."""
    try:
        return await db.has_accepted_tos(user_id)
    except aiosqlite.OperationalError as e:
        if "no such table: tos_acceptances" in str(e):
            await db.init_db(DATABASE_PATH)
            return await db.has_accepted_tos(user_id)
        raise

async def require_tos(inter: discord.Interaction) -> bool:
    if not await has_accepted_tos_safe(inter.user.id):
        await inter.response.send_message(
            "❗ Please run /agree_tos first to accept the Terms of Service.",
            ephemeral=True
        )
        return False
    return True

async def _names(bot: discord.Client, guild: discord.Guild | None, ids: list[int]) -> str:
    """Helper to format player names from user IDs."""
    parts = []
    for uid in ids:
        parts.append(await fmt.display_name_or_cached(bot, guild, uid, fallback=f"User{uid}"))
    return "/".join(parts)

def _serve_marker(serve_side: str | None) -> str:
    """Helper to display serve indicator."""
    return "▶ A" if serve_side == "A" else ("▶ B" if serve_side == "B" else "—")

async def post_scoreboard_message(inter: discord.Interaction, scoreboard_id: int, set_no: int) -> discord.Message:
    """Post a scoreboard message and add reaction controls."""
    sb = await db.get_scoreboard(scoreboard_id)
    s = await db.get_set(scoreboard_id, set_no)
    guild = inter.guild

    a_names = await _names(bot, guild, [int(x) for x in (sb["team_a"].split(",")) if x])
    b_names = await _names(bot, guild, [int(x) for x in (sb["team_b"].split(",")) if x])

    title = f"🏸 Live Scoreboard #{scoreboard_id} — Set {set_no}/{3}"
    fmtline = f"Best-of-3 to {sb['target_points']} (win by 2, cap {sb['cap_points']})"
    score = f"**A {s['a_points']} — {s['b_points']} B**"
    serve = _serve_marker(sb.get("serve_side")) if "serve_side" in sb.keys() else "—"

    content = (
        f"{title}\n"
        f"{a_names} **vs** {b_names}\n"
        f"{fmtline}\n"
        f"{score}   ·   Serve: {serve}\n\n"
        f"React {EMOJI_A_PLUS} to add A point, {EMOJI_B_PLUS} for B, {EMOJI_UNDO} to undo.\n"
        f"{EMOJI_DONE} finalize · {EMOJI_NEXT} next-set · {EMOJI_SERVE} toggle serve"
    )

    channel = inter.channel
    m = await channel.send(content, allowed_mentions=ALLOWED_MENTIONS)
    for e in (EMOJI_A_PLUS, EMOJI_B_PLUS, EMOJI_UNDO, EMOJI_SERVE, EMOJI_NEXT, EMOJI_DONE):
        try:
            await m.add_reaction(e)
        except Exception:
            pass
    await db.record_sb_message(m.id, scoreboard_id, set_no)
    # Never pin; optionally unpin if someone pinned it
    try:
        if getattr(m, "pinned", False) and not PIN_SCOREBOARD:
            await m.unpin(reason="Scoreboard: pin disabled")
    except Exception:
        pass
    return m

async def edit_scoreboard_message(message: discord.Message, scoreboard_id: int, set_no: int) -> None:
    """Edit an existing scoreboard message with updated scores (no pin)."""
    sb = await db.get_scoreboard(scoreboard_id)
    s = await db.get_set(scoreboard_id, set_no)
    guild = message.guild

    a_names = await _names(bot, guild, [int(x) for x in (sb["team_a"].split(",")) if x])
    b_names = await _names(bot, guild, [int(x) for x in (sb["team_b"].split(",")) if x])

    title = f"🏸 Live Scoreboard #{scoreboard_id} — Set {set_no}/{3}"
    fmtline = f"Best-of-3 to {sb['target_points']} (win by 2, cap {sb['cap_points']})"
    score = f"**A {s['a_points']} — {s['b_points']} B**"
    serve = _serve_marker(sb.get("serve_side")) if "serve_side" in sb.keys() else "—"

    content = (
        f"{title}\n"
        f"{a_names} **vs** {b_names}\n"
        f"{fmtline}\n"
        f"{score}   ·   Serve: {serve}\n\n"
        f"React {EMOJI_A_PLUS} to add A point, {EMOJI_B_PLUS} for B, {EMOJI_UNDO} to undo.\n"
        f"{EMOJI_DONE} finalize · {EMOJI_NEXT} next-set · {EMOJI_SERVE} toggle serve"
    )

    await message.edit(content=content, allowed_mentions=ALLOWED_MENTIONS)
    # Never pin; optionally unpin if pinned
    try:
        if getattr(message, "pinned", False) and not PIN_SCOREBOARD:
            await message.unpin(reason="Scoreboard: pin disabled")
    except Exception:
        pass

async def ensure_set_row(scoreboard_id: int, set_no: int):
    row = await db.get_set(scoreboard_id, set_no)
    if not row:
        await db.upsert_set(scoreboard_id, set_no, 0, 0, None)

async def _advance_if_needed(payload, msg: discord.Message, sb: dict, sb_msg_row: dict) -> bool:
    """Advance to next set or finalize match if current set is finished.

    Returns True if we advanced (new message posted) or finalized, else False.
    """
    s = await db.get_set(sb["id"], sb_msg_row["set_no"])
    if s:
        a, b = int(s["a_points"]), int(s["b_points"])
    else:
        a, b = 0, 0
    done, winner = set_finished(a, b, sb["target_points"], win_by=2, cap=sb.get("cap_points"))
    if not done:
        return False

    # Close current set with winner
    await db.upsert_set(sb["id"], sb_msg_row["set_no"], a, b, winner)

    # Count wins across sets 1..3
    sets = []
    for i in (1, 2, 3):
        row = await db.get_set(sb["id"], i)
        if row:
            sets.append(row)
    wins_a = sum(1 for x in sets if (x.get("winner") == "A"))
    wins_b = sum(1 for x in sets if (x.get("winner") == "B"))

    # Match over?
    if wins_a == 2 or wins_b == 2:
        await finalize_scoreboard_match(sb["id"])  # creates pending match + notify
        return True

    # Otherwise start next set
    next_no = max(x["set_no"] for x in sets) + 1 if len(sets) < 3 else None
    if next_no:
        await ensure_set_row(sb["id"], next_no)
        ch = await bot.fetch_channel(payload.channel_id)
        class _Inter:
            guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
            channel = ch
        await post_scoreboard_message(_Inter(), sb["id"], set_no=next_no)
        return True

    return False

async def finalize_scoreboard_match(scoreboard_id: int) -> None:
    """Finalize a scoreboard match and create a verified match record."""
    sb = await db.get_scoreboard(scoreboard_id)
    if not sb:
        log.warning("finalize_scoreboard_match: scoreboard not found id=%s", scoreboard_id)
        return
    
    sets = [await db.get_set(scoreboard_id, i) for i in (1, 2, 3) if await db.get_set(scoreboard_id, i)]
    if not sets:
        log.warning("finalize_scoreboard_match: no sets found for scoreboard id=%s", scoreboard_id)
        return
    
    # Build set_scores JSON for existing finalize_points path
    set_scores = [{"A": int(s["a_points"]), "B": int(s["b_points"])} for s in sets if s]
    
    # Determine winner by sets
    wa = sum(1 for s in sets if s.get("winner") == "A")
    wb = sum(1 for s in sets if s.get("winner") == "B")
    winner = "A" if wa > wb else "B"

    # Insert pending match (or directly finalize if you want to skip verification for ref-controlled games)
    match_id = await db.insert_pending_match_points(
        guild_id=sb["guild_id"],
        mode=sb["mode"],
        team_a=[int(x) for x in sb["team_a"].split(",") if x],
        team_b=[int(x) for x in sb["team_b"].split(",") if x],
        set_scores=set_scores,
        reporter=sb["referee_id"]
    )
    await db.set_status(scoreboard_id, "complete")
    log.info("Finalized scoreboard match id=%s -> match_id=%s winner=%s", scoreboard_id, match_id, winner)

    # Option A: send through existing verification flow
    await notify_verification(match_id)

    # Option B (if ref == verifier): directly call try_finalize_match(match_id)
    # await try_finalize_match(match_id)

# ---- Views: 6 dropdowns (A/B) for Set 1–3 ----
# We keep the view here to avoid import cycles; you can move to views.py if you prefer.
def _point_options(target: int, cap: int | None) -> list[discord.SelectOption]:
    hi = cap or (30 if target >= 21 else 15)
    return [discord.SelectOption(label=str(i), value=str(i)) for i in range(0, hi + 1)]

class _PointsSelect(discord.ui.Select):
    def __init__(self, set_idx: int, side: str, target: int, cap: int | None):
        self.set_idx, self.side = set_idx, side
        opts = _point_options(target, cap)
        ph = f"Set {set_idx} — {'A' if side=='A' else 'B'} points"
        super().__init__(placeholder=ph, min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        self.view.choices.setdefault(self.set_idx, {"A": None, "B": None})
        self.view.choices[self.set_idx][self.side] = int(self.values[0])
        await interaction.response.defer()

class PointsScoreView(discord.ui.View):
    """6 dropdowns: S1A, S1B, S2A, S2B, S3A, S3B"""
    def __init__(self, target: int, cap: int | None, on_submit):
        super().__init__(timeout=180)
        self.target, self.cap, self.on_submit = target, cap, on_submit
        self.choices: dict[int, dict[str, int | None]] = {}
        for s in (1, 2, 3):
            self.add_item(_PointsSelect(s, "A", target, cap))
            self.add_item(_PointsSelect(s, "B", target, cap))

    def _min_two_sets_filled(self) -> bool:
        done = 0
        for s in (1, 2, 3):
            v = self.choices.get(s)
            if v and v.get("A") is not None and v.get("B") is not None:
                done += 1
        return done >= 2

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success)
    async def submit(self, _button, interaction: discord.Interaction):
        if not self._min_two_sets_filled():
            return await interaction.response.send_message(
                "Please select scores for at least **two** sets.",
                ephemeral=True
            )
        set_scores: list[dict] = []
        for i in (1, 2, 3):
            v = self.choices.get(i)
            if v and v.get("A") is not None and v.get("B") is not None:
                set_scores.append({"A": int(v["A"]), "B": int(v["B"])})
        await self.on_submit(interaction, set_scores)

# --- Discord events ---
@bot.event
async def on_ready():
    await db.init_db(DATABASE_PATH)

    if DATABASE_PATH.startswith("file::memory:") or DATABASE_PATH == ":memory:":
        log.warning("Ephemeral DB mode active: data will NOT persist between restarts")

    # Sync commands
    if TEST_MODE and TEST_GUILD_ID:
        await tree.sync(guild=discord.Object(id=TEST_GUILD_ID))
        log.info("Commands synced to test guild %s", TEST_GUILD_ID)
    else:
        await tree.sync()
        log.info("Commands synced globally")

    status = "Badminton 🏸 [TEST MODE]" if TEST_MODE else "Badminton 🏸"
    await bot.change_presence(activity=discord.Game(name=status))
    log.info("Bot ready as %s | guilds=%s | DB=%s", bot.user, len(bot.guilds), DATABASE_PATH)

# Reaction-based verification
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not bot.user or payload.user_id == bot.user.id:
        return

    # SCOREBOARD BRANCH
    sb_msg_row = await db.get_scoreboard_by_message(payload.message_id)
    if sb_msg_row:
        async with sb_lock(sb_msg_row["scoreboard_id"]):
            guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
            ch = await bot.fetch_channel(payload.channel_id)
            msg = await ch.fetch_message(payload.message_id)

            sb = await db.get_scoreboard(sb_msg_row["scoreboard_id"])  # authoritative
            # Only referee can press
            if payload.user_id != sb["referee_id"]:
                try:
                    await msg.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
                except Exception:
                    pass
                return

            # UX: remove the reaction the ref just tapped
            try:
                await msg.remove_reaction(payload.emoji, payload.member or discord.Object(id=payload.user_id))
            except Exception:
                pass

            emoji = str(payload.emoji)
            s = await db.get_set(sb["id"], sb_msg_row["set_no"])
            if s:
                a, b = int(s["a_points"]), int(s["b_points"])
            else:
                a, b = 0, 0
            changed = False

            if emoji == EMOJI_A_PLUS:
                a += 1; changed = True
                await db.record_play(sb["id"], sb_msg_row["set_no"], "A", +1)
                await db.set_serve_side(sb["id"], "A")
            elif emoji == EMOJI_B_PLUS:
                b += 1; changed = True
                await db.record_play(sb["id"], sb_msg_row["set_no"], "B", +1)
                await db.set_serve_side(sb["id"], "B")
            elif emoji == EMOJI_UNDO:
                lp = await db.last_play(sb["id"], sb_msg_row["set_no"])
                if lp:
                    if lp["side"] == "A": a = max(0, a-1)
                    else: b = max(0, b-1)
                    await db.delete_last_play(sb["id"], sb_msg_row["set_no"])
                    changed = True
            elif emoji == EMOJI_SERVE:
                current = sb.get("serve_side")
                await db.set_serve_side(sb["id"], ("B" if current == "A" else "A"))
            elif emoji == EMOJI_NEXT:
                # force next handled by advance if current finished
                pass
            elif emoji == EMOJI_DONE:
                await finalize_scoreboard_match(sb["id"])
                return

            if changed:
                await db.upsert_set(sb["id"], sb_msg_row["set_no"], a, b, None)

            # Advance or finalize if set complete
            advanced_or_final = await _advance_if_needed(payload, msg, sb, sb_msg_row)
            if advanced_or_final:
                return

            # Otherwise update inline (no pin)
            await edit_scoreboard_message(msg, sb["id"], sb_msg_row["set_no"])
            return

    # VERIFICATION BRANCH - fall through to existing verification reactions (✅/❌)
    row = await db.get_verification_message(payload.message_id)
    if not row:
        return
    if payload.user_id != row["user_id"]:
        return

    emoji = str(payload.emoji)
    if   emoji == EMOJI_APPROVE: decision = "approve"
    elif emoji == EMOJI_REJECT:  decision = "reject"
    else: return

    if not await has_accepted_tos_safe(payload.user_id):
        ch = await bot.fetch_channel(payload.channel_id)
        try:
            msg = await ch.fetch_message(payload.message_id)
            await msg.reply(
                "Please run `/agree_tos name:<Your Name>` first, then react again.",
                mention_author=False,
                allowed_mentions=ALLOWED_MENTIONS
            )
        except Exception:
            pass
        return

    tos = await db.get_tos(payload.user_id)
    signed_name = (tos["signed_name"] if tos and tos.get("signed_name") else None)
    if not signed_name:
        guild = bot.get_guild(row["guild_id"]) if row["guild_id"] else None
        member = guild.get_member(payload.user_id) if guild else None
        signed_name = (member.display_name if member else "Unknown")[:60]

    await db.add_signature(row["match_id"], payload.user_id, decision, signed_name)
    await db.delete_verification_message(payload.message_id)

    try:
        ch = await bot.fetch_channel(payload.channel_id)
        msg = await ch.fetch_message(payload.message_id)
        await msg.reply(
            f"Verification recorded as `{signed_name}` ({decision}).",
            mention_author=False,
            allowed_mentions=ALLOWED_MENTIONS
        )
    except Exception:
        pass

    await try_finalize_match(row["match_id"])

# --- Commands ---
@tree.command(name="ping", description="Replies with pong")
async def ping(inter: discord.Interaction):
    await inter.response.send_message("pong")

@tree.command(name="agree_tos", description="Agree to the Terms and record your name")
@app_commands.describe(name="Your name as you want it recorded")
async def agree_tos(inter: discord.Interaction, name: str):
    await db.set_tos_accepted(inter.user.id, version="v1", signed_name=(name or "").strip()[:60])
    await inter.response.send_message(
        f"**ToS accepted.** Recorded name: `{(name or '').strip()[:60]}`",
        ephemeral=True
    )

# Leaderboard (fixed limit handling)
@tree.command(name="leaderboard", description="Show top players by rating")
@app_commands.describe(limit="How many players to show (1-50)")
async def leaderboard(inter: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 20):
    n = int(limit)
    rows = await db.top_players(getattr(inter.guild, "id", None), n)
    if not rows:
        return await inter.response.send_message("No players found yet.", ephemeral=True)

    lines = [f"**🏆 Leaderboard (Top {n})**"]
    for i, r in enumerate(rows, start=1):
        uid, name, rating, w, l = r["user_id"], r["username"], r["rating"], r["wins"], r["losses"]
        mention = f"<@{uid}>"
        lines.append(f"{i}. {mention} — {name} — {rating:.1f} ({w}-{l})")
    await inter.response.send_message("\n".join(lines), allowed_mentions=ALLOWED_MENTIONS)

# Stats
@tree.command(name="stats", description="Show player statistics")
@app_commands.describe(user="The user to show stats for")
async def stats(inter: discord.Interaction, user: discord.User):
    await inter.response.defer(ephemeral=True)

    async with aiosqlite.connect(db.DB_PATH) as _conn:
        _conn.row_factory = aiosqlite.Row
        async with _conn.execute("SELECT * FROM players WHERE user_id = ?", (user.id,)) as cur:
            row = await cur.fetchone()
            player = dict(row) if row else None

    if not player:
        display = user.display_name if getattr(user, "display_name", None) else user.name
        return await inter.followup.send(f"📊 {display} has no games recorded yet.", ephemeral=True)

    total_matches = player["wins"] + player["losses"]
    win_rate = (player["wins"] / total_matches * 100) if total_matches > 0 else 0

    matches = await db.recent_matches(guild_id=inter.guild_id or 0, user_id=user.id, limit=5)

    rating_str = f"{player['rating']:.1f}"
    wl_str = f"{player['wins']}-{player['losses']}"
    win_rate_str = f"{win_rate:.1f}%"
    kv_lines = [
        f"{fmt.bold('Rating')}: {fmt.code(rating_str)}",
        f"{fmt.bold('Record')}: {fmt.code(wl_str)} ({fmt.code(win_rate_str)})",
        f"{fmt.bold('Total Matches')}: {fmt.code(str(total_matches))}",
    ]

    recent_block = ""
    if matches:
        headers = ["Mode", "Team", "Sets", "Result"]
        rows = []
        for m in matches:
            mode = str(m.get("mode", ""))
            team_a_ids = [int(x) for x in (m.get("team_a") or "").split(",") if x]
            team_b_ids = [int(x) for x in (m.get("team_b") or "").split(",") if x]
            winner = m.get("winner")
            try:
                set_scores = json.loads(m.get("set_scores") or "[]")
                sets_str = fmt.score_sets(set_scores) if set_scores else ""
            except Exception:
                sets_str = ""
            if not sets_str:
                sets_str = str(m.get("set_winners") or "")
            user_team = "A" if user.id in team_a_ids else "B"
            result = "WIN" if (winner == user_team) else "LOSS"
            rows.append([mode, f"Team {user_team}", sets_str, result])
        # mono table (fmt.mono_table) is optional; keep it simple here
        recent_block = "\n".join(f"- {a} | {b} | {c} | {d}" for a, b, c, d in rows)
    else:
        recent_block = "*No recent matches found.*"

    display = user.display_name if getattr(user, "display_name", None) else user.name
    msg = f"## 📊 Stats for {display}\n\n" + "\n".join(kv_lines) + f"\n\n**Recent Matches:**\n{recent_block}"
    await inter.followup.send(msg, allowed_mentions=ALLOWED_MENTIONS, ephemeral=True)

# ---- Singles (players, then 6 dropdowns) ----
@tree.command(name="match_singles", description="Report a singles match (pick players, then select scores)")
@app_commands.describe(a="Player A", b="Player B", target="Target points (11 or 21)")
@app_commands.choices(target=[
    app_commands.Choice(name="21", value=21),
    app_commands.Choice(name="11", value=11),
])
async def match_singles(inter: discord.Interaction, a: discord.User, b: discord.User, target: int = 21):
    if not await require_tos(inter):
        return
    cap = derive_cap(target)

    async def on_submit(i2: discord.Interaction, set_scores: list[dict]):
        try:
            # Validates per-set and determines winner & points
            _winner, _sa, _sb, _pts_a, _pts_b = match_winner(set_scores, target=target, win_by=POINTS_WIN_BY, cap=cap)
        except Exception as e:
            return await i2.response.send_message(f"Invalid scores: {e}", ephemeral=True)

        mid = await db.insert_pending_match_points(
            guild_id=inter.guild_id or 0,
            mode="1v1",
            team_a=[a.id],
            team_b=[b.id],
            set_scores=set_scores,
            reporter=inter.user.id,
            target_points=target
        )
        await notify_verification(mid)
        await i2.response.edit_message(content=f"Match #{mid} created. Waiting for approvals.", view=None)

    # Pager-based score picker view
    try:
        from views import PointsScorePagerView  # local views module
    except Exception:
        # Fallback to legacy if import fails
        PointsScorePagerView = PointsScoreView  # type: ignore
    view = PointsScorePagerView(target=target, cap=cap, on_submit=on_submit)
    await inter.response.send_message(
        content=f"Select set scores for {a.mention} vs {b.mention} (to {target}, win by {POINTS_WIN_BY}).",
        view=view, ephemeral=True, allowed_mentions=ALLOWED_MENTIONS
    )

# ---- Doubles (players, then 6 dropdowns) ----
@tree.command(name="match_doubles", description="Report a 2v2 match (pick players, then select scores)")
@app_commands.describe(
    a1="Team A - Player 1", a2="Team A - Player 2",
    b1="Team B - Player 1", b2="Team B - Player 2",
    target="Target points (11 or 21)"
)
@app_commands.choices(target=[
    app_commands.Choice(name="21", value=21),
    app_commands.Choice(name="11", value=11),
])
async def match_doubles(
    inter: discord.Interaction,
    a1: discord.User, a2: discord.User,
    b1: discord.User, b2: discord.User,
    target: int = 21
):
    if not await require_tos(inter):
        return
    all_ids = [a1.id, a2.id, b1.id, b2.id]
    # Allow bot to be used as random/guest player, so filter it out from uniqueness check
    bot_id = _get_bot_id()
    non_bot_ids = [uid for uid in all_ids if uid != bot_id] if bot_id else all_ids
    # Check if there are duplicate human players (excluding bot)
    if len(set(non_bot_ids)) < len(non_bot_ids):
        return await inter.response.send_message("❌ All players (excluding bot) must be different.", ephemeral=True)
    cap = derive_cap(target)

    async def on_submit(i2: discord.Interaction, set_scores: list[dict]):
        try:
            _winner, _sa, _sb, _pts_a, _pts_b = match_winner(set_scores, target=target, win_by=POINTS_WIN_BY, cap=cap)
        except Exception as e:
            return await i2.response.send_message(f"Invalid scores: {e}", ephemeral=True)

        mid = await db.insert_pending_match_points(
            guild_id=inter.guild_id or 0,
            mode="2v2",
            team_a=[a1.id, a2.id],
            team_b=[b1.id, b2.id],
            set_scores=set_scores,
            reporter=inter.user.id,
            target_points=target
        )
        await notify_verification(mid)
        await i2.response.edit_message(content=f"Match #{mid} created. Waiting for approvals.", view=None)

    # Pager-based score picker view
    try:
        from views import PointsScorePagerView  # local views module
    except Exception:
        PointsScorePagerView = PointsScoreView  # type: ignore
    view = PointsScorePagerView(target=target, cap=cap, on_submit=on_submit)
    def disp(u: discord.User) -> str:
        return getattr(u, "display_name", None) or u.name
    await inter.response.send_message(
        content=f"Select set scores for {disp(a1)}/{disp(a2)} vs {disp(b1)}/{disp(b2)} (to {target}, win by {POINTS_WIN_BY}).",
        view=view, ephemeral=True, allowed_mentions=ALLOWED_MENTIONS
    )

# Verify (command)
@tree.command(name="verify", description="Verify a pending match")
@app_commands.describe(
    decision="approve or reject",
    name="Your name as you want it recorded (optional)",
    match_id="Match ID (optional; defaults to latest pending you’re in)"
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
    await db.init_db(DATABASE_PATH)
    if not await has_accepted_tos_safe(inter.user.id):
        return await inter.response.send_message(
            "Please run `/agree_tos name:<Your Name>` first, then verify again.",
            ephemeral=True
        )

    await inter.response.defer(ephemeral=True)

    # Pick latest pending if no ID provided
    if match_id is None:
        row = await db.latest_pending_for_user(inter.guild_id or 0, inter.user.id)
        if not row:
            return await inter.followup.send("No pending matches to verify.", ephemeral=True)
        match_id = row["id"]

    name = (name or (inter.user.display_name or inter.user.name))[:60]

    match = await db.get_match(match_id)
    if not match:
        return await inter.followup.send(f"❌ Match ID {match_id} not found.", ephemeral=True)

    participants = await db.get_match_participant_ids(match_id)
    if inter.user.id not in participants:
        return await inter.followup.send("❌ You are not a participant in this match.", ephemeral=True)
    if inter.user.id == match.get("reporter"):
        return await inter.followup.send("❌ The reporter cannot verify their own match.", ephemeral=True)

    await db.add_signature(match_id, inter.user.id, decision, name)
    await try_finalize_match(match_id)

    hint = fmt.block("/verify decision:approve name:YourName\n/verify decision:reject name:YourName", "md")
    msg = (
        f"{fmt.bold('Verification recorded')}\n"
        f"Match: {fmt.code(str(match_id))}\n"
        f"Decision: {fmt.code(decision)}\n"
        f"Name: {fmt.code(name)}"
    )
    await inter.followup.send(msg + "\n\n" + hint, ephemeral=True, allowed_mentions=ALLOWED_MENTIONS)

# Pending
@tree.command(name="pending", description="List your matches awaiting your verification")
async def pending(inter: discord.Interaction):
    if not await require_tos(inter):
        return

    await inter.response.defer(ephemeral=True)
    guild_id = inter.guild_id or 0
    user_id = inter.user.id

    matches = await db.list_pending_for_user(user_id, guild_id)
    if not matches:
        return await inter.followup.send("You have no pending matches to verify!", ephemeral=True)

    # Filter out already-signed
    unsigned = []
    for m in matches:
        sigs = await db.get_signatures(m["id"])
        if any(s["user_id"] == user_id for s in sigs):
            continue
        unsigned.append((m, sigs))
    if not unsigned:
        return await inter.followup.send("You have no pending matches to verify!", ephemeral=True)

    headers = ["Match", "Mode", "Teams", "Sets"]
    rows = []
    for m, _ in unsigned:
        mid = m["id"]
        mode = m.get("mode", "")
        a_ids = [int(x) for x in (m.get("team_a") or "").split(",") if x]
        b_ids = [int(x) for x in (m.get("team_b") or "").split(",") if x]
        a_names = [await fmt.display_name_or_cached(bot, inter.guild, uid, fallback=f"User{uid}") for uid in a_ids]
        b_names = [await fmt.display_name_or_cached(bot, inter.guild, uid, fallback=f"User{uid}") for uid in b_ids]
        try:
            s = json.loads(m.get("set_scores") or "[]")
            sets_str = fmt.score_sets(s) if s else "N/A"
        except Exception:
            sets_str = "N/A"
        rows.append([f"#{mid}", str(mode), f"{'/'.join(a_names)} vs {'/'.join(b_names)}", sets_str])

    table = fmt.mono_table(rows, headers=headers)
    autofill = inter.user.display_name if getattr(inter.user, "display_name", None) else inter.user.name
    approve_box = fmt.block(f"/verify match_id:<ID> decision:approve name:{autofill}", "md")
    reject_box  = fmt.block(f"/verify match_id:<ID> decision:reject  name:{autofill}", "md")
    await inter.followup.send(table + "\nApprove:\n" + approve_box + "\nReject:\n" + reject_box,
                              ephemeral=True, allowed_mentions=ALLOWED_MENTIONS)

# Scoreboard
@tree.command(name="scoreboard", description="Start a live scoreboard controlled by reactions")
@app_commands.describe(
    mode="Singles or Doubles",
    target="Target points (11 or 21)",
    a="Player A (singles) or Team A - Player 1",
    a2="Team A - Player 2 (doubles only)",
    b="Player B (singles) or Team B - Player 1",
    b2="Team B - Player 2 (doubles only)",
    referee="Referee who can press reactions (defaults to you)"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Singles (1v1)", value="1v1"),
    app_commands.Choice(name="Doubles (2v2)", value="2v2"),
])
@app_commands.choices(target=[
    app_commands.Choice(name="21", value=21),
    app_commands.Choice(name="11", value=11),
])
async def scoreboard(
    inter: discord.Interaction,
    mode: str,
    target: int,
    a: discord.User,
    b: discord.User,
    a2: discord.User | None = None,
    b2: discord.User | None = None,
    referee: discord.User | None = None
):
    if mode == "2v2" and (not a2 or not b2):
        return await inter.response.send_message("For doubles, please provide A2 and B2.", ephemeral=True)

    ref = referee or inter.user
    cap = 30 if target >= 21 else 15

    team_a_ids = [a.id] + ([a2.id] if (mode == "2v2" and a2) else [])
    team_b_ids = [b.id] + ([b2.id] if (mode == "2v2" and b2) else [])

    sb_id = await db.create_scoreboard(inter.guild_id or 0, mode, target, cap, team_a_ids, team_b_ids, ref.id)
    # ensure set 1 row exists
    await db.upsert_set(sb_id, 1, 0, 0, None)

    # Post Set 1 message and add reactions
    msg = await post_scoreboard_message(inter, sb_id, set_no=1)  # implement in next step
    await inter.response.send_message(f"Started scoreboard #{sb_id} (ref: {ref.mention}). See set message below.", ephemeral=True)

# Scoreboard referee change
@tree.command(name="scoreboard_referee", description="Change the referee for a live scoreboard")
@app_commands.describe(
    scoreboard_id="The ID of the scoreboard",
    referee="The new referee who can control reactions"
)
async def scoreboard_referee(
    inter: discord.Interaction,
    scoreboard_id: int,
    referee: discord.User
):
    sb = await db.get_scoreboard(scoreboard_id)
    if not sb:
        return await inter.response.send_message(
            f"Scoreboard #{scoreboard_id} not found.",
            ephemeral=True
        )
    
    if sb["status"] != "live":
        return await inter.response.send_message(
            f"Scoreboard #{scoreboard_id} is not live (status: {sb['status']}).",
            ephemeral=True
        )
    
    # Only current referee or admins can change referee
    is_admin = False
    if inter.guild and isinstance(inter.user, discord.Member):
        is_admin = inter.user.guild_permissions.administrator
    
    if inter.user.id != sb["referee_id"] and not is_admin:
        return await inter.response.send_message(
            "Only the current referee or server admins can change the referee.",
            ephemeral=True
        )
    
    # Update referee
    await db.set_referee(scoreboard_id, referee.id)
    
    log.info("Changed scoreboard #%s referee from %s to %s", scoreboard_id, sb["referee_id"], referee.id)
    await inter.response.send_message(
        f"✅ Scoreboard #{scoreboard_id} referee changed to {referee.mention}.",
        ephemeral=True,
        allowed_mentions=ALLOWED_MENTIONS
    )

# --- Verification utilities ---
async def notify_verification(match_id: int):
    match = await db.get_match(match_id)
    if not match:
        log.error("Notify failed: match not found id=%s", match_id)
        return

    participants = await db.get_match_participant_ids(match_id)
    reporter = match.get("reporter")
    # Filter out bot from non-reporters (bot doesn't need to verify)
    bot_id = _get_bot_id()
    non_reporters = [uid for uid in participants if uid != reporter and uid != bot_id]

    guild_id = match.get("guild_id")
    guild = bot.get_guild(guild_id) if guild_id else None

    # Teams
    a_ids = [int(x) for x in (match.get("team_a") or "").split(",") if x]
    b_ids = [int(x) for x in (match.get("team_b") or "").split(",") if x]
    a_names = [await fmt.display_name_or_cached(bot, guild, uid, fallback=f"User{uid}") for uid in a_ids]
    b_names = [await fmt.display_name_or_cached(bot, guild, uid, fallback=f"User{uid}") for uid in b_ids]

    # Sets
    try:
        set_scores = json.loads(match.get("set_scores") or "[]")
    except Exception:
        set_scores = []
    sets_line = fmt.score_sets(set_scores) if set_scores else "N/A"

    title = fmt.bold(f"Please verify Match #{match_id}")
    tip = fmt.block("/verify\n/verify decision:approve name:YourName\n/verify decision:reject name:YourName", "md")
    msg = f"{title}\n{'/'.join(a_names)} vs {'/'.join(b_names)}\n{sets_line}\nReact {EMOJI_APPROVE} to approve or {EMOJI_REJECT} to reject.\n\n{tip}"

    for user_id in non_reporters:
        try:
            user = await bot.fetch_user(user_id)
            dm = await user.send(msg, allowed_mentions=ALLOWED_MENTIONS)
            try:
                await dm.add_reaction(EMOJI_APPROVE)
                await dm.add_reaction(EMOJI_REJECT)
            except Exception:
                pass
            await db.record_verification_message(dm.id, match_id, guild_id, user_id)
        except discord.Forbidden:
            # Fallback to a channel we can post in
            try:
                channel = getattr(guild, "system_channel", None)
                if channel is None:
                    for ch in getattr(guild, "channels", []) or []:
                        if isinstance(ch, discord.TextChannel) and ch.permissions_for(ch.guild.me).send_messages:
                            channel = ch
                            break
                if channel and isinstance(channel, (discord.TextChannel, discord.Thread)):
                    post = await channel.send(msg, allowed_mentions=ALLOWED_MENTIONS)
                    try:
                        await post.add_reaction(EMOJI_APPROVE)
                        await post.add_reaction(EMOJI_REJECT)
                    except Exception:
                        pass
                    await db.record_verification_message(post.id, match_id, guild_id, user_id)
            except Exception:
                log.debug("Channel fallback failed for user=%s match=%s", user_id, match_id, exc_info=True)

async def try_finalize_match(match_id: int):
    """
    Finalize when:
      - any 'reject' -> rejected
      - singles: 1 approval (opponent)
      - doubles: approvals from all 3 non-reporters
    On verify: update ratings via points-share Elo and set status='verified'.
    """
    match = await db.get_match(match_id)
    if not match:
        log.error("try_finalize: match not found id=%s", match_id)
        return

    participants = await db.get_match_participant_ids(match_id)
    reporter = match.get("reporter")
    sigs = await db.get_signatures(match_id)

    # Rejected?
    if any(s.get("decision") == "reject" for s in sigs):
        await db.set_match_status(match_id, "rejected")
        log.info("Match #%s rejected by participant(s)", match_id)
        return

    # Filter out bot from non-reporters (bot doesn't need to verify)
    bot_id = _get_bot_id()
    non_reporters = [pid for pid in participants if pid != reporter and pid != bot_id]
    required = non_reporters[:1] if match.get("mode") == "1v1" else non_reporters
    approved_users = {s.get("user_id") for s in sigs if s.get("decision") == "approve"}
    if not all(uid in approved_users for uid in required):
        return  # still pending

    # Compute outcome + rating updates
    try:
        set_scores = json.loads(match.get("set_scores") or "[]")
    except Exception:
        set_scores = []
    target_points = match.get("target_points") or POINTS_TARGET_DEFAULT
    cap = derive_cap(target_points)

    winner, _sa, _sb, pts_a, pts_b = match_winner(set_scores, target_points, POINTS_WIN_BY, cap)
    share_a = pts_a / max(1, (pts_a + pts_b))

    a_ids = [int(x) for x in (match.get("team_a") or "").split(",") if x]
    b_ids = [int(x) for x in (match.get("team_b") or "").split(",") if x]

    bot_id = _get_bot_id()
    
    # Get or create players, using guest rating for bot
    players_a = []
    for uid in a_ids:
        if uid == bot_id:
            players_a.append(_create_guest_player(uid))
        else:
            players_a.append(await db.get_or_create_player(uid, f"User{uid}"))
    
    players_b = []
    for uid in b_ids:
        if uid == bot_id:
            players_b.append(_create_guest_player(uid))
        else:
            players_b.append(await db.get_or_create_player(uid, f"User{uid}"))
    
    ratings_a = [p["rating"] for p in players_a]
    ratings_b = [p["rating"] for p in players_b]

    new_ratings_a, new_ratings_b = team_points_update(ratings_a, ratings_b, share_a, k=K_FACTOR)

    # Update ratings only for non-bot players
    for i, p in enumerate(players_a):
        if p["user_id"] != bot_id:
            await db.update_player(p["user_id"], new_ratings_a[i], won=(winner == "A"))
    for i, p in enumerate(players_b):
        if p["user_id"] != bot_id:
            await db.update_player(p["user_id"], new_ratings_b[i], won=(winner == "B"))

    await db.finalize_points(match_id, winner, set_scores, pts_a, pts_b)
    await db.set_match_status(match_id, "verified")
    log.info("Match #%s finalized (winner=%s)", match_id, winner)

# --- Entrypoint ---
if __name__ == "__main__":
    if not TOKEN:
        log.error("DISCORD_TOKEN not set. Put it in environment or .env")
        raise SystemExit(1)

    # Ensure schema before login (on_ready will also ensure)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db.init_db(DATABASE_PATH))
        else:
            loop.run_until_complete(db.init_db(DATABASE_PATH))
    except Exception:
        log.debug("Pre-start DB init failed", exc_info=True)

    bot.run(TOKEN)