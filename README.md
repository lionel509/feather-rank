
# Feather Rank

- `feather_rank/` — Core package (db, rules, mmr, models, logging)
- `app.py` — Bot entrypoint using the package modules
- `test.py`, `test_bot_run.py` — Local tests and a minimal test bot
- `Dockerfile.*`, `Makefile`, `run_prod*.sh` — Ops scripts

## Commands

### Core Badminton Bot

- `/ping` — Replies with pong
- `/agree_tos` — Agree to the Terms of Service to use the bot
- `/match_singles` — Record a 1v1 badminton match (point-based)
- `/match_doubles` — Record a 2v2 badminton match (set winner based)
- `/match_doubles_points` — Record a 2v2 badminton match (point-based)
- `/leaderboard [limit]` — Show the top players by rating
- `/stats user` — Show player statistics
- `/verify match_id decision name` — Verify a match result (approve/reject)
- `/pending` — List your matches awaiting your verification
 
 
### Emoji Verification

- When a match is reported, each non-reporter participant gets a DM with the match summary.
- React **✅** to approve or **❌** to reject. (Set emojis via `EMOJI_APPROVE`/`EMOJI_REJECT` env vars.)
- If DMs are closed, the bot posts a private prompt in the match channel instead.
- You must run `/agree_tos name:<Your Name>` once before reactions count.

### Test Bot (test_bot.py)

- `/ping` — Simple ping test
- `/test_params text number` — Test command with parameters
- `/test_user user` — Test user selection
- `/test_choices winner` — Test with predefined choices (Team A, Team B, Draw)
- `/test_defer` — Test deferred response

## Features

Track 1v1 and 2v2 badminton matches right inside Discord. Pick players with User Select menus, report set winners or points, and auto-update ratings with a configurable Elo (or Glicko-2) system. View leaderboards, player cards, and match history, all powered by slash/context commands and interactive components.

### Random/Guest Player

For doubles matches, you can use the bot itself as a placeholder for a random or guest player:

- When creating a match with `/match_doubles`, select the bot user for any of the four player slots
- The bot will use a default guest rating (configurable via `GUEST_RATING` env var, defaults to 1200)
- The bot doesn't need to verify the match - only the human players do
- The bot's rating won't be updated after the match
- This is useful when you have an odd number of players and need to fill a spot

## Scoring Rules

Matches are played **best-of-3 sets**. Each set follows badminton rally point scoring:

- **Default**: Play to **21 points** (win by 2, capped at 30)
- **Alternative**: Play to **11 points** (win by 2, capped at 15)

When reporting a match via `/match_singles` or `/match_doubles`, you can choose the `target` option:

- `21 points` (default) — Standard badminton scoring
- `11 points` — Short format

### Rules Details

- A set is won by reaching the target score with at least a 2-point lead
- If the score reaches the cap (30 for 21-point games, 15 for 11-point games), the next point wins
- Examples for 11-point games:
  - **11-9**: Valid (reached 11 with 2+ point lead)
  - **13-11**: Valid (2-point lead maintained)
  - **15-14**: Valid (cap reached, next point wins)
  - **11-10**: Invalid (only 1-point lead)

### Environment Variable Overrides

You can customize scoring rules via environment variables:

```bash
# Set default target (21 or 11)
POINTS_TARGET_DEFAULT=21

# Set win-by margin (default: 2)
POINTS_WIN_BY=2

# Override cap calculation (default: 30 for 21pt, 15 for 11pt)
POINTS_CAP=30

# Set POINTS_CAP to empty or omit to disable cap entirely

# Set guest/random player rating (default: 1200)
# Used when the bot is selected as a player in doubles matches
GUEST_RATING=1200

# Set K-factor for rating calculations (default: 32)
K_FACTOR=32
```

When `POINTS_CAP` is not set, the cap is automatically derived:

- 21-point games → cap at 30
- 11-point games → cap at 15

