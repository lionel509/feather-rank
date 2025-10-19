# Docker Compose Usage Guide

This project has two separate Docker Compose configurations:

## Production (`docker-compose.prod.yml`)
Runs the Discord bot application with persistent database.

### Setup
1. Create a `.env` file (copy from `.env.example`):
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and add your `DISCORD_TOKEN`

### Commands
```powershell
# Build and start the bot (detached mode)
docker compose -f docker-compose.prod.yml up -d --build

# View logs
docker compose -f docker-compose.prod.yml logs -f

# Stop the bot
docker compose -f docker-compose.prod.yml down

# Stop and remove volumes (deletes database!)
docker compose -f docker-compose.prod.yml down -v
```

### What it runs
- **Container**: `feather-rank`
- **Command**: `./run_prod.sh` → `python app.py` (Discord bot)
- **Database**: `/data/smashcord_prod.sqlite` (persistent, stored in Docker volume)
- **Environment**: `TEST_MODE=0`, `EPHEMERAL_DB=0`

---

## Test (`docker-compose.test.yml`)
Runs the test suite with ephemeral database.

### Commands
```powershell
# Run tests (container exits after completion)
docker compose -f docker-compose.test.yml up --build

# Run tests in detached mode (not recommended, tests will run in background)
docker compose -f docker-compose.test.yml up -d --build

# Clean up
docker compose -f docker-compose.test.yml down
```

### What it runs
- **Container**: `feather-rank-test`
- **Command**: `python test.py` (test suite)
- **Database**: `/tmp/test_feather_rank_test.sqlite` (ephemeral tmpfs)
- **Environment**: `TEST_MODE=1`, `EPHEMERAL_DB=1`

---

## Quick Reference

| Task | Command |
|------|---------|
| Run bot (production) | `docker compose -f docker-compose.prod.yml up -d --build` |
| Run tests | `docker compose -f docker-compose.test.yml up --build` |
| View bot logs | `docker compose -f docker-compose.prod.yml logs -f` |
| Stop bot | `docker compose -f docker-compose.prod.yml down` |
| Stop tests | `docker compose -f docker-compose.test.yml down` |

## Troubleshooting

### "env file not found"
Create a `.env` file with at least `DISCORD_TOKEN=your_token_here`

### "still running tests"
Make sure you're using the correct compose file:
- **Production bot**: `-f docker-compose.prod.yml`
- **Tests only**: `-f docker-compose.test.yml`

### Check what's running
```powershell
docker ps
```
Look at the `COMMAND` column to see what each container is executing.
