# --- Points and Set Scores Helpers ---
import json
from .logging_config import get_logger
log = get_logger(__name__)

async def insert_pending_match_points(
    guild_id: int,
    mode: str,
    team_a: list[int],
    team_b: list[int],
    set_scores: list[dict],
    reporter: int,
    target_points: int = 21
) -> int:
    """Insert a pending match with set_scores and points columns, return its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        team_a_str = ",".join(map(str, team_a))
        team_b_str = ",".join(map(str, team_b))
        set_scores_str = json.dumps(set_scores)
        try:
            cursor = await db.execute(
                """
                INSERT INTO matches (guild_id, mode, team_a, team_b, set_scores, created_at, status, reporter, created_by, points_a, points_b, set_winners, winner, target_points)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, 0, NULL, NULL, ?)
                """,
                (guild_id, mode, team_a_str, team_b_str, set_scores_str, now, reporter, reporter, target_points)
            )
            await db.commit()
        except aiosqlite.OperationalError as e:
            if "no such table: matches" in str(e):
                # Ensure schema then retry once
                await init_db(DB_PATH)
                cursor = await db.execute(
                    """
                    INSERT INTO matches (guild_id, mode, team_a, team_b, set_scores, created_at, status, reporter, created_by, points_a, points_b, set_winners, winner, target_points)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, 0, NULL, NULL, ?)
                    """,
                    (guild_id, mode, team_a_str, team_b_str, set_scores_str, now, reporter, reporter, target_points)
                )
                await db.commit()
            else:
                raise
    match_id = cursor.lastrowid if cursor.lastrowid is not None else -1
    log.debug("Inserted pending points match id=%s guild=%s mode=%s A=%s B=%s target=%s", match_id, guild_id, mode, team_a_str, team_b_str, target_points)
    return match_id

async def finalize_points(
    match_id: int,
    winner: str,
    set_scores: list[dict],
    points_a: int,
    points_b: int
) -> None:
    """Finalize a match: set winner, set_scores, points_a, points_b."""
    async with aiosqlite.connect(DB_PATH) as db:
        set_scores_str = json.dumps(set_scores)
        await db.execute(
            """
            UPDATE matches
            SET winner = ?, set_scores = ?, points_a = ?, points_b = ?, status = 'verified'
            WHERE id = ?
            """,
            (winner, set_scores_str, points_a, points_b, match_id)
        )
        await db.commit()
    log.debug("Finalized match id=%s winner=%s points A=%s B=%s", match_id, winner, points_a, points_b)

async def get_set_scores(match_id: int) -> list[dict]:
    """Get set_scores (as list of dict) for a match."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT set_scores FROM matches WHERE id = ?", (match_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    scores = json.loads(row[0])
                    log.debug("Fetched set_scores for match id=%s -> %s", match_id, scores)
                    return scores
                except Exception:
                    return []
            return []
# --- Pending Match and Signature/ToS Helpers ---
from typing import Any

async def insert_pending_match(
    guild_id: int,
    mode: str,
    team_a: list[int],
    team_b: list[int],
    set_winners: list[str],
    winner: str,
    reporter: int
) -> int:
    """Insert a pending match and return its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        team_a_str = ",".join(map(str, team_a))
        team_b_str = ",".join(map(str, team_b))
        set_winners_str = ",".join(set_winners)
        cursor = await db.execute(
            """
            INSERT INTO matches (guild_id, mode, team_a, team_b, set_winners, winner, created_at, status, reporter, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (guild_id, mode, team_a_str, team_b_str, set_winners_str, winner, now, reporter, reporter)
        )
        await db.commit()
    match_id = cursor.lastrowid if cursor.lastrowid is not None else -1
    log.debug("Inserted pending match id=%s guild=%s mode=%s A=%s B=%s winner=%s", match_id, guild_id, mode, team_a_str, team_b_str, winner)
    return match_id

async def add_signature(match_id: int, user_id: int, decision: str, signed_name: str | None) -> None:
    """Add or update a match signature."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        await db.execute(
            """
            INSERT OR REPLACE INTO match_signatures (match_id, user_id, decision, signed_name, signed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (match_id, user_id, decision, signed_name or "", now)
        )
        await db.commit()
    log.debug("Signature recorded match=%s user=%s decision=%s name=%s", match_id, user_id, decision, signed_name)

async def get_match(match_id: int) -> Any:
    """Get a match row by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)) as cursor:
            row = await cursor.fetchone()
            data = dict(row) if row else None
            log.debug("Fetched match id=%s -> found=%s", match_id, bool(data))
            return data

async def get_match_participant_ids(match_id: int) -> list[int]:
    """Get all participant user IDs for a match."""
    match = await get_match(match_id)
    if not match:
        return []
    ids = []
    for team in (match['team_a'], match['team_b']):
        ids.extend(int(x) for x in team.split(",") if x)
    return ids

async def get_signatures(match_id: int) -> list[dict]:
    """Get all signatures for a match."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM match_signatures WHERE match_id = ?", (match_id,)) as cursor:
            rows = await cursor.fetchall()
            out = [dict(row) for row in rows]
            log.debug("Fetched %s signatures for match=%s", len(out), match_id)
            return out