## Logging

This project now has centralized logging with two modes:

- Normal usage (default): concise INFO-level logs
- Testing: very detailed DEBUG logs with timestamps, module, and line numbers

How to switch:

- In your shell, set the environment variable before running:

```bash
# Detailed logs for testing
export LOG_LEVEL=DEBUG
python app.py

# Concise logs for normal use
export LOG_LEVEL=INFO
python app.py
```

- Or set in your `.env` file:

```dotenv
LOG_LEVEL=DEBUG  # or INFO, WARNING, ERROR
```

The same applies to `test_bot_run.py`. For Docker, you can pass `-e LOG_LEVEL=DEBUG` when running the container.

## Test Mode (full app, prod features)

Run the full bot with all production features, but using a separate test DB and optional fast, guild-only command sync:

```bash
# Use a separate DB by default and show [TEST MODE] in presence
export TEST_MODE=1

# Optional: sync commands to one guild immediately (much faster than global)
export TEST_GUILD_ID=123456789012345678

# Optional: noisy logs for debugging
export LOG_LEVEL=DEBUG

python app.py
```

Notes:

- When TEST_MODE=1, the default DB path changes to ./test_feather_rank.sqlite unless DATABASE_PATH is explicitly set.
- If TEST_GUILD_ID is provided, slash commands are synced only to that guild for instant availability.


## Docker

Two Dockerfiles are provided:

- `Dockerfile.prod` — Production image that runs `./run_prod.sh` by default and persists the SQLite DB under `/data`.
- `Dockerfile.test` — Test image that runs the built-in `test.py` suite by default (ephemeral DB), suitable for CI or local checks.

Build images:

```bash
# Production
docker build -f Dockerfile.prod -t feather-rank:prod .

# Test
docker build -f Dockerfile.test -t feather-rank:test .
```

Run containers:

```bash
# Production (mount persistent DB dir and pass token)
docker run --rm -it \
  -e DISCORD_TOKEN=your_token_here \
  -e LOG_LEVEL=INFO \
  -v $(pwd)/data:/data \
  --name feather-rank-prod \
  feather-rank:prod

# Production ephemeral DB
docker run --rm -it \
  -e DISCORD_TOKEN=your_token_here \
  -e EPHEMERAL_DB=1 \
  -e LOG_LEVEL=INFO \
  --name feather-rank-prod-ephemeral \
  feather-rank:prod

# Test suite (no token required)
docker run --rm -it \
  -e LOG_LEVEL=DEBUG \
  --name feather-rank-test \
  feather-rank:test

# Optional: Minimal test bot (use test Docker image but override command)
docker run --rm -it \
  -e DISCORD_TOKEN=your_token_here \
  -e TEST_MODE=1 \
  -e LOG_LEVEL=DEBUG \
  feather-rank:test python test_bot_run.py
```

Notes:

- The production image runs as a non-root user and expects the database at `/data/smashcord.sqlite` by default.
- Provide your Discord bot token via `-e DISCORD_TOKEN=...` or a Docker secret mechanism in production.
- Mount a host directory to `/data` to persist the database across container restarts.

## Discord Message Formatting

Messages use Discord Markdown for readability (bold headers, inline `code`, and fenced blocks like ```bash for shell).

Example: match summary shown ephemerally after creating a pending match

```
**Match #123 — Pending Verification**
Alice/Bob vs Carol/Dave
21–18 | 19–21 | 22–20
Use `/verify` to approve or `/verify decision:reject` to reject.
```

Example: shell commands in a fenced block

```bash
# Build the production image
docker build -f Dockerfile.prod -t feather-rank:prod .

# Run with a persistent DB volume
docker run --rm -it \
  -e DISCORD_TOKEN=your_token_here \
  -e LOG_LEVEL=INFO \
  -v $(pwd)/data:/data \
  --name feather-rank-prod \
  feather-rank:prod
```

