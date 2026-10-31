# Score leaderboard

Top-N strongest or weakest cached composite scores across the whole TW universe, by profile.
Sort-and-slice over the nightly cache, never a live rescan.

## Sub-features

- `direction=bullish|bearish`, `profile=swing|position`, `universe=stock|etf`, `limit=1..50`, optional `as_of`.
- Stocks and ETFs are never ranked together.

## How to get to it (user POV)

Web: the market and screener pages. Chat: ask for the strongest or weakest stocks, which calls
`get_score_leaderboard`. API: `GET /api/v1/analytics/rankings`.

## Driving it with the verify harness

```bash
curl -s "http://127.0.0.1:8765/api/v1/analytics/rankings?market=TW&profile=swing&direction=bullish"
curl -s "http://127.0.0.1:8765/api/v1/analytics/rankings?market=TW&profile=swing&direction=bearish"
```

Proof: bullish order is 9001 (100.0), 9002 (about 69.27), 9003 (about 26.82); bearish is the reverse.
Each row carries its full breakdown and caveats. No row contains buy/sell/hold/target wording.

## Gotchas

- `universe=etf` is empty in the scratch run (no ETFs seeded) and must return an empty list, not an error.
- `limit` above 50 is rejected with 422.
- Whole-market output is allowed here only because it is the pre-computed cache; a tool that scans live
  would violate the no-full-market-scan rule.
