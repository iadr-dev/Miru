#!/usr/bin/env bash
# Launch the Next.js frontend dev server (web/OVERVIEW.md §1). Unlike dev-backend.sh there's no
# Docker stack to orchestrate — just pnpm — so this script only exists to mirror dev-backend.sh's
# shape (cd + guard + exec) for a consistent `./scripts/dev-*.sh` entry point. Run this alongside
# `./scripts/dev-backend.sh` in a second terminal; the frontend talks to the backend over
# NEXT_PUBLIC_API_BASE (default http://localhost:8000/api/v1, see web/.env.example) — it does not
# start or depend on the backend process itself.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../web"

if [[ ! -f .env.local ]] && [[ ! -f .env ]]; then
  echo "web/.env.local not found — copying web/.env.example (safe defaults, no secrets)." >&2
  cp .env.example .env.local
fi

if [[ ! -d node_modules ]]; then
  # NOSONAR (S6505) — --ignore-scripts would break the native builds (sharp, @swc/core,
  # @parcel/watcher) explicitly allowlisted in web/pnpm-workspace.yaml's allowBuilds.
  pnpm install
fi

exec pnpm dev "$@"
