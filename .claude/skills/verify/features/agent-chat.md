# Agent chat turn

The product's core: a user asks a Taiwan stock question, the agent plans, calls read-only tools,
and streams a governed answer (caveated, sourced, never a buy/sell/target/sizing instruction).

## Sub-features

- Streaming over SSE: `turn.start`, `route.decided`, `tool.start`, `tool.result`, `rich_card`,
  `thinking.delta`, `message.delta`, `message`, `usage`, `turn.end`.
- Threads: `/threads`, `/threads/{id}/stream` (reattach), `Idempotency-Key` header (a duplicate returns 409).
- Guardrails: output scan redacts advice-shaped sentences and appends a caveat.

## How to get to it (user POV)

Web: `/zh-TW/chat`, type in the composer and press Enter. API: `POST /api/v1/agent/query`.

## Driving it with the verify harness

Needs an LLM provider key exported into the server's environment (the scratch run has none on purpose)
and a minted token:

```bash
TOKEN=...   # see SKILL.md, Authenticated
curl -N -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"9001 的多空分數是多少？","locale":"zh-TW"}' \
  http://127.0.0.1:8765/api/v1/agent/query
```

Proof: a `tool.start` for `get_stock_score` with id 9001, a `tool.result`, a final `message` that
quotes the composite and band from the tool result, carries the non-trading caveat, and contains no
buy/sell/hold or price-target wording. Web: composer is `textarea#miru-chat-composer`, send is
`button[type=submit]` (aria-label 送出), answer is `div[aria-live="polite"]`.

## Gotchas

- Without a provider key the turn fails at the LLM call; that is an environment gap, not a regression.
- A real key means real spend and a real model call: use the smallest model and one question.
- Data the agent sees is the synthetic store; do not present its numbers as market facts.
