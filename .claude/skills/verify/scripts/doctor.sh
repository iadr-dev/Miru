#!/usr/bin/env bash
# Read-only: is the instance up.sh started worth driving? Exit 0 only when every check passes.
# Usage: doctor.sh <scratch-dir>
set -uo pipefail

SCRATCH="${1:?usage: doctor.sh <scratch-dir>}"
PORT="${VERIFY_PORT:-8765}"
fail=0
check() { if "$@"; then echo "ok   $DESC"; else echo "FAIL $DESC"; fail=1; fi; }

DESC="scratch dir has api.pid and env.txt"
check test -f "$SCRATCH/api.pid" -a -f "$SCRATCH/env.txt"

PID="$(cat "$SCRATCH/api.pid" 2>/dev/null || true)"
alive() {
  if command -v tasklist >/dev/null 2>&1; then
    tasklist //FI "PID eq $PID" 2>/dev/null | grep -q "$PID"
  else
    kill -0 "$PID" 2>/dev/null
  fi
}
DESC="recorded server pid ${PID:-?} is alive"
check alive

DESC="/readyz on :${PORT} reports the database ready"
check bash -c "curl -fs http://127.0.0.1:${PORT}/readyz | grep -q '\"database\":true'"

DESC="API points at THIS scratch DB, not a developer's dev.db"
check bash -c "grep -q 'miru-verify' '$SCRATCH/env.txt'"

DESC="scores are seeded (a leaderboard read returns rows)"
check bash -c "curl -fs 'http://127.0.0.1:${PORT}/api/v1/analytics/rankings?market=TW' | grep -q '\"symbol\"'"

exit "$fail"
