# Module: Analytics, scoring & screening (`backend/src/analytics/`)

The **derived-compute layer** that turns the raw governed data (module 01 sources → 19 router
→ ingest store, module 22) into the higher-order analytics a stock-detail page and the agent
need: **technical indicators**, a **multi-factor 多空 (bull/bear) score**, a **trend score**, an
**AI summary + SWOT**, a **screener** (gates → setup, plus a tap-tile tag screener), and a
**theme / tier / supply-chain layer**. The algorithms and factor blends below are **our** design
(`backend/src/analytics/`), governed by the non-trading rules in
[backend/CLAUDE.md](../../../backend/CLAUDE.md) §4 and [AGENTS.md](../../../AGENTS.md).

**This is compute, not a data source.** Everything here is *derived* from data the router
already provides — it opens no sockets (the dependency rule holds: `analytics/` sits beside
`context/`, calls the router/store, never `sources/` directly). Its outputs are `public`
except where a feature is explicitly Sponsor-tier (the AI summary; the 分點 sub-dimension of the
trend score); a factor lacking data is dropped from the composite with the gap disclosed — never
silently.

> **Governance is load-bearing here.** A "score" and a "多空 label" are the closest this
> product comes to a signal, so the non-trading rule is enforced hardest in this module.
> See [§7 Non-trading framing](#7-non-trading-framing-the-hard-line) — it is not optional.

---

## 1. Technical indicators (`analytics/indicators.py`, `patterns.py`, `structure.py`)

A **pure indicator library** — each indicator is a pure function over a `PriceBar` sequence, no
I/O, no LLM. Exposed through the `get_technical_indicators` tool (`tools/technical_tools.py`,
`public`), which runs them over one `PRICE_EOD` fetch for a date range; the score engine, trend
score and tag screener also consume them directly.

| Group | Indicators | Notes |
|-------|-----------|-------|
| Moving average | `sma`, `ema`, `moving_average_bundle` (MA5/10/20/60 at once) | 均線排列 |
| Oscillators | `kd` (stochastic 9/3/3, TW-standard seeded at 50), `rsi` (Wilder 14) | |
| Trend | `macd` (12/26/9, EMA-of-DIF signal, dif/dem/histogram) | |
| Volatility | `bollinger` (20/2σ), `atr` | 布林通道 |
| Volume | `obv`, `vwap` | `vwap` is a same-day `turnover/volume` proxy, not textbook intraday-cumulative VWAP |
| Patterns (`patterns.py`) | doji, bullish/bearish engulfing, 多方炮/空方炮 (bullish/bearish cannon), V/倒V reversal, 紅三兵/黑三兵 — **9 patterns** | each a plain boolean ("completes as of the most recent bar?"); `scan_patterns` returns the hits |
| Structure (`structure.py`) | `support_resistance_levels` — swing-high/low pivots (strict extremum over a ±3-bar window) | every pivot found, oldest first; no ranking |

- Series-returning indicators **left-pad with `None`** for the warm-up window — never a fabricated
  number standing in for "not computable yet". Bars must be supplied ascending by `trade_date`;
  this module never re-sorts.
- **No back-adjustment (還原股價) pipeline exists.** Indicators run on the ingested
  `close`/`high`/`low` series as-is.
- Not built: 量能 (均量/爆量/量價背離) as a structure read, 突破/假突破 detection, and N 波回檔
  detection. A 爆量 boolean exists only as a screener tag (`volume_surge`, §5a).
- **Framing:** a KD golden cross or a bullish engulfing is reported as a **描述** of the chart
  ("KD 在低檔黃金交叉"), never "buy".

## 2. Multi-factor 多空 score (`analytics/score.py`, `analytics/models.py`, `analytics/scoring_batch/`, `analytics/scoring_store.py`)

The bull/bear engine — **pure computation, no LLM** — updated daily by the ingest layer
(module 22) and cached in the relational `stock_scores` table (a SQLAlchemy model in
`backend/src/db/models/scores.py`). Designed the way a professional would (see
[§2b](#2b-composite-blend--horizon-aware)): a **lean, low-correlation factor set**, each
converted onto the same 0–100 "higher is stronger" axis (a cross-sectional winsorized-±3σ
percentile, or — for value/quality — a winsorized z-score against a comparison universe mapped
through the standard normal CDF), then a weighted blend. Why lean: added factors are correlated
noise that overfit and can't be explained to the user (the transparency rule in §7).

`analytics/score.py`'s module docstring is the most current restatement of the mechanics; verify
numbers against it. `unavailable_reason` / `partial_factors` are the load-bearing "never fabricate
a number" mechanism: a factor a symbol's ingested data cannot support is reported as genuinely
missing, never defaulted to a neutral 50.

**The factor set branches by symbol type** (`SymbolType`; breakdown models in `analytics/models.py`):
`StockScoreBreakdown` (TW stock, 5 factors), `UsStockScoreBreakdown` (US stock, 7 factors),
`EtfScoreBreakdown` (any ETF, 5 factors). A plain stock's value/quality factors are meaningless
for a fund, so ETFs get a distinct factor set rather than inapplicable ones degrading to
unavailable.

### 2a. The factors

**TW stock (five factors)**

| Factor | How it's computed (`analytics/score.py`) | Why it's in the lean set |
|--------|------------------|--------------------------|
| **Momentum** | **12-1 momentum** — trailing 12-month total return excluding the most recent month (`twelve_minus_one_momentum`) — cross-sectional winsorized percentile, capped (not zeroed) at 70 when the stock is >~20% above its 50-day MA (over-extension) | The most robust cross-sectional premium |
| **Trend quality** | Minervini trend-template condition count (0–8, `trend_template_score`) → `(count/8)*100`; the 8th condition (RS percentile ≥ 70) counts as *not met* when no momentum percentile is supplied | Turns "is it in an uptrend" into a graded number |
| **Value guard** | Z-scored composite of whichever of **EV/EBITDA, FCF yield, P/E, P/B** the ingested data can assemble (`analytics/fundamentals.py`); each present sub-metric winsorized, z-scored vs the comparison universe, equal-weighted, mapped through the normal CDF | **The anti-追高 brake** |
| **Quality** | Same construction over whichever of **ROE, debt/equity (penalty), earnings variability (penalty), ROIC, gross profitability** the data supports | Filters "empty rockets" |
| **Revenue / flow (籌碼)** | **三大法人 20-day net-buy trend** only (`net_buy_20d_trend`), winsorized cross-sectional percentile. The **月營收 YoY sub-signal is not part of the factor**; its description discloses it reports the chip-flow half only | TW is flow-driven |

**US stock (seven factors)** — `momentum`, `trend_quality`, `value_guard`, `quality` as above, plus
`analyst_revision` (EPS-trend delta + revision breadth from `AnalystConsensus`; takes
`revenue_flow`'s slot since US has no 三大法人 equivalent), `growth` (forward EPS/revenue growth
estimates blended with analyst price-target upside) and `liquidity` (average daily dollar volume).

**ETF (five factors)** — `momentum` (same 12-1), `premium_discount` (TWSE's published 預估折溢價幅度,
fixed linear scale: 0% deviation = 100, ±5% or worse = 0), `cost` (SITCA expense-ratio percentile,
lower is better), `management_activity` (holdings-turnover-derived, **active ETFs only**,
unavailable for passive/leveraged/inverse), `liquidity_flow` (三大法人 net-buy trend).

Every fundamental sub-metric degrades independently (a symbol missing one line item drops that
sub-metric, not the whole factor); a factor with **zero** usable inputs becomes
`FactorScore(percentile=None, unavailable_reason=...)` and is dropped from the blend.

### 2b. Composite blend — horizon-aware

Two weight profiles (`ScoreProfile.SWING` / `ScoreProfile.POSITION`), because a swing trader and a
position holder weight the *same factors* differently. Default (the tools' own default) = swing.

```
composite = Σ (weight_i · percentile_i) / Σ weight_i     # renormalized over available factors, 0–100
```

| TW stock factor | **Swing** (default) | **Position** |
|--------|-------------------------------|------------------------|
| Momentum | **0.35** | 0.15 |
| Trend quality | **0.25** | 0.15 |
| Value guard | 0.10 | **0.25** |
| Quality | 0.10 | **0.30** |
| Revenue / flow | **0.20** | 0.15 |

US weights (swing / position): momentum .25/.05, trend_quality .15/.05, value_guard .10/.25,
quality .10/.30, analyst_revision .15/.10, growth .15/.15, liquidity .10/.10. ETF weights
(swing / position): momentum .40/.20, premium_discount .20/.25, cost .10/.25, management_activity
.10/.15, liquidity_flow .20/.15. The authoritative tables are `_STOCK_WEIGHTS`, `_US_STOCK_WEIGHTS`,
`_ETF_WEIGHTS` in `analytics/score.py`.

> **Why this blend (and why a theme-heavy one was rejected).** A common blend such as
> `chip .35 / technical .30 / theme .20 / fundamental .15` has two flaws: (1) a **theme weight is
> 追高 fuel** — it surfaces stocks *after* the move, so theme is a tag, never a scored factor (see
> [§5b](#5b-theme-tier--supply-chain-layer-analyticsthemes)); (2) there is no valuation/quality
> guard, so a thin pump can score high. Momentum carries the up-trend signal, and value+quality
> are explicit brakes. **籌碼 stays high** (0.20) because it leads price in Taiwan.

- **Over-extension penalty:** a stock **> ~20% above its 50-day MA** has its momentum percentile
  *capped* at 70 (`apply_over_extension_cap`).
- **Regime caveat:** every `ScoreResult` carries a fixed `regime_caveat` disclosing that market-
  regime context (e.g. TAIEX vs its 200-day trend) is **not evaluated**; there is no live regime
  overlay.
- **Normalization caveat:** every result discloses whether value/quality were z-scored against
  sector peers or, absent an ingested sector classification, the whole universe.
- **Label mapping:** composite → **偏多 / 中性 / 偏空** (`ScoreBand`, via `band_from_composite` —
  ≥60 bullish-leaning, ≤40 bearish-leaning, else neutral), alongside the numeric score, the
  horizon profile, and the full factor breakdown — *analytical context with caveats*, not a signal.

### 2c. Mechanics

- **Cache-only, batch-computed:** there is no on-demand recompute path. `get_stock_score` reads the
  cached `stock_scores` row and returns an honest `found=False` if the batch hasn't run — every
  factor is cross-sectional, so answering for one symbol would require ranking against the whole
  day's universe. The batch (`analytics.scoring_batch.job_scoring_batch`, the **`scoring_batch`**
  ingest job, **23:55 Asia/Taipei**; a US counterpart `us_scoring_batch` fires 07:30 —
  [module 22](22-data-ingest.md)) computes and upserts both horizon profiles for every symbol the
  day's ingested data covers. Tools: `get_stock_score` (one symbol's cached composite +
  breakdown), `get_score_leaderboard` (top/bottom-N by composite from the same cache — never a
  live rescan). REST: `/analytics/{symbol}/score`, `/analytics/rankings` (§6).
- **Entitlement:** no shipped TW factor carries a Sponsor-only sub-signal, so nothing is masked
  inside the score. If one is added, the mechanism is: mask it for Free users, renormalize over
  the available factors, and list it in `ScoreResult.partial_factors`.

### 2d. Known gaps (not implemented in `analytics/score.py`)

- ±5 analyst-recommendation overlay on the TW composite (the US `analyst_revision` factor is the
  analyst-data use that exists), 投信作帳 window-dressing flagging, 除權息-adjusted prices,
  a news-sentiment sub-module, and an explicit disclosure-date gate for financial-statement
  factors (look-ahead bias) — the factors read whichever partitions have landed.
- No liquidity factor for TW stocks and no liquidity gate in the score; liquidity is a **gate in
  the screener** (§5), never a weighted TW factor.

## 3. Trend score (`analytics/trend_score.py`, `analytics/trend_score_store.py`)

A second, independent descriptive score: a **single-symbol trend-strength read** (not a
cross-sectional rank) over six dimensions, weighted 技術 20% / 籌碼 35% / 資金信用 15% /
分點集中 10% / 板塊輪動 10% / 大盤連動 10% for TW (`DIMENSION_WEIGHTS`), renormalized over
available dimensions. US uses its own dimension set (`US_DIMENSION_WEIGHTS`: technical,
institutional ownership, insider sentiment, analyst revision, sector momentum, index
co-movement). Deliberately no forward-looking prediction. Batch-computed by `trend_score_batch`
(23:57 Asia/Taipei; US 07:32) and read via `get_trend_score` (`public`; the 分點 dimension is
Sponsor-gated inline and disclosed as locked) and `/analytics/{symbol}/trend-score`.

## 4. AI summary + SWOT (`analytics/summary_generation.py`, `analytics/summary_store.py`)

`get_stock_summary` (`tools/summary_tools.py`, **Sponsor**) returns an LLM-written headline +
narrative + SWOT (Strengths / Weaknesses / Opportunities / Threats), **strictly grounded in the
day's cached `ScoreResult`** (its factor breakdown is the only input — no raw price, no news).

- Cached in the `stock_summaries` table, point-looked-up by `(symbol, market, trade_date)` — never
  a "latest" fallback, since a summary is only valid for the exact score it was grounded in. A
  cache miss triggers generation on demand; there is no weekly batch job. `found=False` if no score
  exists yet to ground a summary.
- The parsed LLM output passes `governance.textscan.scan_structured_payload` before a result is
  built; on any parse failure a minimal fallback grounded in the composite/band is returned (never
  raises). Output is zh-TW, descriptive, never buy/sell/hold, and carries `non_trading_caveat`.
- REST: `/analytics/{symbol}/summary` (Sponsor-gated for unentitled callers).

## 5. Screener (`analytics/screener/`)

Two complementary read surfaces; both are descriptive filters, never a recommendation list.

### 5.0 Gates → setup funnel (`run_screener`, `analytics/screener/engine.py`)

Market is `TW | US | ETF`. A request carries `conditions` (factor/op/value, AND/OR `match`),
optional `gate_overrides`, `count_only` (cheap survivor-count probe) and `size`.

- **Stage A — hard gates** (`gates.py`, `SCREENER_GATES`, binary elimination before any condition):
  price floor, average-volume floor, market-cap floor, 處置/注意/全額交割 exclusion (all default
  on for TW), optional 蜜月期 exclusion; ETF adds leveraged/inverse exclusion (default on) and an
  AUM floor. Per-market defaults, per-request overrides. **Liquidity is a gate, never a scored
  factor** — a gate can't be "bought back" by a great score.
- **Stage B — setup**: evaluates the conditions against the gated universe. TW: boolean tags go to
  the §5a flag partition, numeric conditions filter the batch-computed `stock_scores` cache and
  raw valuation partitions; US: a yfinance equity/ETF query (via `dataaccess/screener.py`); ETF:
  the ingested NAV/AUM partitions. Always reads already-materialized stores, never a live
  whole-market scan.
- **Ranking is not part of `run_screener`** — ordering survivors is a separate read
  (`get_score_leaderboard`), keeping "which symbols match" (a filter) apart from "how are they
  ordered".
- **Catalog and presets:** `get_factor_catalog(market)` (`factor_catalog.py`) lists the filterable
  factors per market with bilingual labels and numeric descriptors; `get_screener_presets(market)`
  (`presets.py`) returns 30 named starting-point condition sets (15 TW, 8 US, 7 ETF) that load into
  the condition builder. All three tools are `public`.
- **Saved screens:** per-user CRUD at `/analytics/screener/screens` over the `screener_screens`
  table; `POST /analytics/screener/run` and `GET /analytics/screener/{factors,presets}` back the
  web screener.

## 5a. The real tag screener (`analytics/screener/`)

A **tap-tile, named-boolean-condition screener**: a symbol either carries a named end-of-day tag or
it doesn't — no numeric field/operator/value query shape.

- **The catalog (`catalog.py`)** is a fixed, code-defined `TagDefinition` tuple (an editor cannot
  add a tag without wiring its predicate in code). Each entry has a stable `key`, bilingual labels,
  a `TagCategory`, a description, and a `data_class` (every tag is `PUBLIC` — chip tags rest on
  the public 三大法人 totals, never the sponsor-gated 分點 data). **4 categories** — 技術面,
  籌碼面, 熱門, 智能 — and **37 tags**: 18 technical (9 MA-cross/oscillator/MACD events plus the
  9 candlestick booleans of §1), 11 chip, 4 momentum/熱門 (e.g. volume surge, 52-week high/low),
  and 4 pre-bundled 智能 combos that AND already-catalogued tags. Fundamental and theme tag
  categories do not exist. `catalog.py` is the source of truth for the exact list.
- **Daily-batch-then-fast-read, never live per-query:** `job_screener_flags`
  (`analytics/screener/batch.py`, **20:30 Asia/Taipei**, after the day's price and
  institutional-flow pulls — [module 22](22-data-ingest.md)) evaluates every tag's predicate
  (`events.py`, `patterns.py`, `chip_streaks.py`, `momentum.py`) for every symbol and writes only
  the **true** `(symbol, tag)` pairs to the `screener_flags` Parquet dataset (tidy/long layout, so
  adding a tag needs no schema change). `store.py::run_tag_screener_query` reads the partition back
  and computes the AND/OR match.
- **AND/OR match semantics** (`schemas.screener.TagScreenerQuery`): a list of
  `TagCondition{tag, negate}` plus `match: "AND"|"OR"`; each hit's `matched_tags` shows which
  conditions it satisfied. **Negation** is evaluated against the partition's own symbol universe
  (symbols carrying at least one true flag that day) minus the flagged set; a symbol with no flag
  at all is not in the partition and is not a negation match, disclosed via `detail`. An unknown
  tag key resolves to zero matches for that condition, never an error, with the key in `detail`.
- **Exposure:** the tools `run_tag_screener` and `get_tag_catalog` (`tools/screener_tools.py`,
  both `public`) read the derived Parquet store directly (not the router). There is no dedicated
  tag REST route; tags also appear as boolean factors in the `run_screener` factor catalog.
- **Governance:** a result is "符合『外資連3買』的個股", never a recommendation list;
  `TagScreenerResult.detail` is never empty, so an empty/failed query carries an honest reason.

## 5b. Theme / tier / supply-chain layer (`analytics/themes/`)

A three-layer design. Theme membership is **never a scored factor** — it tags/groups symbols only.
The tables (`themes`, `theme_memberships`, `theme_tiers`, `supply_chain_edges`,
`theme_change_log`) are SQLAlchemy models in `backend/src/db/models/themes.py`; the REST surface
is in [api-reference.md](../api-reference.md).

- **Layer 1 — deterministic, industry-chain-derived themes (`analytics/themes/seed.py`,
  `seed_industry_themes`).** Monthly ingest job (**`theme_industry_seed`**, 18:30 Asia/Taipei —
  [module 22](22-data-ingest.md)): pulls FinMind's whole-market industry-chain classification
  roster and turns every distinct `industry` value into an `industry`-kind `Theme`, with every
  stock carrying that industry becoming a member (`ThemeSource.INDUSTRY_CHAIN`). Each theme's
  membership snapshot is a clean replace on every run (`replace_theme_members`), never a growing
  union. A Free-tier token with no Sponsor access to this dataset, or a transient empty response,
  is a graceful skip that leaves the existing snapshot untouched. This layer's membership set
  covers the whole listed roster, so it doubles as the real-symbol universe the Layer-3
  fabrication safeguard checks against.
- **Layer 2 — curated tier map + keyword-heuristic backfill (`analytics/themes/tiers.py`,
  `tiers_seed.py`, `seed_theme_tiers`).** Monthly job **`theme_tier_seed`**, 18:35, after Layer 1.
  Every `(theme, sub_industry)` pair gets an upstream/midstream/downstream position, **curated
  always wins**: the editorial map `config/theme_tiers.yaml` (`ThemeTierSource.CURATED`) is applied
  first; then `classify_tier` — a pure substring-keyword classifier over Taiwan business vocabulary
  (downstream checked before upstream, before midstream; unmatched defaults to midstream) —
  backfills every other pair Layer 1 produced membership for (`ThemeTierSource.HEURISTIC`).
  `upsert_theme_tier` itself refuses a heuristic write against an already-curated row, so a re-run
  in any order can never flip an editor's decision back to a guess.
- **Layer 3 — operator-triggered LLM concept-theme research + in-theme-only supply-chain edges
  (`analytics/themes/research.py`, run via `python -m analytics.themes`).** Deliberately **not**
  wired into any scheduled ingest pass — it is the only layer that spends on LLM calls and does
  live web research, so it carries an unattended-drift/unattended-spend risk the two deterministic
  layers above do not; an operator runs it by hand when they want to refresh the concept themes.
  One invocation researches every theme in `config/concept_themes.yaml` under one correlated
  `run_id`: two researcher LLM roles independently propose constituent stock codes (grounded in a
  web search + a local keyword cross-reference against already-classified sub-industries), a
  verifier role reconciles picks only one researcher proposed, and — if the verifier call itself
  fails — the pipeline **fails open** to keeping the full disputed union rather than losing
  research.
  **The fabrication safeguard, which is the single most governance-critical detail here:** every
  symbol either researcher (or the verifier's kept set, including the fail-open path) proposes is
  filtered, in code, against `store.list_all_membership_symbols()` — the real set of symbols
  present in the Layer-1 industry-chain memberships, which covers the whole listed roster — via
  `_filter_to_universe`. A proposed code absent from that set is dropped and logged
  (`fabricated_symbol_dropped`) and **never reaches a stored membership row**, regardless of which
  path produced it. This runs for both researchers' raw output before reconciliation, and again on
  the reconciliation result including the fail-open branch, so failing open never lets a
  fabricated code through. Kept members are written `ThemeSource.CONCEPT_RESEARCH` at one of two
  confidence bands (0.9 if both researchers agreed, 0.6 if only one did and the verifier kept it).
  The in-theme edge generator (`generate_in_theme_edges`) is a deliberately narrower, safer
  extraction: for each concept theme with at least 3 members, it lists **only that theme's own
  member symbols** and asks the model to connect stocks strictly within that list; any proposed
  edge is additionally filtered to endpoints that are both members of that same theme and distinct
  — the model cannot introduce an edge to a symbol outside the listed set, whatever it proposes.
  Hand-seeded bootstrap edges (`config/supply_chain_seed.yaml`, `ThemeSource`-independent
  `EdgeSource.SEED`) load separately as a known-good baseline.
- **Exposure:** tools in `tools/theme_tools.py` — `get_themes`, `get_theme_members`,
  `get_symbol_themes`, `get_theme_tiers`, `get_supply_chain_edges`, `get_theme_tier_heatmap` — and
  REST routes in `api/routers/themes.py`: `GET /themes`, `GET /themes/{key}`,
  `GET /themes/{key}/tiers`, `GET /themes/{key}/tier-heatmap`, `GET /symbols/{symbol}/themes`,
  `GET /symbols/{symbol}/supply-chain`. All `public` and reachable anonymously (`optional_user`) —
  platform reference data, not user-scoped state. Layer 3 is not tier-gated.

## 5c. 策略盯盤 strategy watch (`analytics/strategy_watch/`)

A three-stage pipeline: an 08:00 screen, an intraday check every 10 minutes (09:00-13:30), and an
alert when every condition of a strategy the user subscribed to holds. All three stages are built;
delivery (the subscription, morning list and condition alerts) is module 14 "策略盯盤 subscription".

**Stage 1, `job_strategy_watchlist` (08:00, `batch.py`).** Reads the last completed session from the
store and writes `strategy_watchlist`, one row per `(symbol, strategy)`:

| Strategy | Setup on the last session (`strategies.py`) | Levels recorded for the intraday check |
|---|---|---|
| `volatility_squeeze` 波動率擠壓 | BB(20,2) bandwidth in the narrowest 5% of its last 120 sessions, close above MA60 and still below the upper band; skipped when breadth is risk-off | `upper_band`, `close_5d_ago` |
| `oversold_bounce` 超跌反彈 | 10-session drop of at least 15% or a close under the lower band, with RSI14 < 30 | `lower_band`, `k` (KD 超賣), `drop_10d_pct` |
| `ad_divergence` 量價背離 | close within 3% of the 20-day low and below its level 20 sessions ago, while the Chaikin A/D line (`indicators.accumulation_distribution`) is above its level 20 sessions ago | `low_20d`, `ad_value`, `ad_low_20d`, `rsi_at_low` |
| `chip_accumulation` 籌碼集中盤整 | stocks only (no ETFs): a 20-session range ≤ 12% wide that the last close has not broken (close ≤ the earlier sessions' high), up < 10% over 20 sessions; 外資+投信 net buying over 20 sessions with one of them on a ≥ 3-session streak; 千張大戶 share up and holder count down vs up to 4 weekly 集保 snapshots back; 融資 balance down over 20 sessions | `range_high_20d`, `range_low_20d`, holder and margin changes |
| `momentum_flag` 強勢整理 | stocks only: close > MA20 > MA60; a run of at least 15% off the low of the 20 sessions before the pause; then the longest 3-7 session pause inside a band ≤ 7% wide, not broken, holding the upper half of the run, on lower average volume than the 10 sessions before it; 外資 net buying over 20 sessions, 千張大戶 share up and holders down over 4 weeks. 融資 is not a condition; a 20-session rise above 20% is stated in the alert | `flag_high`, `flag_low`, `flag_sessions`, `run_gain_pct`, `pause_volume_ratio` |

- Universe: scoreable TW symbols that traded on the last session, 20-day average volume at least
  1,000 張. At most 30 per strategy, most liquid first.
- Regime: breadth = share of the universe closing above MA20 (risk-on at least 60%, risk-off under 30%).
- Listing history: a (symbol, strategy) also on the previous trading day's list keeps its
  `first_listed` and counts `days_on_list`; after a gap it starts again.
- The 千張大戶 tier is read under both its TDCC label (`1,000,001以上`) and FinMind's (`more than
  1,000,001`, what the ingested partitions actually hold).
- Exclusions (`context.excluded_symbols`): 處置股 in their period, 注意股 flagged on the last session,
  除權息 from today through 3 trading days ahead.
- Context columns (never conditions): industry and its 20-session sector-strength percentile, 外資
  and 投信 streaks, 5-session 融資餘額 change, the newest 重大訊息 subject.
- Thresholds are fixed constants chosen from common practice, not tuned to a backtest; no return or
  win-rate figure is shown to users (§7).

**Stage 2, `strategy_intraday` (09:00–13:30 every 10 min, `ingest/jobs/strategy_intraday.py` +
`intraday.py`).** One whole-market Sponsor snapshot per slot; each watchlist row gets a hit with
every condition's observed and required value, appended to the day's `strategy_hits` partition
(re-running a slot replaces it). Triggers per strategy:

| Strategy | Triggers (all must hold) |
|---|---|
| `volatility_squeeze` | price > `upper_band` · 量比 ≥ 1.5 · price > `close_5d_ago` |
| `oversold_bounce` | price > open (紅K) · 量比 ≤ 0.8 (量縮) · last session K < 20 |
| `ad_divergence` | price < `low_20d` · RSI14 above `rsi_at_low` · A/D above its 20-day low |

`all_met` = every trigger holds. `alertable` additionally needs the same pair `all_met` on the
previous slot (`confirmed`) and every guard: not before 09:30, market median change > −1%, price at
or above the day's average price (not for divergence), not within 1% of 漲停 (squeeze). Rows that are
not alertable are the near-miss log: stored and shown, never sent. The snapshot must be dated today,
otherwise the slot is skipped.

**Close confirmation, `strategy_close_check` (18:00, after `price_eod`).** For the breakout setups
(`volatility_squeeze`, `chip_accumulation`, `momentum_flag`) it compares today's close with the
morning level (`upper_band` / `range_high_20d` / `flag_high`) and the day's volume with 1.5 × the 20-session average, and writes
the result as slot `close` in `strategy_hits`. A met close is alertable on its own (the close is the
confirmation swing traders use); it is delivered as a separate 收盤確認 alert from any intraday one.
Without today's price partition the run is a journaled skip.

## 6. Tools & endpoints

**Registered tools:** `get_stock_score`, `get_score_leaderboard` (§2c); `get_trend_score` (§3);
`get_stock_summary` (§4, Sponsor); `get_technical_indicators` (§1); `run_screener`,
`get_factor_catalog`, `get_screener_presets` (§5); `run_tag_screener`, `get_tag_catalog` (§5a);
the theme tools (§5b). `get_analyst_consensus` (module 03) is not wired into the TW score. Not
built: `get_top_stocks`, `get_swot`, `get_theme_flow`, `get_figure_events`.

**REST** (`api/routers/analytics.py`, `api/routers/themes.py`; see
[api-reference.md](../api-reference.md)):
```
GET    /analytics/{symbol}/score?profile=swing|position   # composite + 偏多/中性/偏空 + factor breakdown + regime caveat
GET    /analytics/rankings?market=TW&profile=swing&direction=bullish&limit=20&as_of=  # leaderboard, cache-only
GET    /analytics/{symbol}/summary                        # AI summary + SWOT (Sponsor)
GET    /analytics/{symbol}/trend-score
GET    /analytics/screener/factors      GET /analytics/screener/presets
POST   /analytics/screener/run
GET|POST /analytics/screener/screens    PATCH|DELETE /analytics/screener/screens/{screen_id}
GET    /themes   /themes/{key}   /themes/{key}/tiers   /themes/{key}/tier-heatmap
GET    /symbols/{symbol}/themes   /symbols/{symbol}/supply-chain
```

Skills ([modules/08](08-skills.md)): `stock-score`, `stock-screener` (tag screener), `theme-lookup`.

## 7. Non-trading framing (the hard line)

Scores, labels, screens, and theme data are the product's closest approach to a signal, so the
guardrail is strictest here. Enforced in `guardrails.check_output` + this module's own contract:

- **Descriptive, not prescriptive.** 偏多/中性/偏空 describes *what the factors currently show*,
  not what to do. Never "買/賣/加碼/減碼/進場/停損", never a price target of our own, never
  position sizing. A screen/tag-match result is "符合條件的清單", not "推薦名單".
- **Show the work.** Every `ScoreResult` ships its **factor breakdown + the underlying 描述**; the
  number without its basis is never surfaced.
- **Attributed externals.** Analyst data (the US `analyst_revision`/`growth` factors,
  `get_analyst_consensus`) is always "N analysts, as of <date>", never restated as ours.
- **Caveated + current.** Every `ScoreResult` carries `non_trading_caveat`, `regime_caveat`, and
  `normalization_caveat`; the AI summary carries its own `non_trading_caveat`.
- **Divergence honesty.** Bias toward disclosing weakness over manufacturing conviction — the
  standard the factor descriptions (`unavailable_reason`, `normalization_caveat`) hold themselves to.

## Governance obligations
- `analytics/` performs **no network I/O** — derives from the router/store only (dependency
  rule); the one exception, Layer 3 theme research (§5b), calls an LLM and a web-search gateway
  through injected collaborators, never a direct network call from this layer itself, and is
  operator-triggered rather than request-path. The AI summary (§4) likewise calls an injected LLM.
- Sponsor-tier outputs (AI summary, 分點 trend dimension) are entitlement-gated; the score
  discloses partial factors via `ScoreResult.partial_factors`.
- All outputs (scores, leaderboard, screener results, summaries, theme reads) pass
  `guardrails.check_output`: non-trading, caveated, sourced. No module output may become a
  buy/sell/target/sizing signal.
- The Layer-3 fabrication safeguard (`_filter_to_universe`) is itself a governance control, not
  just a data-quality one: it is what makes it safe to let an LLM propose theme constituents at
  all, and it runs on every code path including the verifier fail-open branch.

## Where the batch code lives

`backend/src/analytics/scoring_batch/` is a package; `analytics.scoring_batch` re-exports the public
names (`job_scoring_batch`, `job_trend_score_batch`, the `*_DATASET` constants). `datasets` holds
the dataset names and lookback windows, `inputs` the loading/reshaping of stored data into
per-symbol series and universes, `scoring` the multi-factor batch and its per-symbol scorers, and
`trend` the trend-score batch. Modules import only downward
(`datasets` <- `inputs` <- `scoring` <- `trend`).
