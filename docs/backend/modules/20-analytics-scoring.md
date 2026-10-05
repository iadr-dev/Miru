# Module: Analytics, scoring & screening (`backend/src/analytics/`)

The **derived-compute layer** that turns the raw governed data (module 01 sources → 19 router
→ ingest store, module 22) into the higher-order analytics a stock-detail page and the agent
need: **technical indicators**, a **multi-factor 多空 (bull/bear) score**, an **AI summary +
SWOT**, an **AI-filtered top-N screener**, and **LLM theme discovery**. These are standard
quant-analytics building blocks; the indicator algorithms, factor blend, and screener funnel below are
**our** design (`backend/src/analytics/`), governed by this framework's non-trading rules.

**This is compute, not a data source.** Everything here is *derived* from data the router
already provides — it opens no sockets (the dependency rule holds: `analytics/` sits beside
`context/`, calls the router/store, never `sources/` directly). Its outputs are `public`
except where a factor is fed by `sponsor` data (then that factor is entitlement-masked, and
the composite is computed over available factors with the gap disclosed — never silently).

> **Governance is load-bearing here.** A "score" and a "多空 label" are the closest this
> product comes to a signal, so the non-trading rule (§1.1) is enforced hardest in this module.
> See [§7 Non-trading framing](#7-non-trading-framing-the-hard-line) — it is not optional.

> **Implementation status vs. this spec.** The 5-factor score engine (§2) is real and shipped —
> `analytics/score.py`, `analytics/models.py`, `analytics/normalization.py`,
> `analytics/fundamentals.py`, `analytics/scoring_batch.py`, `analytics/scoring_store.py`, the
> `get_stock_score`/`get_score_leaderboard` tools, and `/analytics/{symbol}/score` +
> `/analytics/rankings`. The **tap-tile tag screener** (a new §5a below) is real and shipped
> too, entirely separate from — and simpler than — the gates→setup→rank funnel this doc's §5
> describes: that funnel (`screener/gates.py`, `screener/screens.py`, named presets,
> `run_screener`, `get_factor_catalog`, `get_top_stocks`, the LLM theme-discovery pass, the AI
> summary/SWOT of §3) is **still pure design intent**, not built — see the callouts inline at
> §3 and §5 for exactly what exists in its place today. The **theme/tier/supply-chain layer**
> (a new §5b below) is real and shipped, and is a different, more elaborate three-layer design
> than the single LLM-discovery paragraph this doc's old §5C sketched.

---

## 1. Technical indicators (`analytics/indicators.py`)

> **Implementation status.** The base numeric indicator library below is real and shipped as pure
> functions over a `PriceBar` sequence in one flat module (`analytics/indicators.py`) — not the
> `analytics/indicators/` package + decorator-registry + `compute_indicators()` dispatcher this
> section originally sketched, and with **no `get_indicators` tool/REST route registered anywhere**
> to expose it yet (no `analytics/indicators/` directory, no `@indicator(name)` registry, no
> `compute_indicators()` function). The candlestick library is real (`analytics/patterns.py`) but
> covers **9** named patterns (doji, bullish/bearish engulfing, bullish/bearish continuation,
> V/inverse-V reversal, three-rising/three-falling), not 24, and each is a plain boolean ("does
> this pattern complete as of the most recent bar?"), not the +1/−1/composite-signal shape below.
> What *is* real from this section already feeds real callers directly: the score engine's
> trend-quality factor (§2) consumes the moving-average series, and the tag screener's technical
> tags (§5a) consume both the MA-cross/oscillator predicates and the pattern booleans.

A **pure indicator library** (`backend/src/analytics/indicators.py`) — each indicator is a pure
function over a `PriceBar` sequence, no I/O, no LLM.

| Group | Indicators | Notes |
|-------|-----------|-------|
| Moving average | `sma`, `ema`, `moving_average_bundle` (MA5/10/20/60 at once) | 均線排列 |
| Oscillators | `kd` (stochastic 9/3/3, TW-standard seeded at 50), `rsi` (Wilder 14) | |
| Trend | `macd` (12/26/9, EMA-of-DIF signal, dif/dem/histogram) | |
| Volatility | `bollinger` (20/2σ), `atr` | 布林通道 |
| Volume | `obv`, `vwap` | `vwap` is a same-day `turnover/volume` proxy, not textbook intraday-cumulative VWAP |
| Patterns (`analytics/patterns.py`) | doji, bullish/bearish engulfing, 多方炮/空方炮 (bullish/bearish cannon), V/倒V reversal, 紅三兵/黑三兵 — **9 patterns** | each a plain boolean, no composite per-bar signal |

- Series-returning indicators **left-pad with `None`** for the warm-up window where there isn't
  enough history — never a fabricated number standing in for "not computable yet". Bars must be
  supplied ascending by `trade_date`; this module never re-sorts.
- **No back-adjustment (還原股價) pipeline exists yet.** The ±10%-price-limit-implies-a-corporate-
  action adjustment method described here (and preferring FinMind `TaiwanStockPriceAdj` when
  present) is still design intent — real callers today run indicators directly on the ingested
  `close`/`high`/`low` series.
- **Framing:** a KD golden cross or a bullish engulfing is reported as a **描述** of the chart
  ("KD 在低檔黃金交叉"), never "buy" — enforced today at the two real consumers (the tag screener's
  `screener_flags` batch, §5a, and the score engine's trend-quality factor, §2), not yet at a
  dedicated `get_indicators` tool boundary since that tool does not exist.

### 1a. Structure signals (量能 + 支撐/壓力 + N 波回檔) — design intent, not built

> **Implementation status.** Nothing in this subsection exists in code today. There is no
> `analytics/indicators/structure.py`, no computed 量能/支撐壓力/N波回檔 read, and no `get_indicators`
> tool to expose a `set=...,volume,sr,wave` parameter through. Kept here, explicitly marked, as
> genuine design intent for future work rather than deleted — a comparable 爆量 volume-surge
> boolean **does** already exist, but as a screener tag (`analytics/screener/momentum.py`'s
> `volume_surge`, §5a), not as this section's richer structure read.

Beyond the point-in-time oscillators, a future `analytics/indicators/structure.py` would compute
the **chart-structure descriptions** a 技術面 read needs — all pure compute, all `public`, all
described (never a trade):

- **量能 (volume):** 均量 (5/20-day), **爆量** (volume ≫ moving-average volume) / **量縮**, OBV
  trend, VWAP vs price, 量價背離 (price up on shrinking volume) — surfaced as a *confirmation /
  divergence description*, feeding the technical factor's "volume confirm vs distribution" (§2).
- **支撐/壓力 (support/resistance):** swing-high/low pivots + recent congestion zones + round-number
  and prior-limit levels → a ranked list of nearby S/R with distance-to-price; plus **突破/跌破**
  detection reusing the **假突破/誘多 filter** (needs large body + volume spike + next-bar hold, §2)
  so a false break is labelled as such, not as a breakout.
- **N 波回檔 (impulse–pullback structure):** detect a **strong impulse leg into a key level (突破帶
  量), then a pullback that holds** above a prior support / a rising MA / a Fibonacci-style
  retracement of the impulse. Reported as *"急拉觸及壓力後回檔，回測 X 未破 → 型態尚存"* — a
  **structural observation**, explicitly **not** an entry/加碼 instruction (§7). The detector would
  emit: impulse leg (start/end, %), the tested level, pullback depth, and a hold/break flag.

These structure signals are inputs to the technical factor (§2) and the `technical-read` skill
([08](08-skills.md)); the tool `get_indicators` accepts `set=...,volume,sr,wave` to include them.

## 2. Multi-factor 多空 score (`analytics/score.py`, `analytics/models.py`, `analytics/scoring_batch.py`, `analytics/scoring_store.py`)

> **Implementation status.** This whole section is real and shipped, but the factor set, weights,
> and mechanics below are corrected from an earlier draft — verify against `analytics/score.py`
> before trusting a number in this section (that module's own docstring is the most current
> restatement of this design). The biggest corrections vs. the original text: the momentum factor
> is the academically-verified **12-1** convention, not an IBD-style multi-window blend; the
> value/quality factors are **z-scored composites of several sub-metrics**, not a single ratio
> each; there is **no `月營收` (monthly revenue) sub-signal yet** — the revenue/flow factor
> currently runs off the 三大法人 chip sub-signal alone, with that gap disclosed in the factor's own
> description; there is no separate news-sentiment sub-module, no per-asset-kind ETF/US variant,
> and the regime overlay is a fixed disclosed-as-unassessed caveat, not a live TAIEX-vs-200MA
> read. `unavailable_reason`/`partial_factors` are the load-bearing "never fabricate a number"
> mechanism throughout — a factor a symbol's ingested data cannot support is reported as
> genuinely missing, never defaulted to a neutral 50.

The bull/bear engine — **pure computation, no LLM** — updated daily post-close by the ingest
layer (module 22) and cached in the relational `stock_scores` table (see
[database-schema.md](../database-schema.md) for its columns). Designed the way a professional
would (see [§2b](#2b-composite-blend--horizon-aware)): a **lean, low-correlation factor set**,
each converted onto the same 0–100 "higher is stronger" axis (either a cross-sectional
winsorized-±3σ percentile, or — for the two fundamental factors — a winsorized z-score against a
comparison universe mapped through the standard normal CDF), then a weighted blend. **Not 15
factors — 5**, because added factors are correlated noise that overfit and can't be explained to
the user (violating our §7 transparency rule).

### 2a. The five factors

| Factor | `data_class` | How it's actually computed (`analytics/score.py`) | Why it's in the lean set |
|--------|-------------|------------------|--------------------------|
| **Momentum** | public | **12-1 momentum** — trailing 12-month total return excluding the most recent month (`twelve_minus_one_momentum`), the verified academic convention that avoids short-term-reversal contamination — cross-sectional winsorized percentile, capped (not zeroed) when the stock is >~20% above its 50-day MA (over-extension) | The single most robust cross-sectional premium |
| **Trend quality** | public | Minervini trend-template condition count (0–8, `trend_template_score`) → `(count/8)*100`; the 8th condition (RS percentile ≥ 70) is counted as *not met* when a momentum percentile isn't supplied, a conservative disclosed choice | Turns "is it in an uptrend" into a graded number, not a vibe |
| **Value guard** | public | Z-scored composite of whichever of **EV/EBITDA, FCF yield, P/E, P/B** the ingested financial-statement + market-value data can assemble (`analytics/fundamentals.py`); winsorize → z-score each present sub-metric against a comparison universe → equal-weight-combine → map through the normal CDF to 0–100 | **The anti-追高 brake** — stops pure momentum ranking crowded 概念股 at the top |
| **Quality** | public | Same z-scored-composite construction over whichever of **ROE, debt/equity (penalty direction), earnings variability (penalty direction), ROIC, gross profitability** the data supports | Filters "empty rockets" (high momentum on a pump, no earnings) |
| **Revenue / flow (籌碼)** | public today | **三大法人 (foreign + investment-trust) 20-day net-buy trend** only (`net_buy_20d_trend`), winsorized cross-sectional percentile — the **月營收 YoY sub-signal the original design called for is not yet ingested**, so the factor's own description discloses it is reporting the chip-flow half only | TW is flow-driven; a TW score that ignores 籌碼 feels wrong to local users |

Every fundamental sub-metric degrades independently (a symbol missing one line item just drops
that sub-metric, not the whole factor); a factor with **zero** usable sub-metrics/history becomes
`FactorScore(percentile=None, unavailable_reason=...)` and is dropped from the blend below, never
defaulted to a fake neutral value. There is no `sponsor`-tier sub-signal in the shipped revenue/
flow factor today (三大法人 flow is `public`) — the 分點-derived `sponsor` sub-signal the original
design described for this factor has not been built; when it lands it will need the entitlement-
masking mechanics this section still documents in §2c.

### 2b. Composite blend — horizon-aware

Two weight profiles (`ScoreProfile.SWING` / `ScoreProfile.POSITION` in `analytics/score.py`),
because a swing trader and a position holder weight the *same factors* differently. Default (the
tools' own default) = swing.

```
composite = Σ (weight_i · percentile_i) / Σ weight_i     # renormalized over available factors, 0–100
```

| Factor | **Swing** (default) | **Position** |
|--------|-------------------------------|------------------------|
| Momentum | **0.35** | 0.15 |
| Trend quality | **0.25** | 0.15 |
| Value guard | 0.10 | **0.25** |
| Quality | 0.10 | **0.30** |
| Revenue / flow | **0.20** | 0.15 |

> **Why this blend (and why we rejected a theme-heavy one).** A naive/common blend —
> `chip .35 / technical .30 / theme .20 / fundamental .15` — a real trader would reject two
> things: (1) **`theme .20` is 追高 fuel** — 20% of the score chasing whichever narrative is hot
> surfaces stocks *after* the move. Theme is now **demoted to a tag, not a scored factor** (see
> [§5b](#5b-theme-tier--supply-chain-layer-analyticsthemes) — theme still tags/groups stocks, but
> never lifts the score). (2) There was **no valuation/quality guard**, so a thin pump could score
> high. Momentum now carries the up-trend signal (measurable, no narrative-guessing), and
> value+quality are explicit brakes. **籌碼 stays high** (0.20, top-3 weight) because it genuinely
> leads price in Taiwan — the instinct to weight it was right; it just can't be the *only* thing.

- **Over-extension penalty:** a stock **> ~20% above its 50-day MA** has its momentum percentile
  *capped* at 70 rather than left to rank at the top (`apply_over_extension_cap`) — a naive
  momentum score ranks the most stretched names top, right before mean-reversion.
- **Regime overlay:** every `ScoreResult` carries a fixed `regime_caveat` disclosing that
  market-regime context (e.g. TAIEX vs its 200-day trend) is **not evaluated** in this pass, and
  that any momentum-driven factor should be read with the same elevated reversal risk you'd assume
  in any unassessed market condition. This is a real, honest caveat about a **not-yet-built**
  live regime read — the original design's "computed during a market downtrend" dynamic overlay
  does not exist yet.
- **Normalization caveat:** every result also discloses whether value/quality were z-scored
  against sector peers or, absent an ingested sector classification, the whole universe (a
  coarser fallback) — never silently presented as sector-relative when it wasn't.
- **±5 analyst overlay, 投信作帳 flagging, 除權息-adjusted-price requirement, look-ahead-bias
  guard** — all still genuine design intent (§2d), **not implemented** in `analytics/score.py`
  today; the module computes the five factors and the blend only.

### 2c. Mechanics

- **Entitlement handling:** the shipped revenue/flow factor has no `sponsor` sub-signal today (see
  §2a), so there is nothing to mask yet for this factor specifically. The general mechanism this
  subsection originally described — mask a `sponsor` sub-signal for Free users, renormalize the
  blend over the *available* factors, and disclose which factor was partial via
  `ScoreResult.partial_factors` — **is** real and load-bearing for any factor that genuinely lacks
  data for a symbol (e.g. missing financial statements), just not yet exercised by an actual
  entitlement gate inside this module.
- **Cache-only, batch-computed:** there is no `resolve_score`/on-demand recompute path.
  `get_stock_score` (the tool) reads the cached `stock_scores` row and returns an honest
  `found=False` if today's batch hasn't run yet — it deliberately never live-computes, because
  every factor here is cross-sectional and answering for one symbol would still require reading
  and ranking against the whole day's universe (see `tools/score_tools.py`'s docstring). The batch
  (`analytics.scoring_batch.job_scoring_batch`, the **`scoring_batch`** job, scheduled 23:55
  Asia/Taipei — [module 22](22-data-ingest.md) §2) computes and upserts both horizon profiles for
  **every** symbol the day's ingested price/institutional-flow/financial-statement/market-value
  data covers, in one pass, so every symbol's percentile ranks against the true whole-day universe
  rather than a re-derived subset. Tools: `get_stock_score` (one symbol's cached composite +
  breakdown), `get_score_leaderboard` (a sort-and-slice read of the same cache, top/bottom-N by
  composite — never a live rescan). REST: `/analytics/{symbol}/score`, `/analytics/rankings`
  (§6).
- **No per-asset-kind ETF/US variant yet.** The blend renormalization described here is real, but
  there is no `tw-etf`/US-specific factor substitution (`detect_kind`, NAV/折溢價/流向 factors) —
  the five factors above are computed uniformly; ETF/US-specific scoring is still design intent
  for the multi-market future (module 19).
- **No news-sentiment sub-module.** There is no `scoring/news_sentiment.py` and no 追高風險
  divergence caveat wired into the composite — still design intent.
- **Label mapping:** composite → **偏多 / 中性 / 偏空** descriptive band (`ScoreBand`, via
  `band_from_composite` — ≥60 bullish-leaning, ≤40 bearish-leaning, else neutral), alongside the
  numeric score, the horizon profile used, and the full factor breakdown. This is the "多空" —
  presented as *analytical context with caveats*, not a signal (§7).

### 2d. Where a naive score misleads (design intent beyond what's coded today)

The research flagged the classic traps. Two are coded rules already (marked); the rest remain
design intent, not yet implemented in `analytics/score.py`:
- **追高 / theme-chasing** → the value guard factor + the over-extension cap (§2b) — **coded**.
- **Momentum with no quality/value floor** → the value + quality factors exist and are blended in
  — **coded**; the specific 月營收 sleeve inside revenue/flow is **not yet ingested** (§2a).
- **Liquidity ignored** → there is no gate at all yet in the score module — the tag screener
  (§5a) and the still-design-intent Stage-A/B/C funnel (§5) are the places a liquidity gate would
  live; the score engine itself has never applied one.
- **投信作帳 window-dressing** (quarter-end flow spikes flagged lower-confidence) — design intent,
  not coded.
- **除權息 distortion** (technical/momentum factors should use adjusted prices, 還原股價) — design
  intent; no back-adjustment pipeline exists yet (§1).
- **Look-ahead bias** (月營收/財報 factors should only use data after its disclosure date) — design
  intent for the eventual 月營收 sub-signal; the currently-ingested financial-statement factors read
  whichever partitions have landed, with no explicit disclosure-date gate yet.

## 3. AI summary + SWOT (`analytics/summary/`) — design intent, not built

> **Implementation status.** Nothing in this section exists in code. There is no
> `analytics/summary/` module, no `compute-ai-summary` job, no `ai_summaries` table (confirmed
> absent from `db/models.py` — the only score-related table is `stock_scores`), and no
> `get_ai_summary`/`get_swot` tool. Kept here as genuine design intent, not deleted — it depends
> on the score engine (§2), which is real, so it is buildable as described whenever this feature
> is prioritized.

The stock-detail **AI 智能摘要 + SWOT** (Screenshot_3 reference: 欣興 智能摘要 / 市場定位).

- **Input = the deterministic factor scores + breakdown (§2) + company profile + recent
  catalysts/news** — the LLM *summarizes structured facts*, it does not invent numbers.
- **Output** = a caveated markdown card: a 市場定位 line + factor-grounded narrative + a **SWOT**
  (Strengths / Weaknesses / Opportunities / Threats) each bullet tied to a cited datum
  (e.g. "S: 外資連 12 買 (籌碼)"; "T: PER 位於 5 年 90 分位 (估值)").
- **Cadence — full-universe weekly batch + on-demand only when the row is empty:**
  the **`compute-ai-summary` job** ([module 22](22-data-ingest.md) §Derived-compute) runs a
  **weekly batch over ALL listed+OTC stocks (~1,900)** on the **weekend (Sat/Sun)** — after the
  week's final (Friday) scores, when the market is closed and load is near zero — and **upserts
  into `ai_summaries`** so every stock always has a card. This is affordable: cheap-tier
  summarization ≈ single-digit-to-low-tens of $/week (~9M in / ~1.2M out tokens), so full
  coverage is preferred over a "covered universe only" subset.
- **On-demand generation fires only when the DB row is missing/empty** — realistically just a
  **new IPO** listed since the last weekend batch. **If the row has data, it is served as-is — no
  refresh.** No staleness threshold, no event-invalidation in the default design (kept simple by
  choice). Uses the **cheap-tier** model (orchestration §model-routing) — summarization, not
  reasoning. Each row is stamped with its factor-snapshot date (display + provenance).
- **Trade-off (accepted):** the LLM *prose* can be up to ~7 days old between weekend batches,
  even though the underlying score/chips/revenue update **daily** (only the narrative lags). If
  event-driven freshness is ever wanted, nulling a row on a material event (earnings / large score
  move / 重大訊息) makes the next view regenerate it — an **optional** opt-in, not in the default.
- **Governance:** passes `guardrails.check_output` like any agent output — **no price target,
  no buy/sell**, every claim caveated and sourced. A SWOT "Opportunity" is a *factor
  observation*, never "進場點". The card renders the currentness caveat and an "AI-generated
  summary of data, not advice" footer.
- Tools: `get_ai_summary`, `get_swot`. Entitlement: **Premium+** (an AI feature, tier-gated
  axis 2 — [modules/12](12-multi-user-tiers.md) §3); Free sees a blurred teaser.

## 4. Analyst overlay (yfinance, attributed) — design intent, not built

> **Implementation status.** `get_analyst_consensus` itself is real (module 03,
> `sources/yfinance/analyst.py`), but it is **not wired into the composite** — `analytics/score.py`
> has no analyst-overlay term, and `ScoreResult` has no field for one. Kept here as design intent:
> the tool this section would nudge from already exists, only the wiring into the blend doesn't.

The composite would add a small **±5 analyst-recommendation bonus** from `get_analyst_consensus`
(module 03, yfinance, large-cap). This would be surfaced strictly as an **attributed third-party
estimate** ("N analysts, 目標價 X, as of <date>") and the guardrail would block restating a target
or rating as *our* view (§7). `null` when `numberOfAnalystOpinions == 0` → the overlay would
simply be omitted (no synthetic value).

## 5. AI-filtered top-N screener funnel (Stage A/B/C) — design intent, not built

> **Implementation status.** Everything below this line through the end of §5 (Stage A gates,
> Stage B setup-filter rule screens and named presets, Stage C score-and-rank, `run_screener`,
> `get_factor_catalog`, `get_screener_presets`, `get_top_stocks`, saved/shared screens, the
> `compute-topN` job) is **pure design intent — none of it exists in code.** There is no
> `screener/gates.py`, no `screener/screens.py`, no `screener/presets/`, no `run_screener` tool,
> no `saved_screens` table, and no `/analytics/screener/run` or `/analytics/screener/factors`
> route. What IS real and shipped in `analytics/screener/` is a **different, simpler tag-based
> screener** — described in the new [§5a](#5a-the-real-tag-screener-analyticsscreener) below —
> that reads named end-of-day boolean tags (not numeric field conditions) off a nightly batch, with
> no elimination-gate/setup-filter/score-and-rank funnel and no REST route of its own yet. The
> theme-discovery paragraph at the end of the original §5C is also superseded — the real theme
> layer is a three-layer design covered in [§5b](#5b-theme-tier--supply-chain-layer-analyticsthemes).
> The rest of this section is preserved below, explicitly marked, because it remains real design
> intent for a possible future numeric screener distinct from the tag screener.

**A real trader screens as a funnel — eliminate first, score last.** Nobody scores 2000 stocks
and ranks; they kill ~70–85% with binary gates, apply a setup filter, then score only the
survivors. This funnel design follows that shape. "AI-filtered" would be mostly **rule-scored
SQL**, not LLM. It would read the ingest store (module 22); short cache keyed on the latest
trading date.

```
~2000 stocks
  │  STAGE A — HARD ELIMINATION GATES (binary, no score, no mercy)
  ▼  ~300–500 tradable
  │  STAGE B — SETUP FILTER (the strategy thesis: trend / value / breakout / 籌碼)
  ▼  ~30–80 candidates
  │  STAGE C — SCORE & RANK the survivors  ← the §2 composite lives HERE, not before
  ▼  top N
```

### Stage A — hard elimination gates (`screener/gates.py`)

Binary eliminate, applied **before any score** (a 處置股 is *gone*, not "scored 45"). Config
`SCREENER_GATES` (tunable defaults from real TW practice):

| Gate | Default threshold | Why |
|------|-------------------|-----|
| Liquidity — 20d avg 成交金額 | ≥ NT$50M (retail-swing); institutions want ≥ NT$300M | slippage/fill — the single most-used gate |
| Price floor | ≥ NT$10 | penny/microcap spread + manipulation risk |
| Market-cap floor | ≥ NT$5–10B | investability + data quality |
| **處置股 / 注意股 / 全額交割股** | **exclude** | execution mechanics change; distress flag (already in our data, module 03) |
| 停牌 / 變更交易方式 | exclude | untradeable |
| New listing < ~3–6 months (蜜月期) | exclude/quarantine | no base, no MA/RS history |
| Data completeness (< 200d history, missing financials) | exclude | can't score reliably |

**Liquidity is a gate, never a weighted factor** — a gate can't be "bought back" by a great
score, a weight can. This is the single most important structural fix vs. the old "score
everything" design.

### Stage B — setup filter: rule screens + named pro frameworks (`screener/screens.py`)

~50 SQL screens over the columnar store, plus **named professional presets** users recognize,
exposed via a **bilingual factor catalog** (技術 / 基本面 / 動能 / 籌碼):

| Category | Example screens |
|----------|-----------------|
| 技術 | 趨勢股 (MA5>20>60), 站上均線, 布林突破, 突破新高, 均線黃金/死亡交叉, KD 交叉, MACD 柱翻正, 長紅/長黑 |
| 基本面 | 連 N 月營收年增 >X%, 月營收/EPS 創新高, 三率三升, ROE/ROA (TTM), 負債比, 速動比, 現金殖利率 |
| 動能 | 近 5/10 日強勢, 急漲, 爆量 |
| 籌碼 | 法人搶買/連買, 外資/投信連 N 買, 外資持股率變化, 融資減券增 |

**Named preset screens** (`screener/presets/` — the frameworks a trader actually knows, each
purely descriptive/non-trading):
- **Minervini Trend Template** — the 8 conditions (price > MA150 & MA200; MA150 > MA200; MA200
  rising ≥1mo; MA50 > MA150 & MA200; price > MA50; ≥30% above 52w-low; ≤25% below 52w-high;
  RS ≥ 70). Also feeds the trend-quality factor (§2a).
- **CANSLIM (O'Neil)** — 季 EPS YoY ≥ 25%, 年 EPS growth ≥ 25% × 3y, ROE ≥ 17%, near 52w-high (N),
  volume surge on up-days (S), RS ≥ 80 (L), 法人持股上升 (I), TAIEX uptrend (M).
- **Weinstein Stage-2** — price > 30-week MA ∧ MA slope > 0 ∧ RS-line positive.
- **Piotroski F-Score ≥ 7** — the 9-point quality gate (ROA>0, CFO>0, ΔROA>0, CFO>NI, ↓debt,
  ↑current ratio, no new shares, ↑gross margin, ↑asset turnover).
- **Greenblatt Magic Formula** — rank by EY (EBIT/EV) + ROC, exclude financials/utilities.
- **TW favorites** — 月營收 YoY 創新高 + 站上季線; 三率三升; 投信連 N 買 + 外資同買.

Tools: `run_screener`, `get_factor_catalog`, `get_screener_presets`. Rows enriched with latest OHLC
+ sparkline + which gates/framework they pass.

**Screener contracts (support the pro condition builder — web screener/README.md §4):**

- **`get_factor_catalog(market)`** returns the bilingual catalog **and per-market numeric field
  descriptors** the builder's sliders need — for each factor:
  `{key, label_zh, label_en, family, unit, class, min, max, step, enum?, preset_chips?}`
  (e.g. `roe → {unit:'%', min:-20, max:60, step:0.5, preset_chips:[15,20]}`;
  `market_cap → {unit:'億', min:5, max:30000, step:5}`). A factor appears **only** if the market's
  adapters `supports()` its capability (module 19 §3) — so US omits 籌碼, ETF omits single-stock quality.
- **`run_screener`** accepts **either** a named entry **or** a custom condition list:
  ```
  run_screener({
    market: "TW"|"US",           # asset_class facet via ETF query below
    trade_date?: date,
    gate_overrides?: {...},       # stage-A gate tuning (SCREENER_GATES)
    # stage-B setup — one of:
    preset?: <preset_id>,         # named framework (populates the same conditions server-side)
    screen_type?: <rule_id>,      # a single ~50 built-in SQL screen
    conditions?: [ {factor, op, value|value2} ],   # custom builder rows
    match?: "AND"|"OR"            # how conditions combine (default AND)
  })
  ```
  `op ∈ {gt, gte, lt, lte, eq, between, in}` (`between` uses `value`+`value2`; `in` for enum factors).
  A **count-only probe** (`{..., count_only:true}`) backs the live "符合 N 檔" survivor count without
  materializing rows. Sponsor-factor conditions require entitlement (else the column is masked, §screener governance).
- **Per-market screening paths.** `run_screener` is TW-first (FinMind columnar store). **US** = a
  yfinance equity/etf query path (`market:"US"`, valuation/growth/momentum/analyst factors, **no 籌碼**);
  **ETF** = `(TW, etf)` with 折溢價/AUM/受益人/持股 factors (`get_etf_nav_premium`, `get_etf_aum`,
  `get_etf_beneficiaries`, `get_etfs_holding_stock`) instead of single-stock quality. US is **phased**
  (module 19) — until its adapters land, `market:"US"` returns a `not_supported` result the UI degrades
  to "尚未支援" (no fabricated rows).
- **Saved / shared screens** — `run_screener` is stateless; a user's named screens persist via
  **`/analytics/screener/screens`** CRUD (below), storing `{name, market, conditions, match,
  gate_overrides}`. A shared screen is a read-only, URL-encodable reconstruction of that state
  (non-trading — a filtered list, not advice).

### Stage C — score & rank the survivors (`get_top_stocks`)

**AI top-N** — `get_top_stocks(profile=swing|position, sort=composite|momentum|value|quality|flow,
n=20, screen?=<preset>)`: applies Stage-A gates, optionally a Stage-B setup, then ranks the
survivors by the §2 composite (or a single factor percentile), each row carrying its **factor
breakdown** + optional §3 mini-summary. **This is the "AI 篩選前 20 名"** — ranked *descriptively*,
with the market-regime + over-extension caveats (§2b) on the list.

- **Cadence:** the **`compute-topN` job** ([module 22](22-data-ingest.md) §Derived-compute) runs
  **daily, immediately after `compute-scores`** (it's a ranking *of* the day's scores, so it must
  follow them); the ranked lists are cached and served instantly. On-demand `get_top_stocks` with
  a custom screen/profile computes over the already-cached scores (no re-score).

**C+. LLM theme discovery — superseded by the real three-layer design (§5b).** The
`discover_themes(days)` news/figure-events/hot-stocks LLM-proposal flow described here does not
exist; what shipped instead is a more elaborate three-layer theme system covered in §5b below.
`get_theme_flow` and `figure_events` also do not exist as tools.

- **Screener governance (still applies to the funnel design above):** a screen result would be a
  **filtered list matching described criteria**, presented as such ("符合『外資連 5 買』的個股"),
  never "these are the stocks to buy" — the same non-trading framing the real tag screener (§5a)
  already enforces today.
- **Entitlement (design intent for this funnel):** the rule screener + top-N ranking would be
  **public** where every factor is public; a screen whose criteria require `sponsor` factors
  would be Premium+ (or mask the sponsor column).

## 5a. The real tag screener (`analytics/screener/`)

This is what actually ships today in place of the Stage A/B/C funnel above: a **tap-tile,
named-boolean-condition screener** — a symbol either carries a named end-of-day tag or it doesn't,
there is no numeric field/operator/value query shape, and there is no elimination-gate or
setup-filter/score-and-rank funnel. It is a genuinely different, simpler design, not a partial
implementation of §5.

- **The catalog (`analytics/screener/catalog.py`)** is a fixed, code-defined `TagDefinition` tuple
  (not a YAML file — an editor cannot add a tag without also wiring its predicate in code). Every
  entry has a stable `key`, bilingual labels, a `TagCategory`, a plain-prose description, and a
  `data_class` (every tag today is `PUBLIC`, including the chip-streak tags — even though they
  derive from institutional-flow data, they rest on the same public 三大法人 totals
  `get_institutional_flow` already classes public, never the branch-level 分點 data
  `get_chip_flows` gates as sponsor). The catalog has **4 categories** — `TagCategory.TECHNICAL`
  (技術面), `TagCategory.CHIP` (籌碼面), `TagCategory.HOT` (熱門), `TagCategory.SMART` (智能) — and
  **29 tags** as of this writing: **18 technical** (9 MA-cross/oscillator/MACD events —
  `ma5_turning_up`/`_down`, `golden_cross_5_20`, `death_cross_5_20`, `macd_histogram_turned_
  positive`/`_negative`, `breakout_above_ma20`, `kd_golden_cross`, `kd_death_cross` — plus
  `analytics/patterns.py`'s **9** candlestick booleans: doji, bullish/bearish engulfing,
  bullish/bearish cannon, V/inverse-V reversal, three-rising/three-falling), **4 chip-streak**
  tags (外資/投信連3買/賣 streaks + a 5-day cumulative foreign net-buy), **3 momentum/熱門** tags
  (volume surge, new 52-week high/low), and **4 pre-bundled 智能** combo tags that AND together
  already-catalogued tags (e.g. `smart_breakout_volume` = `breakout_above_ma20` AND
  `volume_surge`). Fundamental and theme tag categories are explicitly future dispatches, not
  built. `TagCategory` and the exact catalog are the source of truth — re-read
  `analytics/screener/catalog.py` if this count needs verifying again later.
- **Daily-batch-then-fast-read architecture, never live per-query** (`analytics/screener/batch.py`
  → `job_screener_flags`, scheduled **20:30 Asia/Taipei**, after the day's price (17:40) and
  institutional-flow (20:10) pulls it depends on — [module 22](22-data-ingest.md) §2/§3). The
  batch reads price + flow history back from the derived store, evaluates every tag's predicate
  (`events.py` for MA-cross/MACD/KD events, `analytics/patterns.py` for candlesticks,
  `chip_streaks.py` for the 籌碼 streaks, `momentum.py` for volume-surge/52-week extremes) for
  every symbol, and writes only the **true** `(symbol, tag)` pairs to the `screener_flags`
  Parquet dataset (a tidy/long layout — an empty day stays a small partition, and adding a tag
  later needs no schema change). The query-time read seam
  (`analytics/screener/store.py::run_tag_screener_query`) then just reads that partition back and
  computes the AND/OR match — it never re-evaluates a predicate per query.
- **AND/OR match semantics** (`schemas.screener.TagScreenerQuery`): a query is a list of
  `TagCondition{tag, negate}` plus a `match: "AND"|"OR"`. An AND result is symbols satisfying
  every requested condition; an OR result is symbols satisfying any, each hit's `matched_tags`
  showing exactly which. **Negation** is evaluated against the partition's own symbol universe
  (every symbol carrying at least one true flag that day) minus the flagged set — a symbol with
  no flag at all that day is not in the partition and is therefore not considered a negation
  match, disclosed via `detail` rather than silently treated as "the whole market minus the tag".
  An unknown tag key resolves to zero matches for that condition, never an error, with the unknown
  key reported in `detail`.
- **Exposure — tools only, no REST route yet.** `run_tag_screener` and `get_tag_catalog`
  (`tools/screener_tools.py`) are registered agent tools, both `public`, both reading the derived
  Parquet store directly (not the data-access router) with the blocking DuckDB read offloaded to a
  threadpool. There is **no `/analytics/screener/*` REST route wired to this tag catalog** —
  `api/routers/analytics.py` only exposes the score/leaderboard routes (§6); a frontend tap-tile UI
  would need a new route added, which has not happened yet.
- **Governance:** identical framing to the rest of this module — a result is "符合『外資連3買』的個
  股", never a recommendation list; `TagScreenerResult.detail` is never empty, so an empty/failed
  query still carries an honest reason rather than fabricated matches.

## 5b. Theme / tier / supply-chain layer (`analytics/themes/`)

A real, shipped three-layer design — substantially different from, and more elaborate than, the
single LLM-discovery paragraph this doc originally sketched for §5C. See also
[database-schema.md](../database-schema.md) for the `themes`/`theme_memberships`/`theme_tiers`/
`supply_chain_edges`/`theme_change_log` table shapes and [api-reference.md](../api-reference.md)
for the REST surface.

- **Layer 1 — deterministic, industry-chain-derived themes (`analytics/themes/seed.py`,
  `seed_industry_themes`).** Monthly ingest job (the **`theme_industry_chain`** scheduled job,
  fires 00:30 Asia/Taipei on a rolling ~4-week cadence — [module 22](22-data-ingest.md) §2/§3):
  pulls FinMind's whole-market industry-chain classification roster and turns every distinct
  `industry` value into an `industry`-kind `Theme`, with every stock carrying that industry
  becoming a member (`ThemeSource.INDUSTRY_CHAIN`). Each theme's membership snapshot is a clean
  replace on every run (`replace_theme_members`), never a growing union. A Free-tier token with no
  Sponsor access to this dataset, or a transient empty response, is a graceful skip that leaves
  the existing snapshot untouched — never a wipe-to-zero. This layer's membership set is also what
  covers the **whole listed roster**, which is why it doubles as the real-symbol universe the
  Layer-3 fabrication safeguard checks against (below).
- **Layer 2 — curated tier map + keyword-heuristic backfill (`analytics/themes/tiers.py`,
  `tiers_seed.py`, `seed_theme_tiers`).** Runs immediately after Layer 1 in the same monthly pass
  (same **00:30** fire time, sequenced after `theme_industry_chain` in the daemon's job tuple).
  Two producers assign every `(theme, sub_industry)` pair an upstream/midstream/downstream
  position, **curated always wins**: a small editorial YAML map (`config/theme_tiers.yaml`,
  `ThemeTierSource.CURATED`) is applied first; then `classify_tier` — a pure, I/O-free substring-
  keyword classifier over Taiwan business vocabulary (downstream keywords checked before upstream,
  before midstream; an unmatched segment defaults to midstream) — backfills every OTHER
  `(theme, sub_industry)` pair Layer 1 actually produced membership for
  (`list_uncurated_sub_industries`), written `ThemeTierSource.HEURISTIC`. Curated-wins is enforced
  twice over: the heuristic backfill only visits pairs the curated pass didn't cover, and
  `upsert_theme_tier` itself refuses a heuristic write against an already-curated row — so a
  re-run in any order can never flip an editor's decision back to a guess.
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
- **Exposure:** `get_theme_members`, `get_symbol_themes`, `get_theme_tiers`
  (`tools/theme_tools.py`) and three REST routes (`api/routers/themes.py`):
  `GET /themes/{key}` (identity + members with sub-industry/confidence/source),
  `GET /themes/{key}/tiers` (sub-industries grouped upstream/midstream/downstream, each marked
  curated or heuristic), `GET /symbols/{symbol}/themes` (the reverse lookup). All `public`, all
  reachable anonymously (`optional_user`) — this is platform reference data, not user-scoped
  state.
- **What this layer does NOT do (and the original §5C did claim):** there is no `discover_themes`
  news/figure-events scan, no `get_theme_flow` (三大法人 net flow per theme), no `figure_events`
  tool, and Layer 3 is not Premium+-gated or tier-gated at all today — every theme tool/route is
  `public`. Theme membership is never a scored factor (§2b already covers why) — it tags/groups
  symbols only.

## 6. Tools & endpoints

**Real, registered tools:** `get_stock_score`, `get_score_leaderboard` (§2c),
`run_tag_screener`, `get_tag_catalog` (§5a), `get_theme_members`, `get_symbol_themes`,
`get_theme_tiers` (§5b). `get_analyst_consensus` exists (module 03) but is not wired into the
score. **Not real** (design intent, listed in the original spec below): `get_indicators`,
`get_ai_summary`, `get_swot`, `run_screener`, `get_factor_catalog`, `get_screener_presets`,
`get_top_stocks`, `get_theme_flow`, `get_themes`, `get_figure_events`.

**Real REST** (see [api-reference.md](../api-reference.md) §4a — that doc's table still lists
several routes below as if shipped; cross-check against this list before trusting it):
```
GET /analytics/{symbol}/score?profile=swing|position   # composite + 偏多/中性/偏空 + factor breakdown + regime caveat
GET /analytics/rankings?market=TW&profile=swing&direction=bullish&limit=20&as_of=  # leaderboard, cache-only
GET /themes/{key}                        # theme identity + members
GET /themes/{key}/tiers                  # upstream/midstream/downstream tiers
GET /symbols/{symbol}/themes             # reverse lookup: a symbol's themes
```
Not real (design intent, no route wired): `/analytics/{symbol}/indicators`,
`/analytics/{symbol}/summary`, `/analytics/screener/factors`, `/analytics/screener/run`,
`/analytics/themes`, `/analytics/screener/screens` (+ PATCH/DELETE).

Skills: `technical-read`, `multi-factor-score`, `ai-summary-swot`, `stock-screener`,
`theme-discovery` ([modules/08](08-skills.md)) — verify against that module's own doc for which
of these are actually registered; several are named for still-design-intent tools above.

## 7. Non-trading framing (the hard line)

Scores, labels, screens, and theme data are the product's closest approach to a signal, so the
guardrail is strictest here. Enforced in `guardrails.check_output` + this module's own contract —
the bullets below describe the target framing; where a feature (SWOT, analyst overlay, news
divergence caveat) is still design intent rather than shipped (§3/§4/§2d), its governance
obligation applies from the moment it ships, not before:

- **Descriptive, not prescriptive.** 偏多/中性/偏空 describes *what the factors currently show*,
  not what to do. Never "買/賣/加碼/減碼/進場/停損", never a price target of our own, never
  position sizing. A screen/tag-match result is "符合條件的清單", not "推薦名單".
- **Show the work.** Every `ScoreResult` ships its **factor breakdown + the underlying 描述** so
  it reads as transparent analysis, not an oracle — real today (`analytics/score.py`). The number
  without its basis is never surfaced.
- **Attributed externals.** Analyst targets/ratings (§4, not yet wired into the composite) would
  always be "N analysts, as of <date>", never restated as ours, once built.
- **Caveated + current.** Every `ScoreResult` carries `non_trading_caveat`, `regime_caveat`, and
  `normalization_caveat` — real today. The SWOT card's "AI-generated summary of data, not advice"
  footer (§3) applies once that feature is built.
- **Divergence honesty.** The 假突破 filter and the news-factor 追高風險 caveat described in earlier
  sections are still design intent (§1a, §2c) — they exist to *warn*, not to hype, and the design
  bias toward disclosing weakness over manufacturing conviction is the standard the real factor
  descriptions (`unavailable_reason`, `normalization_caveat`) already hold themselves to today.

## Governance obligations
- `analytics/` performs **no network I/O** — derives from the router/store only (dependency
  rule); the one exception, Layer 3 theme research (§5b), calls an LLM and a web-search gateway
  through injected collaborators, never a direct network call from this layer itself, and is
  operator-triggered rather than request-path.
- Sponsor-fed factors would be entitlement-masked when they exist (§2c — none of the five shipped
  factors carry a `sponsor` sub-signal today); the composite discloses partial factors via
  `ScoreResult.partial_factors` — real today.
- All real outputs (scores, leaderboard, tag-screener results, theme reads) pass
  `guardrails.check_output`: non-trading, caveated, sourced. No module output may become a
  buy/sell/target/sizing signal.
- The Layer-3 theme-research fabrication safeguard (§5b's `_filter_to_universe`) is itself a
  governance control, not just a data-quality one: it is what makes it safe to let an LLM propose
  theme constituents at all, and it runs on every code path including the verifier fail-open
  branch.
