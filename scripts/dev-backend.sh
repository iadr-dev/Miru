#!/usr/bin/env bash
# Launch the full backend stack via Docker Compose: api + scheduler + ingest + mcp, backed by
# real Postgres + Redis (the dev-parity stack, docs/backend/DEVELOPMENT.md §1) — so the
# Redis-transport, one-writer-DuckDB, and cross-service paths are exercised locally, not first
# discovered in prod. For a lighter API-only loop (SQLite, in-process event bus, no
# scheduler/ingest/mcp), run `uv run uvicorn api.app:app --reload` from backend/ directly instead.
#
# `./dev-backend.sh --backfill` seeds market data into the fresh DuckDB/Parquet volume first
# (docker-compose.yml's INGEST_ON_STARTUP is "false" by design — nothing auto-populates on a
# fresh volume) via the real operator CLI (`python -m ingest.backfill_cli --yes`), run once for
# each market (TW, then US) inside the ingest container after it's up, before handing off to the
# normal `up`.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../backend"

if [ ! -f .env ]; then
  echo "backend/.env not found — copy backend/.env.example to backend/.env and fill in secrets first." >&2
  exit 1
fi

if [ "${1:-}" = "--backfill" ]; then
  shift
  docker compose up --build -d postgres redis migrate ingest
  docker compose exec ingest python -m ingest.backfill_cli --yes --market tw
  docker compose exec ingest python -m ingest.backfill_cli --yes --market us
fi

exec docker compose up --build "$@"
