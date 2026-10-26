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
  籌碼面, 熱門, 智能 — and **39 tags**: 18 technical (9 MA-cross/oscillator/MACD events plus the
  9 candlestick booleans of §1), 11 chip, 5 momentum/熱門 (volume surge/contraction, 20-day high,
  52-week high/low), and 5 pre-bundled 智能 combos that AND already-catalogued tags (e.g.
  `smart_foreign_buy_20d_high` = 外資連3買 AND 創20日新高, descriptive only). Fundamental and theme tag
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
  both `public`) read the derived Parquet store directly (not the router). The same tools back the REST routes
  `GET /market/tag-catalog` and `GET /market/tag-screener` ([api-reference](../api-reference.md));
  tags also appear as boolean factors in the `run_screener` factor catalog.
- **Governance:** a result is "符合『外資連3買』的個股", never a recommendation list;
  `TagScreenerResult.detail` is never empty, so an empty/failed query carries an honest reason.

## 5b. Theme / tier / supply-chain layer (`analytics/themes/`)

A three-layer design. Theme membership is **never a scored factor** — it tags/groups symbols only.
**Pro previews:** the supply-chain relations, chain positions, entity graph and the member detail of
concept themes are Pro features (`FEATURE_MIN_TIER`: `supply_chain`, `concept_themes`). A caller below
Pro gets a server-side preview from the same tools (3 peers per role without sources, the primary chain,
the top 8 entities, 3 concept members per position) with additive `locked` / `hidden_count` fields;
industry themes and the industry-chain tier heatmap stay free ([12](12-multi-user-tiers.md)).
The tables (`themes`, `theme_memberships`, `theme_tiers`,
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
  always wins**: the editorial map `themes/theme_tiers.yaml` (in the private reference directory, `REFERENCE_DATA_DIR`, never in git) (`ThemeTierSource.CURATED`) is applied
  first; then `classify_tier` — a pure substring-keyword classifier over Taiwan business vocabulary
  (downstream checked before upstream, before midstream; unmatched defaults to midstream) —
  backfills every other pair Layer 1 produced membership for (`ThemeTierSource.HEURISTIC`).
  `upsert_theme_tier` itself refuses a heuristic write against an already-curated row, so a re-run
  in any order can never flip an editor's decision back to a guess.
- **Layer 3 — operator-triggered LLM concept-theme research
  (`analytics/themes/research.py`, run via `python -m analytics.themes`).** Deliberately **not**
  wired into any scheduled ingest pass — it is the only layer that spends on LLM calls and does
  live web research, so it carries an unattended-drift/unattended-spend risk the two deterministic
  layers above do not; an operator runs it by hand when they want to refresh the concept themes.
  One invocation researches every theme in `themes/concept_themes.yaml` (private reference directory) under one correlated
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
- **Exposure:** tools in `tools/theme_tools.py` — `get_themes`, `get_theme_members`,
  `get_symbol_themes`, `get_theme_tiers`, `get_supply_chain_edges`, `get_theme_tier_heatmap` — and
  REST routes in `api/routers/themes.py`: `GET /themes`, `GET /themes/{key}`,
  `GET /themes/{key}/tiers`, `GET /themes/{key}/tier-heatmap`, `GET /symbols/{symbol}/themes`,
  `GET /symbols/{symbol}/supply-chain`. All `public` and reachable anonymously (`optional_user`) —
  platform reference data, not user-scoped state. Layer 3 is not tier-gated.

### Supply-chain relations (`analytics/supply_chain/`) — files are the truth, Postgres is an index

The operator's private files (never in git; `REFERENCE_DATA_DIR`) are the source of truth. The tables
`sc_relations` + `sc_evidence` (platform reference data, no `user_id`) are an **index rebuilt from the
files** by the `sc_refdata_load` ingest job; nothing is restored from the database.

- **File format** — `<reference dir>/supply_chain/relations/*.yaml`, each a YAML list of rows
  `{supplier:{symbol,market,name}, customer:{...}, kind: supplies|competes, product, status, origin:
  research|seeded|manual, revenue_pct, as_of, grade (seeded only), evidence:[{url, publisher,
  published_on, excerpt <=300, checker}]}`. Each endpoint carries its own market (`TW`/`US`). Every
  row is validated (`refdata_io/schema.py`); a bad row fails the whole load with `file row[i]: ...`
  and the database is left untouched. An optional `supply_chain/publisher_trust.yaml`
  (`domain: primary|secondary|tertiary|rejected`) overrides the built-in domain trust table.
- **Rules** (`domain/rules.py`, pure) — trust comes from the evidence URL's publisher. Grade is derived,
  never typed: high = >=1 primary or >=2 independent secondary; medium = 1 secondary + 1 tertiary or 2
  tertiary from different publishers; low = 1 secondary; tertiary-only stays `candidate`; rejected
  evidence is ignored. Older than 24 months (newest of `as_of`/`last_verified_at`) becomes `stale`.
  Documented exception: `seeded` rows carry no evidence, keep their stated grade and are `verified`.
- **Load** — `job_sc_refdata_load` (leader-only, daily 06:20 Taipei, also run once in the dev startup
  catch-up) upserts by natural key `(supplier, customer, kind, product)` replacing each row's evidence,
  and deletes index rows the files no longer contain. Idempotent. A missing or empty directory is a
  no-op with one warning (it never wipes the index). CLI: `python -m analytics.supply_chain load|stats`.
- **Read** — `GET /symbols/{symbol}/supply-chain?market=` and `get_supply_chain_edges` read these
  tables and return only `verified`, non-stale relations. Each flat edge adds `from_market`,
  `to_market`, `grade` (`confidence` is the legacy name), `status`, `product`, `as_of`,
  `last_verified_at` and `sources[{publisher,url,published_on}]`.

#### Chain positions (`analytics/supply_chain/positions/`) — 上游 / 中游 / 下游 per company per chain

A *chain* is an industry theme (`themes.kind='industry'`, key = industry name); a company's chain
membership supplies `(symbol, chain_key, sub_industry)`. The **prior** is the existing `theme_tiers`
row for `(chain, sub_industry)`: `curated` = confidence 0.8, `heuristic` (keyword rule) = 0.35. The
files stay the truth: `sc_positions` is derived and the `sc_positions_refresh` job rewrites the whole
table (full replace in one transaction); nothing is hand-edited there.

- **Graph signal** (`signals.py`, pure) — a directed graph of verified, non-stale `supplies`
  relations between Taiwan companies that are members of the same chain. A node needs **>= 2 distinct
  incident edges** to get any opinion. *Layering*: strongly connected components are collapsed (cycle
  protection), `score = depth / (depth + height)` from longest paths in the DAG (0 = pure supplier
  side = upstream-leaning, 1 = pure customer side = downstream-leaning). *Propagation*: anchors =
  operator overrides + curated priors (confidence >= 0.8), scored upstream 0 / midstream 0.5 /
  downstream 1; every other node is set to the mean over its scored neighbours of `min(1, s(supplier)
  + 0.25)` and `max(0, s(customer) - 0.25)` (Jacobi update, anchors fixed), until no score moves by
  more than 1e-3 or 20 iterations. An anchor is scored with its own anchor removed (leave-one-out), so
  a prior never confirms itself. Propagation is used when it has an opinion, else layering. Score ->
  position: `< 0.34` upstream, `> 0.66` downstream, else midstream, with a +-0.04 band around each
  threshold that yields **no** graph opinion.
- **Resolver** (`resolver.py`) — precedence: operator override (`method=override`, 1.0; the operator's
  note stays private, only the code is published) > prior and graph agree (`evidence`, 0.95 curated /
  0.85 heuristic, `agree_prior_graph`) > **curated prior kept** when the graph disagrees (`prior`, 0.8,
  `prior_curated` + `conflict_prior_kept`) > graph alone when there is no curated prior (`evidence`,
  0.60 + 0.05 per edge beyond two, max 0.80; `graph_layering` or `graph_propagation`) > curated prior
  (`prior`, 0.8, `prior_curated`) > **abstain** (no row; the UI shows 尚未分類). A keyword (heuristic)
  prior alone abstains. Both choices follow from measuring against the labelled set (`eval`): keyword
  guesses scored at chance and a small noisy relationship graph scored below the curated placement.
  `is_primary`: per company the chain with the highest confidence (ties: the chain with more members,
  then the alphabetically first key); a partial unique index keeps it to one. Reason codes (stable API
  contract): `override`, `prior_curated`, `prior_keyword`, `graph_layering`, `graph_propagation`,
  `agree_prior_graph`, `conflict_graph_over_prior`, `conflict_prior_kept`.
- **Overrides** — optional private `<reference dir>/supply_chain/position_overrides.yaml`, a list of
  `{symbol, market, chain_key, position, reason}`; a missing file means none, a bad row fails the run
  with `file row[i]: field: message`.
- **Job** — `job_sc_positions_refresh` (leader-only, daily 06:30 Taipei, after `sc_refdata_load`, also
  run once in the dev startup catch-up after it). With no industry memberships it is journaled as
  skipped and the existing rows are kept. CLI: `python -m analytics.supply_chain positions` (compute +
  write, prints counts per position/method only).
- **Evaluation** — `python -m analytics.supply_chain eval [--golden PATH] [--json]` computes positions
  in memory from the current database and scores them against the private
  `<reference dir>/supply_chain/golden_positions.yaml` (`{symbol, chain_key, position,
  label_confidence: sure|unsure, note}`). Metrics: coverage (non-abstained share), accuracy on
  non-abstained `sure` rows, per-position precision/recall/F1, a confusion matrix (with an `abstain`
  column) and the list of disagreements. Gate (exit code 1): accuracy >= 85% and abstentions <= 25%.
  CI exercises the gate logic with synthetic labels only.
- **Read** — `GET /symbols/{symbol}/chain-positions?market=TW` and the deferred public tool
  `get_chain_position` return `{symbol, market, positions:[{chain_key, chain_name, position,
  is_primary, method, confidence, reasons:[{code, detail}], computed_at}]}`, primary chain first; an
  empty list means 尚未分類. Not in this change (follow-ups): the LLM proposer for abstentions and the
  financial-profile tie-breaker.

#### Concept themes & entity graph (`analytics/supply_chain/themes_io/`, `entities_io/`, `entity_graph.py`)

Two more private file sets feed the index; as with relations, **the files are the truth and Postgres
is an index rebuilt from them** (idempotent upsert, rows missing from the files are deleted, a
missing/empty directory is a no-op with one warning, a bad row fails that load before anything is
written).

- **Concept themes** — `<reference dir>/themes/concepts/<key>.yaml`, one theme per file: `{key,
  name_zh, name_en?, description?, related: [names], members: [{symbol, name, position:
  upstream|midstream|downstream|related, origin: seeded|curated|proposed}]}`. The market of a member
  is derived from the symbol (4-6 digit code = TW, ticker = US). Tables `sc_concept_themes` and
  `sc_concept_members` hold them. Each theme is also **mirrored** (replace-per-theme, same
  transaction) into `themes` (`kind='concept'`, `source='concept_research'`) and `theme_memberships`
  (`sub_industry` = 上游 / 中游 / 下游 / 相關公司, `confidence` 0.6) so `get_themes`,
  `get_theme_members`, `get_symbol_themes` and the screener 主題 chips list them with no further
  change. A concept key that an industry theme already uses is refused (the load fails, nothing is
  changed); a concept theme whose file is gone is deleted from all four tables.
- **Entities** — `<reference dir>/entities/catalog.yaml` (`{id, type: tw_company | intl_company |
  technology | material | application | product, mentions, reports, symbol?, aliases?}`) and
  `entities/mentions.jsonl` (`{symbol, entity, section: business|chain|counterparty, role:
  customer|supplier|null, count}`). Tables `sc_entities` and `sc_entity_mentions` (unique on symbol,
  entity, section, role). A mention whose entity is not in the catalog or whose company symbol is not
  a Taiwan code is dropped and counted in the job detail; a malformed line fails the load naming file
  and line.
- **Job** — `job_sc_themes_entities_load` (leader-only, daily 06:25 Taipei, between
  `sc_refdata_load` and `sc_positions_refresh`, also run once in the dev startup catch-up). The two
  loads are independent: a bad file in one is journaled as `failed` but never blocks the other.
  `rows_written` = concept members + entities + mentions. `python -m analytics.supply_chain load`
  loads relations, concept themes and entities; `stats` prints counts only.
- **Theme routes** — additive: theme summaries (`GET /themes`) and `GET /themes/{key}` carry
  `description` and `related_keys` (related names resolved to concept keys; unknown names dropped),
  and each member of a concept theme carries `position` (null for industry themes).
- **Entity graph** — `GET /symbols/{symbol}/entity-graph?market=TW&min_weight=1&limit=60` and the
  deferred public tool `get_entity_graph` return `{symbol, market, nodes:[{id, label, type, symbol,
  market, weight, focus}], links:[{source, target, weight, kind: mention|cooccurrence|supply}]}`.
  The focus node is the company; entity nodes are the entities it mentions (weight = total mentions
  across sections, most-mentioned first, `min_weight` filter, the company's own name excluded);
  peer nodes are up to 25 other companies mentioning at least one shown entity (node weight = shared
  entity count; the focus-peer `cooccurrence` link weight is the Jaccard overlap of the two full
  entity sets, 0..1). `mention` links run company -> entity (weight = count). `supply` links are
  directed supplier -> customer, weight 1, for verified non-stale `supplies` relations between the
  companies shown. An entity that stands for a company already shown reuses that company's node. At
  most 120 nodes (`limit` <= 94 entities + 25 peers + the focus). Only ids, labels, types and counts
  leave the service - no report text.

### Research pipeline: DISCOVER -> VERIFY (`analytics/supply_chain/verification/`)

A budgeted, auditable replacement for hand-driven research. It keeps the file-truth model: verified
results are written to the private relation files and the loader then refreshes the index. It is
**off by default and off in production** (`SUPPLY_CHAIN_RESEARCH_ENABLED=false`).

- **Discover** (`discovery.py`, pure). A target is a claim `{supplier, customer, kind}` with a stable
  id `kind:MARKET-symbol>MARKET-symbol` (a coverage-gap target is `gap:MARKET-symbol`). Sources, in
  priority order: (a) `supply_chain/candidates.jsonl` - only claims whose two ends both resolve to a
  real company (a Taiwan code in the stock roster, or a resolved US ticker); (b) coverage gaps - the
  top `SUPPLY_CHAIN_GAP_TOP_N` companies by market value with no published relation; (c) stale
  re-checks of `research` rows whose `last_verified_at` (else `as_of`) is older than
  `SUPPLY_CHAIN_REVERIFY_DAYS` or unknown. Within a source the larger company's market value ranks
  first. With `--source all` the claim limit is shared 60/25/15 between the three sources and room a
  source does not use flows to the next, so the (large) candidates file cannot starve the others.
  Skipped: a pair already published, and any claim in `supply_chain/rejected.jsonl` (append-only,
  one atomic `O_APPEND` write per line) within `SUPPLY_CHAIN_REJECT_COOLDOWN_DAYS`.
- **Search + read** (`search.py`). `SearchProvider` is a Protocol; the one real implementation wraps
  `dataaccess.web_search.WebSearchGateway` (Brave search, SSRF-guarded page reads), so `analytics/`
  makes no network call of its own. Two queries per claim (Traditional Chinese names, then English),
  hits from publishers the trust table marks `rejected` are dropped, primary then secondary then
  tertiary are preferred, and at most `SUPPLY_CHAIN_PAGES_PER_CLAIM` pages are read.
- **Judge** (`judge.py`, `ModelRole.SUBAGENT`). The page goes in a delimited block labelled untrusted
  data with an instruction to ignore anything written in it (the delimiter is stripped from the page
  text). Strict JSON back: `{supports, direction, supplier_is_first, excerpt, current,
  anonymous_counterparty, as_of_year}`. A page that does not name both companies, is from a rejected
  host or is dated older than the cap never reaches the model. A gap target is turned into concrete
  claims by one extraction call per page; a counterparty the company directory does not know is
  dropped (the fabrication safeguard of the theme research), so the model cannot add a company.
- **Guards** (`guards.py`, pure, unit-tested rule by rule) - the model cannot override any of them:
  the excerpt must be a verbatim substring of the fetched page (after whitespace normalisation), at
  most 300 characters, and name both companies or a known alias (roster names, entity-catalog
  aliases, names already in the relation files; corporate suffixes are tolerated); past or ended
  wording (曾, 過去, 原為, 先前, 已終止, 歷年, formerly, previously, no longer) is rejected unless the
  same sentence also has a present-tense marker; anonymous counterparties (客戶A, 某大客戶, "a major
  customer") are rejected; sector, peer and concept-stock lists are rejected; a `supplies` claim needs
  relation wording and a `competes` claim explicit rivalry wording; the direction must match and not
  be flipped; the source is rejected when older than `SUPPLY_CHAIN_MAX_SOURCE_AGE_DAYS` (1095) when a
  date can be parsed (page text and search metadata first; only when neither yields a date, an
  unambiguous date in the URL path such as `/YYYY/MM/DD/`, `YYYY-MM-DD`, `/YYYYMMDD` or a
  `post/YYYYMMDDNN` id, years 2000 to the current year; query strings and other digit runs are
  ignored; the same rule applies to theme research), and so is a host whose trust is `rejected`; and the excerpt is scanned with
  `governance.textscan` (advice wording or a data-vendor name rejects it).
- **Aggregate**. Each accepted page becomes `Evidence(checker=llm)`; the grade is
  `domain.rules.grade_from_evidence` (publisher = the page host, so independence is by host). A claim
  is published only when a grade exists. Reading stops early once the grade is high. With
  `SUPPLY_CHAIN_SECOND_OPINION=true` (default false) a low-grade single-source claim is re-judged
  once by `ModelRole.SYNTHESIS`; a disagreement writes it as `candidate` instead of `verified`.
  A claim that was examined and not supported is logged to `rejected.jsonl`; one that was not
  examined (budget cap, provider down) is skipped and not logged.
- **Write** (`writer.py`) - the only writer of `relations/*.yaml`, leader-only. One file per supplier
  (`TW-<symbol>.yaml`, `US-<ticker>.yaml`); a row already in any file is updated in place, found by
  `(supplier, customer, kind, product)`. Evidence merges by URL, `last_verified_at` is set, `as_of`
  only moves forward, rows are never deleted, and `manual` and `seeded` rows are never touched. A
  `candidate` result never demotes a published row. Writes take a lock file in the directory, stage
  every touched file beside its original, validate each with the loader's own row parser
  (`RelationFileRow` + `build_relation`), and only then `os.replace`; on any failure files already
  swapped are restored and nothing half-written remains. Rewritten files are normalised YAML
  (comments are not kept). A dry run plans the same changes and writes nothing.
- **Budget** (`budget.py`). Per run: `SUPPLY_CHAIN_RESEARCH_MAX_CLAIMS` (25), `..._MAX_SEARCHES` (60),
  `..._MAX_PAGES` (90), `..._MAX_LLM_CALLS` (120), `..._BUDGET_USD` (2.0) against a conservative
  per-call estimate (search 0.005, subagent call 0.02, synthesis call 0.03; a higher cost reported by
  the provider wins). The first cap reached ends the run gracefully (`stopped_by`).
- **Entry points.** `python -m analytics.supply_chain discover [--limit N] [--source
  candidates|gaps|stale|all]` prints the next targets; `verify [--limit N] [--source ...]
  [--dry-run]` runs the pipeline. A dry run does search and call the model within the caps but
  writes no file and no rejection. A real `verify` takes the ingest writer lease (exit 3 if the daemon
  holds it), writes, and runs the loader. Both print counts and claim ids only - never page text.
  The monthly job `sc_research` (`job_sc_research`, leader-only, registered in
  `ingest.daemon.SCHEDULED_JOBS` and an optional step of the dev backfill CLI) is gated by
  `SUPPLY_CHAIN_RESEARCH_ENABLED`; it journals SKIPPED when disabled, not the leader, no
  `BRAVE_SEARCH_API_KEY`, or no LLM key for the SUBAGENT role. After a run that wrote rows it calls
  `job_sc_refdata_load`. The journal detail is the run summary (claims tried, verified, candidates,
  rejected, skipped, searches, pages, LLM calls, estimated spend, `stopped_by`, rejection reason
  counts); no page text is logged or journaled.
- **Safety.** Web content is untrusted data, never instructions: the only things from a page that can
  reach a file are a verbatim excerpt that passed every guard and the page URL. The ingest container
  mounts `reference-data` read-write (the other services keep it read-only) because it is the single
  writer.

### Theme candidates (Step A)

Concept-theme discovery is a loop: **A** deterministic mining of our own entity mentions for new
candidate themes (this section), **B** web research per theme and **C** evidence verification (both
in "Theme research (Steps B and C)" below), then a **human review** and the explicit `promote` step
("Promoting proposals"). Nothing before `promote` edits a live concept-theme file.

`analytics/supply_chain/theme_discovery/` (`mining.py` pure, `io.py` files, `job.py` job and CLI) reads
the private entity catalog, mentions and concept-theme files (never the database) and proposes every
technology / material / application / product entity mentioned by at least N distinct listed
companies (default 5) that no concept theme already covers (theme key, zh/en name or related name,
or an entity alias, compared ignoring case, width, spaces and punctuation). Entities sharing a name or
alias are merged first; labels under 2 characters or purely numeric are dropped; the list is capped
(default 50) and ordered by specificity (below), total mentions, then label. Each candidate carries a stable
`candidate_id`, symbols only (top 15 by mention count), the sections it appears in, `first_seen` and a
`trend` against the previous run (new / rising / steady / falling).

Output is a private file, `reference-data/themes/theme_candidates.jsonl` (one JSON object per line),
written atomically (temp file then rename); the previous file is kept as
`theme_candidates.prev.jsonl`. Nothing is published or indexed. No search, no model, no network.

**Precision filters.** Raw company count ranks very broad business words first and re-proposes
entities whose companies an existing theme already holds under another name, so every entity that
reaches the company threshold is also tested, in this order, and dropped when:

1. `covered_by_name`: a concept theme already answers to its name or alias (as above).
2. `stoplist`: it matches an entry of the optional private file
   `reference-data/themes/candidate_stoplist.yaml`, a plain YAML list of entity labels or aliases
   (`- some label`), compared with the same normalisation as name coverage. A missing file means no
   stoplist; a file that is not a list of strings fails the run without writing anything.
3. `generic`: more than `--max-share` (default 0.04) of all `tw_company` entities in the catalog
   mention it. With no company entities in the catalog the universe is unknown and the cap is off.
4. `overlap:<key>`: at least `overlap_threshold` (default 0.6) of its companies already belong to
   ONE group: a single concept theme's member list (key = theme key), or one industry-chain theme
   (`industry:<theme key>`) or sub-industry (`sub:<theme key>/<sub-industry>`). One group, never a
   union, and the best-covering group is the one reported.

Survivors are ranked by `specificity = company_count * ln(N_tw / company_count)` (tf-idf style, `N_tw`
the catalog's `tw_company` count; plain `company_count` when `N_tw` is unknown), then total mentions,
then label. Each candidate record carries `specificity`. Industry-chain membership has no file source,
so it is injected: the weekly job reads it from the themes table (`theme_discovery/industry.py`); the
CLI does the same when the database is reachable and otherwise prints `industry overlap skipped:
database unavailable` and uses concept-theme overlap only (the database never fails the command).

Dropped entities are written to `theme_candidates_skipped.jsonl` beside the candidates file (one
object per line: `candidate_id`, `label`, `type`, `company_count`, `specificity`, `reason`), so the
operator can audit a drop. Entities below the company threshold are not listed. Keys and counts only.

- CLI: `python -m analytics.supply_chain discover --themes [--min-companies N] [--max-share F]
  [--overlap F] [--limit N] [--dry-run]` runs only this mining (no key and no research flag needed)
  and prints rank, label, type, company count, specificity, trend and up to five stock symbols, then
  `skipped=<n>`, never a company name.
- Job: `sc_theme_candidates` (weekly 22:05, leader-only, journaled). It spends nothing, so it is not
  gated by `SUPPLY_CHAIN_RESEARCH_ENABLED`; it journals SKIPPED with a reason when not the leader or
  when the entity files are absent.

#### Promoting proposals

`python -m analytics.supply_chain promote FILE... [--dry-run] [--min-confidence medium|high]`
(`themes_io/promote.py`) is the explicit, repeatable operator step that turns reviewed proposal files
into live `reference-data/themes/concepts/*.yaml`. It is never scheduled. Proposal files live under the reference directory (e.g. `reference-data/tools/`): a path that is not a `.yaml`/`.yml` file inside it (after resolving `..` and symlinks) is an invalid input file, exit 1, nothing written. A proposal file is a YAML
list; each row is either a **new theme** (`key, name_zh, name_en, description, related, thin,
members[]`) or a **member addition** (`theme_key, symbol, name, position, publisher, url, excerpt,
confidence`), detected per row. Every failed check is a counted reject reason, never a silent drop:

| Reason | Meaning |
|---|---|
| `unknown_theme` | member row names no existing (or just-created) concept theme |
| `theme_exists` | new-theme key or `name_zh` already used by a concept theme |
| `industry_collision` | new-theme key or name equals an industry-chain theme (the loader rejects it too) |
| `unknown_symbol` | not a `tw_company` in the entity catalog nor in an industry-chain roster |
| `invalid_position` | not upstream / midstream / downstream / related |
| `evidence_missing` | url not `https://`, excerpt empty or over 200 chars, or it names neither the company nor a catalog alias (case/width-insensitive) |
| `low_confidence` | below `--min-confidence` (default `medium` accepts high and medium) |
| `duplicate_symbol` / `already_member` | repeated within a new theme / already in the theme (what makes a re-run add nothing) |
| `schema_invalid`, `invalid_row`, `no_valid_members` | `ConceptThemeFile` validation, unrecognised row shape, new theme left with no accepted member |

The industry roster (keys, names, symbols) comes from the themes table; with the database unreachable
the command warns loudly, checks concept themes only and takes the symbol roster from the catalog.
Accepted members get origin `proposed`. Existing files are rewritten in field order (`safe_dump`,
`allow_unicode`, `sort_keys=False`, temp file then `os.replace`). Before any write the concepts directory
is copied to `themes/_backups/concepts-<UTC timestamp>/` (5 newest kept); every resulting file is
validated first and one failure writes nothing. `--dry-run` prints the report and writes nothing. A real
run appends one line to `themes/promote_log.jsonl` (timestamp, input file names, counts per reason; no
names). Output is counts per theme and reject reasons with symbols only; exit 1 on an unusable input
file. Run `load` (or wait for the 06:25 job) afterwards to index the result.

#### Theme research (Steps B and C)

`analytics/supply_chain/theme_research/` turns web pages into **proposal files** that `promote` reads.
It never edits a live concept-theme file and never calls `promote`.

```
discover --themes (A)      theme_candidates.jsonl ──┐
                                                    ├─> research-themes (B) ──> proposals/claims.jsonl
existing concept themes (rotating, oldest first) ───┘     search -> read -> extract    (raw claims)
                                                                                          │
                                  proposals/rejected.jsonl <── verify-themes (C) <───────┘
                                  (reason codes, no names)     quote guard, trust, staleness,
                                                               universe, membership, 2nd opinion
                                                                          │ verified only
                                                  proposals/new_themes_<UTC date>.yaml
                                                  proposals/theme_members_<UTC date>.yaml
                                                                          │
                                           HUMAN review ──> promote --dry-run ──> promote
```

**Step B** (`research.py`, `extract.py`, `targets.py`). `python -m analytics.supply_chain research-themes
[--limit N] [--source candidates|existing|all] [--dry-run]`. Targets: the unresearched entries of
`themes/theme_candidates.jsonl` in file order (new-theme proposals), and the existing concept themes
looking for **missing members**, least recently researched first (`themes/research_state.json`
records the date per theme key and candidate id, replaced atomically; a target that was skipped for a
cap or an outage is not marked). With `--source all` candidates take the first half of the limit and
the rest flows to existing themes. Per target: two Brave queries built from the label (`<label>
概念股`, `<label> 供應鏈 台灣`), then up to `SUPPLY_CHAIN_PAGES_PER_CLAIM` pages through the same
`SearchProvider` as claim verification (rejected publishers dropped, primary then secondary then
tertiary preferred; the fetch goes through `dataaccess.web_search`, which carries the SSRF, size and
timeout limits). A page older than `SUPPLY_CHAIN_MAX_SOURCE_AGE_DAYS` or not mentioning the theme never
reaches the model. One extraction call per page (`resolve_role_with_override(ModelRole.SUBAGENT,
SUPPLY_CHAIN_RESEARCH_MODEL)`, the page in the same delimited untrusted block as the claim judge)
returns `[{company, symbol|null, excerpt, position}]`. A company counts only when the **company
directory** resolves its name (`verification/directory.py`; a stated symbol is accepted only if the
directory knows it); anything unresolved, non-Taiwan, already a member, or whose excerpt is not literally
in the page is dropped and counted. A claim is `{theme, kind, label, symbol, excerpt, url, publisher,
retrieved_at, position, published_on}` (`theme` = concept theme key or candidate id), appended to
`themes/proposals/claims.jsonl` (one `O_APPEND` write, de-duplicated on theme + symbol + url).

**Step C** (`verify.py`, `proposals.py`). `verify-themes [--limit N] [--covered-threshold X] [--dry-run]` groups the claims
by (theme, symbol) and applies the **same evidence rules as supply-chain claims**: the symbol must be a
`tw_company` of the entity catalog (the universe) and resolve in the directory, an existing theme must
still exist and not already hold the symbol; each claim must pass the **quote guard** (https URL,
publisher not `rejected`, excerpt at most 200 characters and literally naming the company or an alias
AND the theme label or alias, compared ignoring case, width, spaces and punctuation, no advice wording,
source not older than `SUPPLY_CHAIN_MAX_SOURCE_AGE_DAYS` when dated). Surviving sources are graded by
`verification.pipeline.status_for`: **verified** with at least one primary/secondary source or two
independent tertiary registrable domains (`independence_key`; subdomains of one site count once, an
unlisted host is tertiary); a single tertiary source stays a **candidate** and is not written. With
`SUPPLY_CHAIN_SECOND_OPINION=true` a single weak source must also convince the SYNTHESIS model
(`SUPPLY_CHAIN_RESEARCH_SECOND_MODEL`), which sees only the excerpt; a refusal for budget skips the
group instead of writing it. Verified members become the two shapes `promote` reads: member rows
`{theme_key, symbol, name, position, publisher, url, excerpt, confidence}` for existing themes, and
`{key, name_zh, name_en, description, related, thin: false, members}` for a candidate that has **at
least 6** verified members (fewer stay pending; the claims are kept and counted again next run).
`confidence` is `high` with a primary source or two independent primary/secondary sites, else `medium`; `position` is the model's
majority guess. A pair already present in any proposal file is not proposed again, and writing merges
into the day's files (union, atomic replace), so a re-run changes nothing. Rejected claims append to
`themes/proposals/rejected.jsonl` as `{theme, symbol, url, rejected_on, reason}` (no names); reasons
include `unknown_theme`, `already_member`, `not_in_universe`, `url_not_https`, `host_rejected`,
`excerpt_too_long`, `company_not_in_quote`, `theme_not_in_quote`, `advice_language`,
`source_too_old`. A new-theme key is the slug of the first ASCII label or alias, else the candidate id;
review and rename before promoting.

**Coverage check (new-theme candidates).** Mining compares a candidate's *mentioning* companies with the
existing themes; Step C repeats the test on the candidate's **verified** members. For each concept theme
(`themes/concepts`, via the `themes_io` loader) containment = verified members already in it / verified
members; the best theme (ties to the smaller key) decides:

- containment >= `--covered-threshold` (module constant `COVERED_THRESHOLD`, default 0.7): the candidate is
  **covered**. No new-theme row; the members NOT already in the covering theme (if any) are written as
  member-addition rows for the covering theme into the day's `theme_members_<UTC date>.yaml`; the decision
  is appended to `themes/proposals/covered.jsonl` as `{candidate, covering, containment, verified, new,
  symbols, covered_on}` (ids, keys and stock codes only, no names); the console prints `candidate <id>:
  covered by <theme key> (<overlap>/<n>)` and the summary line and journal detail carry `covered=<n>`.
- 0.5 <= containment < threshold: the new theme is still proposed (6-member rule unchanged) with the
  covering theme key in `related`, and the console prints `candidate <id>: partly covered by <key>
  (<overlap>/<n>)`.
- below 0.5: proposed as before.

A covered candidate is not judged again: its claims for symbols listed in `covered.jsonl` are skipped
(`already_covered`), and the symbols count toward containment on later runs. It is proposed as a theme
only if it later gains verified members that pull the containment under the threshold. `covered.jsonl`
keeps the latest line per candidate; a re-run appends nothing. The generated files go through `promote`
unchanged.

**Gating and budget.** Both commands refuse (exit 1, one line on stderr) without `BRAVE_SEARCH_API_KEY`
and an LLM role for the research model (and the second model when the second opinion is on); a dry run
needs them too because it searches and judges, but writes no claim, state, proposal or rejection.
Caps are `verification/budget.py` with the `SUPPLY_CHAIN_RESEARCH_*` settings (targets/groups count as
claims); the first cap hit ends the run gracefully. Console output and the journal carry counts and ids
only (never page text or a company name). Neither command takes the ingest writer lease: they write
private proposal files only (no index, no DuckDB).

**Job.** `sc_theme_research` (monthly 22:30, leader-only, journaled) runs B then C with the caps and is
gated exactly like `sc_research`: it journals SKIPPED when `SUPPLY_CHAIN_RESEARCH_ENABLED` is off, the
process is not the leader, or the search key or LLM role is missing. It never calls `promote`: the
operator reviews `themes/proposals/*.yaml`, runs `promote FILE... --dry-run`, then `promote`.

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

> **Trust of automated evidence.** A host is primary or secondary only when the trust table says so
> (built-in list plus the private `publisher_trust.yaml`); every unlisted host, including one that
> merely starts with `ir.`, is tertiary. Unattended research (`verify`) publishes a relation only when
> a primary or secondary publisher, or two independent tertiary ones, back it; a lone tertiary source
> is written as a `candidate` for a person or a second source to confirm.
