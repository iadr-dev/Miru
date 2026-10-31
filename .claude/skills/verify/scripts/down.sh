#!/usr/bin/env bash
# Stop the API that up.sh started and delete its scratch dir. Kills only the recorded PID.
# Usage: down.sh <scratch-dir> [--keep-evidence]
# Evidence is never inside the scratch dir (see SKILL.md), so deleting it cannot eat the proof.
set -uo pipefail

SCRATCH="${1:?usage: down.sh <scratch-dir>}"
case "$(basename "$SCRATCH")" in
  miru-verify.*) ;;
  *) echo "refusing to delete $SCRATCH: not a miru-verify scratch dir" >&2; exit 2 ;;
esac

if [ -f "$SCRATCH/api.pid" ]; then
  PID="$(cat "$SCRATCH/api.pid")"
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //PID "$PID" //T //F >/dev/null 2>&1 || true
  else
    kill "$PID" 2>/dev/null || true
  fi
  sleep 1
fi
if [ "${2:-}" != "--keep-evidence" ] || [ ! -f "$SCRATCH/api.log" ]; then
  rm -rf "$SCRATCH"
else
  echo "kept $SCRATCH (api.log inside)"
fi
