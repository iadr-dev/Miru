#!/usr/bin/env bash
# Launch the full backend stack via Docker Compose: api + scheduler + ingest + mcp, backed by
# real Postgres + Redis (the dev-parity stack; topology in docs/backend/modules/16-infrastructure.md §1) — so the
# Redis-transport, one-writer-DuckDB, and cross-service paths are exercised locally, not first
# discovered in prod. For a lighter API-only loop (SQLite, in-process event bus, no
# scheduler/ingest/mcp), run `uv run uvicorn api.app:app --reload` from backend/ directly instead.
#
# `./dev-backend.sh --backfill` backfills market data first (docker-compose.yml's
# INGEST_ON_STARTUP is "false" by design, so nothing populates a fresh volume on its own) with the
# operator CLI (`python -m ingest.backfill_cli --yes`), once per market (TW, then US), then hands
# off to the normal `up`. Safe to run every day on a live stack: the ingest daemon holds the
# single-writer lease, so it is stopped for the run and the backfill runs in a one-off container
# (with it running, the CLI would refuse with status 3). The final `up` starts the daemon again,
# and its startup gap-fill continues from where the backfill ended. The api, scheduler and mcp
# keep serving throughout; a reader only ever sees a whole partition (atomic swap). A market
# whose backfill reports a failed job is listed at the end, and the stack still comes up.
#
# `./dev-backend.sh --webhook [https://<public-host>]` (no URL = WEBHOOK_BASE_URL from .env) points the Telegram bot's webhook at
# <public-host>/api/v1/webhooks/telegram (Telegram setWebhook, authenticated with
# TELEGRAM_WEBHOOK_SECRET) before bringing the stack up. Telegram can only call a public HTTPS URL,
# so for local work expose the API through a tunnel (cloudflared / ngrok) and pass that URL. Only
# the outcome is printed — never the bot token or the secret. Flags combine, in any order:
# `--backfill --webhook https://...`.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../backend"

if [[ ! -f .env ]]; then
  echo "backend/.env not found — copy backend/.env.example to backend/.env and fill in secrets first." >&2
  exit 1
fi

# Read single values from .env without sourcing it (it holds other secrets).
env_value() {
  grep -E "^$1=" .env | head -n1 | cut -d= -f2- | tr -d '\r' | sed -e "s/^[\"']//" -e "s/[\"']\$//"
}

BACKFILL=0
WEBHOOK_URL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backfill) BACKFILL=1; shift ;;
    --webhook)
      # `--webhook` alone uses WEBHOOK_BASE_URL from backend/.env (your ngrok / public URL).
      if [[ -n "${2:-}" ]] && [[ "${2#-}" = "$2" ]]; then
        WEBHOOK_URL="$2"
        shift 2
      else
        WEBHOOK_URL="$(env_value WEBHOOK_BASE_URL)"
        shift
      fi
      if [[ -z "$WEBHOOK_URL" ]]; then
        echo "--webhook needs a public https URL (or WEBHOOK_BASE_URL in backend/.env)" >&2
        exit 1
      fi ;;
    *) break ;;
  esac
done

if [[ -n "$WEBHOOK_URL" ]]; then
  case "$WEBHOOK_URL" in
    https://*) ;;
    *) echo "--webhook URL must start with https:// (Telegram only calls HTTPS)" >&2; exit 1 ;;
  esac
  BOT_TOKEN="$(env_value TELEGRAM_BOT_TOKEN)"
  HOOK_SECRET="$(env_value TELEGRAM_WEBHOOK_SECRET)"
  if [[ -z "$BOT_TOKEN" ]] || [[ -z "$HOOK_SECRET" ]]; then
    echo "TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must be set in backend/.env" >&2
    exit 1
  fi
  RESPONSE="$(curl -sS -m 20 "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
    --data-urlencode "url=${WEBHOOK_URL%/}/api/v1/webhooks/telegram" \
    --data-urlencode "secret_token=${HOOK_SECRET}" \
    --data-urlencode 'allowed_updates=["message"]' || true)"
  if printf '%s' "$RESPONSE" | grep -q '"ok":true'; then
    echo "Telegram webhook set -> ${WEBHOOK_URL%/}/api/v1/webhooks/telegram"
  else
    echo "Telegram setWebhook failed: $(printf '%s' "$RESPONSE" | grep -o '"description":"[^"]*"' || echo 'no response')" >&2
    exit 1
  fi
fi

if [[ "$BACKFILL" = "1" ]]; then
  # No separate migrate step: every process creates the schema at its own startup
  # (backend/CLAUDE.md §6), the one-off backfill container included.
  docker compose build ingest
  docker compose up -d postgres redis
  # The daemon holds the writer lease while it runs; stop it for the backfill (a no-op when it is
  # not running). `unless-stopped` keeps it down until the `up` below.
  docker compose stop ingest
  FAILED_MARKETS=""
  for market in tw us; do
    # The image entrypoint ignores a plain command, hence --entrypoint.
    if ! docker compose run --rm --no-deps --entrypoint python ingest \
        -m ingest.backfill_cli --yes --market "$market"; then
      FAILED_MARKETS="$FAILED_MARKETS $market"
    fi
  done
  if [[ -n "$FAILED_MARKETS" ]]; then
    # Usually one source refusing one symbol or one day; the rest of that market was still written,
    # and the daemon's gap-fill retries what is missing. Exit status 3 means another backfill holds
    # the lease.
    echo "backfill reported failed jobs for:${FAILED_MARKETS} (see the log above); starting the stack anyway" >&2
  fi
fi

exec docker compose up --build "$@"
