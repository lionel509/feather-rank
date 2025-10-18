
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

