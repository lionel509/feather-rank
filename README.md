
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

