#!/usr/bin/env bash
set -euo pipefail

# Production run with ephemeral (in-memory) DB. Nothing is persisted.
# Uses .env for DISCORD_TOKEN. Useful for quick demos.

export TEST_MODE=${TEST_MODE:-0}
export EPHEMERAL_DB=1
export LOG_LEVEL=${LOG_LEVEL:-INFO}

echo "[run_prod_ephemeral] Mode=PROD Ephemeral DB (NOT PERSISTED)"
echo "[run_prod_ephemeral] LOG_LEVEL=${LOG_LEVEL}"
echo "[run_prod_ephemeral] WARNING: All data will be lost when the process stops."

exec python app.py
