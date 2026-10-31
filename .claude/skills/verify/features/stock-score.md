# Stock score

The cached multi-factor 多空 score for one symbol: composite 0-100, a 偏多/中性/偏空 band, the
five-factor TW breakdown (momentum, trend_quality, value_guard, quality, revenue_flow), disclosed
caveats. Descriptive context, never a signal. Spec: `docs/backend/modules/20-analytics-scoring.md` section 2.

## Sub-features

- Two horizon profiles: `swing` (default) and `position`.
- Honest gaps: a factor with no usable input is `percentile: null` plus `unavailable_reason`, and its
  name appears in `partial_factors`. The composite renormalizes over the available factors.
- `revenue_flow` reports the 三大法人 20-day chip half; its `description` discloses whether the 月營收 YoY
  half is included (only with `SCORE_REVENUE_YOY_ENABLED=true`).
- ETFs (`00xxxx`) use a different factor set; US symbols use seven factors. Not seeded by `up.sh`.

## How to get to it (user POV)

Web: `/zh-TW/stock/<symbol>`, the AI panel with a swing/position toggle. Chat: ask "9001 的多空分數", the
agent calls `get_stock_score`. API: `GET /api/v1/analytics/{symbol}/score`.

## Driving it with the verify harness

```bash
bash .claude/skills/verify/scripts/up.sh          # prints SCRATCH, PORT
curl -s "http://127.0.0.1:8765/api/v1/analytics/9001/score?market=TW&profile=swing"
curl -s "http://127.0.0.1:8765/api/v1/analytics/9001/score?market=TW&profile=position"
bash .claude/skills/verify/scripts/down.sh "$SCRATCH"
```

Proof of a correct run (measured with the shipped scoring code): `found:true`, `composite` 100.0,
`band` 偏多, momentum 100, trend_quality 100 (8/8), revenue_flow 100,
`partial_factors: ["value_guard","quality"]`, `non_trading_caveat`, `regime_caveat` and
`normalization_caveat` all non-empty. 9003 must read 偏空 (about 26.82). An unknown symbol returns
`found:false` with a message, not an error.

## Gotchas

- Scores are cache-only and batch-computed (23:55 Asia/Taipei in production); `up.sh` runs the batch
  once for 2024-01-02. A symbol or date the batch did not cover is `found:false`.
- value_guard and quality are unavailable in the scratch run because no financial statements are seeded.
  That is the expected, honest baseline, not a bug.
- The seeded dates are 2024, so `as_of` is 2024-01-02 whatever today is.
