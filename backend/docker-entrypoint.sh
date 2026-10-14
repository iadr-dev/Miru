#!/usr/bin/env sh
# Role-selected process entrypoint: one image, one of several process types chosen at container
# start via SERVICE_ROLE. Keeps api/scheduler parity (same build, same deps, same code) so a bug
# can't hide in a role that only one image was ever built with.
set -eu

ROLE="${SERVICE_ROLE:-api}"

case "$ROLE" in
  api)
    # The ASGI app is imported by string so uvicorn owns the process lifecycle (graceful shutdown,
    # worker management). Host/port are container-fixed; replicas/concurrency are an orchestrator
    # concern, not baked into the image.
    # On SIGTERM uvicorn stops accepting and waits this long for the open connections (a chat
    # turn mid-answer) before cancelling them; the container's stop grace period must be longer.
    exec uvicorn api.app:app --host 0.0.0.0 --port "${PORT:-8000}" \
      --timeout-graceful-shutdown "${SHUTDOWN_GRACE_SECONDS:-30}"
    ;;
  scheduler)
    # The long-running leader loop: fires due jobs + evaluates alerts on an interval, with its own
    # graceful SIGTERM handling.
    exec python -m scheduler
    ;;
  ingest)
    # The long-running leader loop that fires each whole-market pull at its documented daily
    # time-of-day, on trading days only, with its own graceful SIGTERM handling.
    exec python -m ingest
    ;;
  mcp)
    # The standalone remote streamable-HTTP MCP server — a separate process from the API, never
    # sharing its worker. Own host/port (MCP_HOST/MCP_PORT), uvicorn owns its lifecycle the same
    # way it owns the api role's.
    exec python -m mcp
    ;;
  *)
    echo "unknown SERVICE_ROLE='$ROLE' (expected 'api', 'scheduler', 'ingest', or 'mcp')" >&2
    exit 64
    ;;
esac
