#!/usr/bin/env bash
# Runs before the main process.
# 1. Waits for Postgres to accept connections (belt-and-suspenders — compose
#    already waits for the healthcheck, but a brief extra wait prevents races
#    when the DB is restarting mid-session).
# 2. Applies any pending Alembic migrations.
# 3. Executes whatever CMD was passed (default: python main.py).

set -euo pipefail

MAX_WAIT=60   # seconds
WAITED=0

echo "[entrypoint] Waiting for database …"
until python - <<'PY' 2>/dev/null
from artarb.database import check_connection
import sys
sys.exit(0 if check_connection() else 1)
PY
do
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "[entrypoint] ERROR: database not reachable after ${MAX_WAIT}s — aborting." >&2
        exit 1
    fi
    echo "[entrypoint] Not ready yet — retrying in 2 s …"
    sleep 2
    WAITED=$((WAITED + 2))
done

echo "[entrypoint] Database ready."
echo "[entrypoint] Running Alembic migrations …"
alembic upgrade head

echo "[entrypoint] Starting: $*"
exec "$@"
