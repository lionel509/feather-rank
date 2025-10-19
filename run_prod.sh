#!/usr/bin/env bash
set -euo pipefail

# Production run with persistent DB (default). Uses .env for DISCORD_TOKEN.
# You can override DATABASE_PATH before calling, e.g.:
#   DATABASE_PATH=./smashcord.sqlite ./run_prod.sh

export TEST_MODE=${TEST_MODE:-0}
export EPHEMERAL_DB=${EPHEMERAL_DB:-0}
export LOG_LEVEL=${LOG_LEVEL:-INFO}

echo "[run_prod] Mode=PROD Persistent DB (DATABASE_PATH=${DATABASE_PATH:-./smashcord.sqlite})"
echo "[run_prod] LOG_LEVEL=${LOG_LEVEL}"

exec python app.py