async def set_match_status(match_id: int, status: str) -> None:
    """Set the status of a match."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE matches SET status = ? WHERE id = ?", (status, match_id))
        await db.commit()
    log.debug("Set match status id=%s status=%s", match_id, status)

async def list_pending_for_user(user_id: int, guild_id: int) -> list[dict]:
    """List all pending matches for a user in a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM matches
            WHERE guild_id = ? AND status = 'pending' AND (
                team_a LIKE ? OR team_a LIKE ? OR team_a LIKE ? OR
                team_b LIKE ? OR team_b LIKE ? OR team_b LIKE ?
            )
            ORDER BY created_at DESC
            """,
            (
                guild_id,
                f"{user_id},%", f"%,{user_id},%", f"%,{user_id}",
                f"{user_id},%", f"%,{user_id},%", f"%,{user_id}"
            )
        ) as cursor:
            rows = await cursor.fetchall()
            out = [dict(row) for row in rows]
            log.debug("Pending matches for user=%s guild=%s -> %s", user_id, guild_id, len(out))
            return out

async def latest_pending_for_user(guild_id: int, user_id: int) -> dict | None:
    """Return the most recent pending match for a user in a guild they haven't signed yet.

    Conditions:
    - matches.status = 'pending'
    - user_id appears in team_a or team_b (CSV stored IDs; supports single-member equality)
    - reporter != user_id (cannot be the reporter)
    - user has not signed in match_signatures for that match
    Ordered by id DESC, limited to 1.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        like_params = (
            f"{user_id},%",
            f"%,{user_id},%",
            f"%,{user_id}",
        )
        query = (
            """
            SELECT * FROM matches m
            WHERE m.guild_id = ?
              AND m.status = 'pending'
              AND m.reporter != ?
              AND (
                  m.team_a LIKE ? OR m.team_a LIKE ? OR m.team_a LIKE ? OR m.team_a = ? OR
                  m.team_b LIKE ? OR m.team_b LIKE ? OR m.team_b LIKE ? OR m.team_b = ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM match_signatures s
                  WHERE s.match_id = m.id AND s.user_id = ?
              )
            ORDER BY m.id DESC
            LIMIT 1
            """
        )
        params = (
            guild_id,
            user_id,
            *like_params,
            str(user_id),
            *like_params,
            str(user_id),
            user_id,
        )
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def has_accepted_tos(user_id: int) -> bool:
    """Check if a user has accepted the ToS."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM tos_acceptances WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            accepted = bool(row)
            log.debug("has_accepted_tos user=%s -> %s", user_id, accepted)
            return accepted

async def set_tos_accepted(user_id: int, version: str = "v1", signed_name: str | None = None) -> None:
    """Upsert ToS acceptance for a user with version and signed_name."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO tos_acceptances (user_id, accepted_at, version, signed_name)
            VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                accepted_at = excluded.accepted_at,
                version = excluded.version,
                signed_name = COALESCE(excluded.signed_name, tos_acceptances.signed_name)
            """,
            (user_id, version, signed_name)
        )
        await db.commit()
    log.debug("set_tos_accepted user=%s version=%s name=%s", user_id, version, signed_name)

async def get_tos(user_id: int) -> dict | None:
    """Return ToS acceptance row for a user, including signed_name if present."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tos_acceptances WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

import aiosqlite
from datetime import datetime
from typing import Optional

# Helper to check if a table has a column
async def table_has_column(table: str, column: str, db_path: str = "feather_rank.db") -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(f"PRAGMA table_info({table})") as cursor:
            async for row in cursor:
                if row[1] == column:
                    return True
    return False

# Global variable for database path (will be set by init_db)
DB_PATH = "feather_rank.db"

