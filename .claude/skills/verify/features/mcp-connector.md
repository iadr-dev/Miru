# MCP connector

A read-only MCP endpoint (streamable HTTP) exposing the same tools to external agents (Claude Code,
Codex, Cursor). Spec: `docs/backend/mcp-claude-connector.md`.

## Sub-features

- Anonymous Free access with `MCP_REQUIRE_AUTH=false`; OAuth consent otherwise.
- Tools listed directly plus `search_tools` and `execute_tool`; private-data and write-shaped tools are excluded.

## How to get to it (user POV)

`claude mcp add --transport http miru http://localhost:8100/mcp`, then ask the client about a stock.

## Driving it with the verify harness

From a scratch cwd, with the same `env.txt` variables as `up.sh` plus `MCP_REQUIRE_AUTH=false` and
`PYTHONPATH=backend/src`, start `python -m mcp` (port 8100; pick another with `MCP_PORT` to avoid the
Docker stack). Then:

```bash
curl -s localhost:8100/healthz
curl -s localhost:8100/mcp -H 'accept: application/json, text/event-stream' -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"execute_tool","arguments":{"id":"get_stock_score","args":{"id":"9001","market":"TW"}}}}'
```

Proof: `/healthz` reports `auth.required:false`; the call returns the same score payload as the REST route.

## Gotchas

- Not yet exercised by this skill. The invocation comes from the repo's MCP tests
  (`backend/tests/mcp/connector_support.py`); confirm the exact tool arguments on first use and fix this file.
- It shares `DATABASE_URL` with the API, so point both at the same scratch DB.
- `up.sh` does not start it, and `down.sh` does not stop it. Kill the PID you started.
