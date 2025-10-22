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
from feather_rank.rules import match_winner, valid_set
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

K_FACTOR = int(os.getenv("K_FACTOR", "32"))

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

# ToS text
TOS_TEXT = (
    "By using this bot you agree to fair-play. "
    "False reports may be rejected or reverted. "
    "Your Discord ID and chosen display name are stored for match and verification records. "
    "Type /agree_tos to continue."
)

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
    if len(set(all_ids)) != 4:
        return await inter.response.send_message("❌ All four players must be different.", ephemeral=True)
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

# --- Verification utilities ---
async def notify_verification(match_id: int):
    match = await db.get_match(match_id)
    if not match:
        log.error("Notify failed: match not found id=%s", match_id)
        return

    participants = await db.get_match_participant_ids(match_id)
    reporter = match.get("reporter")
    non_reporters = [uid for uid in participants if uid != reporter]

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

    non_reporters = [pid for pid in participants if pid != reporter]
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

    players_a = [await db.get_or_create_player(uid, f"User{uid}") for uid in a_ids]
    players_b = [await db.get_or_create_player(uid, f"User{uid}") for uid in b_ids]
    ratings_a = [p["rating"] for p in players_a]
    ratings_b = [p["rating"] for p in players_b]

    new_ratings_a, new_ratings_b = team_points_update(ratings_a, ratings_b, share_a, k=K_FACTOR)

    for i, p in enumerate(players_a):
        await db.update_player(p["user_id"], new_ratings_a[i], won=(winner == "A"))
    for i, p in enumerate(players_b):
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