async def init_db(db_path: str = "feather_rank.db"):
    """Initialize the database with required tables and columns."""
    global DB_PATH
    DB_PATH = db_path

    async with aiosqlite.connect(DB_PATH) as db:
        # Create players table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                rating REAL DEFAULT 1500.0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Create matches table (old columns for backward compatibility)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                team_a TEXT NOT NULL,
                team_b TEXT NOT NULL,
                set_winners TEXT,
                winner TEXT,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT CHECK(status IN ('pending','verified','rejected')) NOT NULL DEFAULT 'pending',
                reporter INTEGER NOT NULL
            )
        """)

        # Add new columns to matches if missing
        # set_scores TEXT
        if not await table_has_column("matches", "set_scores", DB_PATH):
            await db.execute("ALTER TABLE matches ADD COLUMN set_scores TEXT")
        # points_a INT DEFAULT 0
        if not await table_has_column("matches", "points_a", DB_PATH):
            await db.execute("ALTER TABLE matches ADD COLUMN points_a INTEGER NOT NULL DEFAULT 0")
        # points_b INT DEFAULT 0
        if not await table_has_column("matches", "points_b", DB_PATH):
            await db.execute("ALTER TABLE matches ADD COLUMN points_b INTEGER NOT NULL DEFAULT 0")
        # target_points INT DEFAULT 21
        if not await table_has_column("matches", "target_points", DB_PATH):
            try:
                await db.execute("ALTER TABLE matches ADD COLUMN target_points INTEGER DEFAULT 21")
            except aiosqlite.OperationalError as e:
                # Ignore duplicate column errors
                if "duplicate column" not in str(e).lower():
                    raise
        
        # Migrate existing tables: make set_winners and winner nullable for point-based matches
        # SQLite doesn't support ALTER COLUMN, so we check if recreation is needed
        try:
            # Test if we can insert with NULL set_winners
            await db.execute("SELECT set_winners FROM matches WHERE set_winners IS NULL LIMIT 1")
        except Exception:
            # Table exists but columns aren't nullable; need to recreate
            log.warning("Migrating matches table schema to support point-based matches...")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS matches_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    team_a TEXT NOT NULL,
                    team_b TEXT NOT NULL,
                    set_winners TEXT,
                    winner TEXT,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT CHECK(status IN ('pending','verified','rejected')) NOT NULL DEFAULT 'pending',
                    reporter INTEGER NOT NULL,
                    set_scores TEXT,
                    points_a INTEGER NOT NULL DEFAULT 0,
                    points_b INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Copy data
            await db.execute("""
                INSERT INTO matches_new 
                SELECT id, guild_id, mode, team_a, team_b, set_winners, winner, created_by, created_at, 
                       status, reporter, set_scores, points_a, points_b
                FROM matches
            """)
            # Drop old and rename
            await db.execute("DROP TABLE matches")
            await db.execute("ALTER TABLE matches_new RENAME TO matches")

        # Try to add status and reporter columns for upgrades (legacy)
        try:
            await db.execute("ALTER TABLE matches ADD COLUMN status TEXT CHECK(status IN ('pending','verified','rejected')) NOT NULL DEFAULT 'pending'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE matches ADD COLUMN reporter INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

        # Create match_signatures table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS match_signatures (
                match_id INTEGER,
                user_id INTEGER,
                decision TEXT CHECK(decision IN ('approve','reject')),
                signed_name TEXT,
                signed_at TEXT,
                PRIMARY KEY(match_id, user_id)
            )
        """)

        # Create tos_acceptances table with defaults and signed_name
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tos_acceptances(
              user_id     INTEGER PRIMARY KEY,
              accepted_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
              version     TEXT NOT NULL DEFAULT 'v1',
              signed_name TEXT
            )
            """
        )

        # Ensure signed_name exists for older DBs
        if not await table_has_column("tos_acceptances", "signed_name", DB_PATH):
            await db.execute("ALTER TABLE tos_acceptances ADD COLUMN signed_name TEXT")

        # Create verification_messages to track DM or channel verification prompts
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_messages(
              message_id INTEGER PRIMARY KEY,
              match_id   INTEGER NOT NULL,
              guild_id   INTEGER,
              user_id    INTEGER NOT NULL,
              created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )

        # Index for faster lookups by match_id
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_verif_match ON verification_messages(match_id)
            """
        )

        await db.commit()
    log.debug("Initialized database at %s", DB_PATH)

