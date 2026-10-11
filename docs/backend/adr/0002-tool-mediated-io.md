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

## Amendment: what "only `sources/`" covers
The rule governs **market-data acquisition**: a caller names a capability and `(symbol, market)`,
and the router picks a source, so no layer can add an undeclared data provider or bypass
governance on fetched data. Other outbound connections exist because the app has its own lifecycle:
the Redis event bus, rate limiter and quota counters, the LLM and voice vendors, Paddle billing,
OAuth sign-in and notification delivery. They are not data sources, but they are not free-for-all
either. `backend/tests/arch/test_network_io.py` keeps an explicit allow-list of the modules that
may import a network client, each with its reason; a new one fails the test until it is added
there on purpose, and a stale entry fails it too.

