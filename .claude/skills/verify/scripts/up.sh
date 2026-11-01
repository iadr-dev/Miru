#!/usr/bin/env bash
# Launch an isolated Miru API for verification: scratch dir, synthetic data, own port.
# Prints SCRATCH=<dir>, PORT=<n> and PID=<n> on success. Tear down with down.sh <scratch>.
# Usage: up.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PORT="${VERIFY_PORT:-8765}"
if [[ $# -gt 0 ]]; then echo "usage: up.sh (no arguments)" >&2; exit 2; fi

if curl -fs "http://127.0.0.1:${PORT}/livez" >/dev/null 2>&1; then
  echo "port ${PORT} already answers; not double-driving it. Set VERIFY_PORT or stop that instance." >&2
  exit 1
fi

# Verify another checkout's backend (a worktree) with VERIFY_BACKEND_REPO=<its repo root>; the
# helper scripts always come from this skill.
BACKEND_REPO="${VERIFY_BACKEND_REPO:-$REPO}"
if [ -x "$BACKEND_REPO/backend/.venv/Scripts/python.exe" ]; then
  PY="$BACKEND_REPO/backend/.venv/Scripts/python.exe"
elif [ -x "$BACKEND_REPO/backend/.venv/bin/python" ]; then
  PY="$BACKEND_REPO/backend/.venv/bin/python"
else
  echo "no backend venv in $BACKEND_REPO; run: (cd backend && uv sync)" >&2
  exit 1
fi

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/miru-verify.XXXXXX")"
if command -v cygpath >/dev/null 2>&1; then SCRATCH="$(cygpath -m "$SCRATCH")"; fi
mkdir -p "$SCRATCH/parquet"

# backend/src must precede the standard library on sys.path: its `calendar` package would
# otherwise lose to stdlib `calendar` (the Dockerfile sets PYTHONPATH=/app/src for the same reason).
SRC="$BACKEND_REPO/backend/src"
if command -v cygpath >/dev/null 2>&1; then SRC="$(cygpath -m "$SRC")"; fi
export PYTHONPATH="$SRC"

# A scratch cwd means pydantic-settings never finds backend/.env (live secrets).
export ENV=development
export DATABASE_URL="sqlite+aiosqlite:///${SCRATCH}/dev.db"
export PARQUET_ROOT="${SCRATCH}/parquet"
export JWT_SECRET_KEY="verify-$(date +%s)-local-only-secret-not-for-anything-real"
SECRET_ENCRYPTION_KEYS="$(cd "$SCRATCH" && "$PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export SECRET_ENCRYPTION_KEYS
export CORS_ALLOWED_ORIGINS="http://localhost:3000"
printf 'DATABASE_URL=%s\nPARQUET_ROOT=%s\nJWT_SECRET_KEY=%s\n' \
  "$DATABASE_URL" "$PARQUET_ROOT" "$JWT_SECRET_KEY" > "$SCRATCH/env.txt"

cd "$SCRATCH"
if ! "$PY" "$REPO/.claude/skills/verify/scripts/seed_and_score.py" \
    > "$SCRATCH/seed.log" 2>&1; then
  tail -n 30 "$SCRATCH/seed.log" >&2
  exit 1
fi
grep '^scoring_batch ' "$SCRATCH/seed.log"

nohup "$PY" -m uvicorn api.app:app --host 127.0.0.1 --port "$PORT" > "$SCRATCH/api.log" 2>&1 &
# Git Bash's $! is not the Windows PID taskkill needs; `ps` shows the real one in column 4. The
# venv launcher spawns the server as a child, so down.sh kills the whole tree from this PID.
PID="$!"
WINPID="$(ps -p "$PID" 2>/dev/null | awk 'NR==2 {print $4}')"
case "$WINPID" in ''|*[!0-9]*) WINPID="$PID" ;; esac
echo "$WINPID" > "$SCRATCH/api.pid"

for _ in $(seq 1 60); do
  if curl -fs "http://127.0.0.1:${PORT}/readyz" >/dev/null 2>&1; then
    echo "SCRATCH=$SCRATCH"; echo "PORT=$PORT"; echo "PID=$(cat "$SCRATCH/api.pid")"
    exit 0
  fi
  sleep 1
done
echo "API not ready after 60s; last log lines:" >&2
tail -n 30 "$SCRATCH/api.log" >&2
"$REPO/.claude/skills/verify/scripts/down.sh" "$SCRATCH" --keep-evidence >&2 || true
exit 1