async def record_verification_message(message_id: int, match_id: int, guild_id: int | None, user_id: int) -> None:
    """Record a verification message mapping to a match and recipient."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT OR REPLACE INTO verification_messages (message_id, match_id, guild_id, user_id)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, match_id, guild_id, user_id),
            )
            await db.commit()
        except aiosqlite.OperationalError as e:
            if "no such table: verification_messages" in str(e):
                # Create the table and retry once
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS verification_messages (
                        message_id INTEGER PRIMARY KEY,
                        match_id INTEGER NOT NULL,
                        guild_id INTEGER,
                        user_id INTEGER NOT NULL,
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    )
                    """
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_verif_match ON verification_messages(match_id)"
                )
                await db.commit()
                # Retry the insert
                await db.execute(
                    """
                    INSERT OR REPLACE INTO verification_messages (message_id, match_id, guild_id, user_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (message_id, match_id, guild_id, user_id),
                )
                await db.commit()
            else:
                raise
    log.debug("Recorded verification_message id=%s match=%s user=%s guild=%s", message_id, match_id, user_id, guild_id)

async def get_verification_message(message_id: int) -> dict | None:
    """Fetch a verification message row by message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM verification_messages WHERE message_id = ?",
            (message_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def delete_verification_message(message_id: int) -> None:
    """Delete a verification message mapping by message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM verification_messages WHERE message_id = ?",
            (message_id,),
        )
        await db.commit()
    log.debug("Deleted verification_message id=%s", message_id)

async def get_or_create_player(user_id: int, username: str, base_rating: float = 1200) -> dict:
    """Get existing player or create new one."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Try to get existing player
        async with db.execute(
            "SELECT * FROM players WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                player = dict(row)
                log.debug("Fetched existing player user_id=%s rating=%.2f", user_id, player.get("rating", 0))
                return player
        # Create new player
        now = datetime.utcnow().isoformat()
        await db.execute(
            """
            INSERT INTO players (user_id, username, rating, wins, losses, created_at, updated_at)
            VALUES (?, ?, ?, 0, 0, ?, ?)
            """,
            (user_id, username, base_rating, now, now),
        )
        await db.commit()
        # Return the newly created player
        async with db.execute(
            "SELECT * FROM players WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            player = dict(row) if row else {}
            log.debug("Created new player user_id=%s rating=%.2f", user_id, player.get("rating", 0))
            return player

async def update_player(user_id: int, new_rating: float, won: bool):
    """Update player rating and win/loss count."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        
        if won:
            await db.execute("""
                UPDATE players 
                SET rating = ?, wins = wins + 1, updated_at = ?
                WHERE user_id = ?
            """, (new_rating, now, user_id))
        else:
            await db.execute("""
                UPDATE players 
                SET rating = ?, losses = losses + 1, updated_at = ?
                WHERE user_id = ?
            """, (new_rating, now, user_id))
        
        await db.commit()
    log.debug("Updated player user_id=%s rating=%.2f won=%s", user_id, new_rating, won)

async def insert_match(
    guild_id: int,
    mode: str,
    team_a: list[int],
    team_b: list[int],
    set_winners: list[str],
    winner: str,
    created_by: int
) -> int:
    """Insert a new match record and return its ID.

    Note: For legacy set-winner based matches. Reporter is set to created_by.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        # Convert lists to comma-separated strings
        team_a_str = ",".join(map(str, team_a))
        team_b_str = ",".join(map(str, team_b))
        set_winners_str = ",".join(set_winners)
        cursor = await db.execute(
            """
            INSERT INTO matches (guild_id, mode, team_a, team_b, set_winners, winner, created_by, created_at, reporter)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, mode, team_a_str, team_b_str, set_winners_str, winner, created_by, now, created_by),
        )
        await db.commit()
        new_id = cursor.lastrowid if cursor.lastrowid is not None else -1
    log.debug("Inserted match id=%s guild=%s mode=%s", new_id, guild_id, mode)
    return new_id

async def top_players(guild_id: int, limit: int = 10) -> list[dict]:
    """Get top players by rating."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("""
            SELECT * FROM players
            ORDER BY rating DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            out = [dict(row) for row in rows]
            log.debug("Top players query limit=%s -> %s", limit, len(out))
            return out

async def recent_matches(guild_id: int, user_id: Optional[int] = None, limit: int = 10) -> list[dict]:
    """Get recent matches, optionally filtered by user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        if user_id is not None:
            # Filter matches where user_id appears in either team
            async with db.execute(
                """
                SELECT * FROM matches
                WHERE guild_id = ? AND (
                    team_a LIKE ? OR 
                    team_a LIKE ? OR 
                    team_a LIKE ? OR
                    team_b LIKE ? OR 
                    team_b LIKE ? OR 
                    team_b LIKE ?
                )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    guild_id,
                    f"{user_id},%",
                    f"%,{user_id},%",
                    f"%,{user_id}",
                    f"{user_id},%",
                    f"%,{user_id},%",
                    f"%,{user_id}",
                    limit,
                ),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            # Get all recent matches for the guild
            async with db.execute(
                """
                SELECT * FROM matches
                WHERE guild_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()

        out = [dict(row) for row in rows]
        log.debug("Recent matches guild=%s user=%s limit=%s -> %s", guild_id, user_id, limit, len(out))
        return out
