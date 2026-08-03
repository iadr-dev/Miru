# ADR 0002 — All I/O is tool-mediated; network confined to `sources/`

## Status
Accepted.

## Context
Governance forbids startup network calls, full-market scans, and hidden side effects. An
agent that can call arbitrary code is hard to govern.

## Decision
The agent may act **only** through registered tools. Tools call the context pipeline; the
context pipeline calls source adapters; **only `backend/src/sources/` performs network I/O**.
The dependency direction is strictly one-way: interfaces → runtime → tools → packages →
sources.

## Consequences
- A single choke point (`sources/base.py`) owns TLS policy and network behavior.
- Governance tests can assert "no network call originates outside `sources/`".
- The agent cannot invent side effects; every action is a declared, tiered tool.
- Mirrors Claude Code's model (agent acts through a fixed tool set), aiding auditability.
