import aiosqlite
from datetime import datetime
from typing import Optional

# Global variable for database path (will be set by init_db)
DB_PATH = "feather_rank.db"

async def init_db(db_path: str = "feather_rank.db"):
    """Initialize the database with required tables."""
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
        
        # Create matches table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                team_a TEXT NOT NULL,
                team_b TEXT NOT NULL,
                set_winners TEXT NOT NULL,
                winner TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
        await db.commit()

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
                return dict(row)
        
        # Create new player
        now = datetime.utcnow().isoformat()
        await db.execute("""
            INSERT INTO players (user_id, username, rating, wins, losses, created_at, updated_at)
            VALUES (?, ?, ?, 0, 0, ?, ?)
        """, (user_id, username, base_rating, now, now))
        await db.commit()
        
        # Return the newly created player
        async with db.execute(
            "SELECT * FROM players WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row)

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

async def insert_match(
    guild_id: int,
    mode: str,
    team_a: list[int],
    team_b: list[int],
    set_winners: list[str],
    winner: str,
    created_by: int
) -> int:
    """Insert a new match record and return its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        
        # Convert lists to comma-separated strings
        team_a_str = ",".join(map(str, team_a))
        team_b_str = ",".join(map(str, team_b))
        set_winners_str = ",".join(set_winners)
        
        cursor = await db.execute("""
            INSERT INTO matches (guild_id, mode, team_a, team_b, set_winners, winner, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, mode, team_a_str, team_b_str, set_winners_str, winner, created_by, now))
        
        await db.commit()
        return cursor.lastrowid

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
            return [dict(row) for row in rows]

async def recent_matches(guild_id: int, user_id: Optional[int] = None, limit: int = 10) -> list[dict]:
    """Get recent matches, optionally filtered by user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        if user_id is not None:
            # Filter matches where user_id appears in either team
            async with db.execute("""
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
            """, (
                guild_id,
                f"{user_id},%", f"%,{user_id},%", f"%,{user_id}",
                f"{user_id},%", f"%,{user_id},%", f"%,{user_id}",
                limit
            )) as cursor:
                rows = await cursor.fetchall()
        else:
            # Get all recent matches for the guild
            async with db.execute("""
                SELECT * FROM matches
                WHERE guild_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (guild_id, limit)) as cursor:
                rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]
