# Miru feature map

One file per user-facing feature. Each says what it is, how a user reaches it, how to drive it with
the `verify` harness, and what end state proves it. A proof that drives one convenient entry point
is incomplete when the feature lists others.

| Feature | File | Provable offline with `up.sh` |
|---------|------|-------------------------------|
| Stock score (多空 score, factor breakdown) | [stock-score.md](stock-score.md) | yes (proven) |
| Score leaderboard (top/bottom N) | [score-leaderboard.md](score-leaderboard.md) | yes (proven) |
| Agent chat turn | [agent-chat.md](agent-chat.md) | needs an LLM provider key |
| MCP connector | [mcp-connector.md](mcp-connector.md) | yes in principle, not yet exercised |

Not mapped yet: screener (`/analytics/screener/*`), trend score, themes, strategy watch, alerts,
settings pages. Add a file here when a change touches one.
