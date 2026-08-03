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

---

## 1. Technical indicators (`analytics/indicators/`)

A **pure indicator library** (`backend/src/analytics/indicators/`) — each indicator is a pure
function over price/volume arrays, no I/O, no LLM. A **decorator registry** (`@indicator(name)`)
collects them; `compute_indicators(ohlcv, [{name, params}]) -> {name: {series_key: [...]}}`.

| Group | Indicators | Notes |
|-------|-----------|-------|
| Moving average | `sma`, `ema`, `ma` (MA5/10/20/60 at once) | 均線排列 |
| Oscillators | `kd` (stochastic 9/3/3, TW-standard seeded at 50), `rsi` (Wilder 14) | |
| Trend | `macd` (12/26/9, EMA-of-DIF signal, dif/dem/histogram) | |
| Volatility | `bollinger` (20/2σ), `atr` | 布林通道 |
| Volume | `obv`, `vwap` | |
| Patterns | `candlestick` — 24 K-line patterns (doji/hammer/engulfing/harami/star/soldiers/…) | each bar → +1 看多 / −1 看空 / 0, plus a composite per-bar `signal` |

- All indicators run off a **back-adjusted price series** (還原股價). Our TW adjustment method:
  derive the split/dividend adjustment from the **±10% daily price limit** — an overnight gap larger
  than the limit implies a corporate action (there's no other legal way to exceed it), so it back-
  adjusts the pre-event series without a separate split feed. Where FinMind `TaiwanStockPriceAdj` is
  present, prefer it (module 22).
- Tool `get_indicators` returns **latest values + the full series** (for charting) for a chosen
  indicator set. Pure `public` compute.
- **Framing:** a KD golden cross or a bullish engulfing is reported as a **描述** of the chart
  ("KD 在低檔黃金交叉"), never "buy". The +1/−1 pattern signal is an *observation label*, not a
  position instruction (§7).

### 1a. Structure signals (量能 + 支撐/壓力 + N 波回檔)

Beyond the point-in-time oscillators, `analytics/indicators/structure.py` computes the
**chart-structure descriptions** a 技術面 read needs — all pure compute, all `public`, all
described (never a trade):

- **量能 (volume):** 均量 (5/20-day), **爆量** (volume ≫ moving-average volume) / **量縮**, OBV
  trend, VWAP vs price, 量價背離 (price up on shrinking volume) — surfaced as a *confirmation /
  divergence description*, feeding the technical factor's "volume confirm vs distribution" (§2).
- **支撐/壓力 (support/resistance):** swing-high/low pivots + recent congestion zones + round-number
  and prior-limit levels → a ranked list of nearby S/R with distance-to-price; plus **突破/跌破**
  detection reusing the **假突破/誘多 filter** (needs large body + volume spike + next-bar hold, §2)
  so a false break is labelled as such, not as a breakout.
- **N 波回檔 (impulse–pullback structure):** detect the pattern you described — a **strong impulse
  leg into a key level (突破帶量), then a pullback that holds** above a prior support / a rising MA /
  a Fibonacci-style retracement of the impulse. Reported as *"急拉觸及壓力後回檔，回測 X 未破 →
  型態尚存"* — a **structural observation**, explicitly **not** an entry/加碼 instruction (§7). The
  detector emits: impulse leg (start/end, %), the tested level, pullback depth, and a hold/break flag.

These structure signals are inputs to the technical factor (§2) and the `technical-read` skill
([08](08-skills.md)); the tool `get_indicators` accepts `set=...,volume,sr,wave` to include them.

## 2. Multi-factor 多空 score (`analytics/scoring/`)

The bull/bear engine — **pure computation, no LLM** — updated daily post-close by the ingest
layer (module 22) and cached in the derived store. Designed the way a professional would (see
[§2b](#2b-composite-blend--horizon-aware)): a **lean, low-correlation
factor set**, each converted to a **cross-sectional percentile (0–100)** across the universe
(winsorized ±3σ, so a fat-tailed TW small-cap can't dominate), then a weighted blend. **Not**
15 factors — 5, because added factors are correlated noise that overfit and can't be explained
to the user (violating our §7 transparency rule).

### 2a. The five factors

| Factor | `data_class` | Proxy (codeable) | Why it's in the lean set |
|--------|-------------|------------------|--------------------------|
| **Momentum / RS (相對強度)** | public | IBD-style weighted return `0.4·ret_3m + 0.2·ret_6m + 0.2·ret_9m + 0.2·ret_12m`, **skip last 5d** (short-term reversal guard) → percentile | The single most robust cross-sectional premium; the backbone of O'Neil/Minervini/Weinstein |
| **Trend quality (技術)** | public | count of **Minervini trend-template** conditions met (0–8, [§5](#5-ai-filtered-top-n-screener-analyticsscreener)) → 0–100; encodes Weinstein Stage-2 | Turns "is it in an uptrend" into a graded number, not a vibe |
| **Value guard (估值)** | public | Earnings Yield (EBIT/EV) or FCF yield → percentile | **The anti-追高 brake** — stops pure momentum ranking crowded 概念股 at the top |
| **Quality (品質)** | public | ROE + gross-profitability (毛利/資產) + **Piotroski F-Score** → percentile | Filters "empty rockets" (RS 99 on a pump, no earnings) |
| **Revenue / flow (籌碼, TW-specific)** | **mixed** | **月營收 YoY** percentile + **投信/外資 net-buy 20d trend** percentile | TW is flow-driven; a TW score that ignores 籌碼 feels wrong to local users. 分點-derived sub-signals are `sponsor`; 月營收 + 三大法人 are `public` |

### 2b. Composite blend — horizon-aware

Two weight profiles (config `SCORE_WEIGHTS`), because a swing trader and a position holder
weight the *same factors* differently. Default = swing.

```
composite = Σ (weight_i · percentile_i)          # 0–100
```

| Factor | **Swing / Momentum** (default) | **Position / Quality** |
|--------|-------------------------------|------------------------|
| Momentum / RS | **0.35** | 0.15 |
| Trend quality | **0.25** | 0.15 |
| Value guard | 0.10 | **0.25** |
| Quality | 0.10 | **0.30** |
| Revenue / flow (TW) | **0.20** | 0.15 |

> **Why this blend (and why we rejected a theme-heavy one).** A naive/common blend —
> `chip .35 / technical .30 / theme .20 / fundamental .15` — a real trader would reject two
> things: (1) **`theme .20` is 追高 fuel** — 20% of the score chasing whichever narrative is hot
> surfaces stocks *after* the move. Theme is now **demoted to a tag, not a scored factor** (it
> still tags rows and drives discovery in [§5C], but doesn't lift the score). (2) There was **no
> valuation/quality guard**, so a thin pump could score high. RS/momentum now carries the
> up-trend signal (measurable, no narrative-guessing), and value+quality are explicit brakes.
> **籌碼 stays high** (0.20, top-3 weight) because it genuinely leads price in Taiwan — the
> instinct to weight it was right; it just can't be the *only* thing.

- **±5 analyst overlay** ([§4](#4-analyst-overlay-yfinance-attributed)) nudges the composite;
  omitted (not synthesized) when `numberOfAnalystOpinions == 0`.
- **Over-extension penalty:** a stock **> ~20–25% above its 50-day MA** is flagged
  "乖離過大, 回檔風險" and the momentum contribution is *capped* — a naive momentum score ranks the
  most stretched names top, right before mean-reversion.
- **Regime overlay (O'Neil "M"):** a market flag (TAIEX vs its 200-day MA / breadth) does **not**
  change the score — it's **surfaced as context** ("computed during a market downtrend; momentum
  factors carry elevated reversal risk"). Keeps us signal-free while warning honestly.
- **Percentile stability:** after gating the survivor set can be small and percentiles jumpy →
  factor percentiles are computed against the **full pre-gate universe** (fixed reference), then
  gates apply as a display filter.

### 2c. Mechanics

- **Entitlement handling:** the flow factor's `sponsor` sub-signals (分點-based) are masked for
  Free users; the composite is then computed over the **available** factors and the output
  **states which factor was partial** — never silently degraded.
- **Cache-first + scheduled:** `resolve_score(symbol, profile)` reads the cached `stock_scores`
  row (stale after 1 trading day → recompute on read). The batch `compute_daily_scores` runs
  **daily post-close** as the **`compute-scores` job** ([module 22](22-data-ingest.md) §Derived-
  compute), chained *after* `ingest-daily-late` so it never scores partial data; it loads all
  factor tables in a few queries, winsorizes, percentiles, and blends both profiles. Tools:
  `get_score`, `get_score_breakdown` (per-factor percentile + contribution + the 描述 behind each).
- **Per-asset-kind** (`scoring/models.py`): the blend renormalizes over *available* factors per
  kind — **tw-stock** (above), **tw-etf** (drops single-stock chip/quality; adds NAV/折溢價/流向),
  and US variants for the multi-market future (module 19). `detect_kind` from symbol shape.
- **News sub-signal** (`scoring/news_sentiment.py`): an **auditable TW-finance lexicon** →
  a **divergence caveat** (hot news but weak chip/fundamentals ⇒ 追高風險); it drives the *caveat*,
  **not** a scored factor (avoids double-counting narrative with momentum) — never claims manipulation.
- **Label mapping:** composite → **偏多 / 中性 / 偏空** descriptive band (with the numeric score,
  the horizon profile used, and the factor breakdown). This is the "多空" — presented as
  *analytical context with caveats*, not a signal (§7).

### 2d. Where a naive score misleads (coded guardrails)

The research flagged the classic traps; each is a coded rule, not a hope:
- **追高 / theme-chasing** → the value guard + over-extension penalty (above).
- **Momentum with no quality/value floor** → never ship a 1-factor momentum score; quality + value
  + 月營收 sleeves must be present.
- **Liquidity ignored** → liquidity is a **gate** ([§5A](#5-ai-filtered-top-n-screener-analyticsscreener)),
  never a weighted factor — a gate can't be "bought back" by a great score, a weight can.
- **投信作帳 window-dressing** → quarter-end (Mar/Jun/Sep/Dec) flow spikes are flagged
  lower-confidence (they reverse hard).
- **除權息 distortion** → all technical/momentum factors use **adjusted prices (還原股價)** (§1).
- **Look-ahead bias** → 月營收/財報 factors only use data **after its disclosure date** (module 22
  models the release date), so a score is never computed from figures that weren't yet public.

## 3. AI summary + SWOT (`analytics/summary/`)

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

## 4. Analyst overlay (yfinance, attributed)

The composite adds a small **±5 analyst-recommendation bonus** from `get_analyst_consensus`
(module 03, yfinance, large-cap). This is surfaced strictly as an **attributed third-party
estimate** ("N analysts, 目標價 X, as of <date>") and the guardrail blocks restating a target
or rating as *our* view (§7). `null` when `numberOfAnalystOpinions == 0` → the overlay is
simply omitted (no synthetic value).

## 5. AI-filtered top-N screener (`analytics/screener/`)

**A real trader screens as a funnel — eliminate first, score last.** Nobody scores 2000 stocks
and ranks; they kill ~70–85% with binary gates, apply a setup filter, then score only the
survivors. Our screener follows that funnel. "AI-filtered" is mostly **rule-scored SQL**, not
LLM (the LLM only proposes themes). All read the ingest store (module 22); short cache keyed on
the latest trading date.

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

**Screener contracts (support the pro condition builder — [web screener/README.md](../../web/screener/README.md) §4):**

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

**C+. LLM theme discovery** (`analytics/themes/`) — the only genuinely LLM-driven part.
`discover_themes(days)` gathers signals (recent news headlines + figure events + hot
high-volume stocks), prompts the LLM (JSON mode, low temp) to propose **emerging themes +
member stocks + 0–1 confidence**; accepted if ≥4 members and confidence ≥0.6, deduped against
existing themes, persisted with `source='llm_discovery'` provenance + a change log. Runs as a
scheduled ingest pass (module 22, `ingest-event`, ~7-day window). Related: `get_theme_flow`
(三大法人 net flow per theme, 資金流向) and `figure_events` (named-figure news scan, 黃仁勳/馬斯克…).

- **Screener governance:** a screen result is a **filtered list matching described criteria**,
  presented as such ("符合『外資連 5 買』的個股"), never "these are the stocks to buy". The top-N
  is ranked by a *descriptive composite*, with the non-trading caveat on the list (§7).
- **Entitlement:** the rule screener + top-N ranking are **public** where every factor is
  public; a screen whose criteria require `sponsor` factors (分點-based) is Premium+ (or masks
  the sponsor column). Theme discovery is an AI feature → Premium+.

## 6. Tools & endpoints

Tools registered in module 03 (deferred set): `get_indicators`, `get_score`,
`get_score_breakdown`, `get_ai_summary`, `get_swot`, `run_screener`, `get_factor_catalog`,
`get_screener_presets`, `get_top_stocks`, `get_theme_flow`, `get_themes`, `get_figure_events`.

REST (see [api-reference.md](../api-reference.md) §4a):
```
GET /analytics/{symbol}/indicators?set=ma,kd,macd,rsi,bollinger,volume,sr,wave
GET /analytics/{symbol}/score?profile=swing|position   # composite + 偏多/中性/偏空 + factor breakdown + regime caveat
GET /analytics/{symbol}/summary          # AI summary + SWOT (Premium+)
GET  /analytics/screener/factors?market=TW|US|ETF   # bilingual catalog + per-market fields[] descriptors + presets
POST /analytics/screener/run             # {market, preset|screen_type|conditions[], match, gate_overrides?, count_only?} — funnel: gates→setup
GET  /analytics/rankings?profile=swing&sort=composite&n=20&screen=  # AI top-N (gated→setup→scored)
GET  /analytics/themes                   # discovered themes + flow (tag, not scored)
# saved / shared screens (per-user, non-trading list definitions)
GET    /analytics/screener/screens       # list this user's saved screens (scoped per market)
POST   /analytics/screener/screens       # create {name, market, conditions, match, gate_overrides}
PATCH  /analytics/screener/screens/{id}  # rename / update conditions
DELETE /analytics/screener/screens/{id}  # remove
```
Skills: `technical-read`, `multi-factor-score`, `ai-summary-swot`, `stock-screener`,
`theme-discovery` ([modules/08](08-skills.md)).

## 7. Non-trading framing (the hard line)

Scores, labels, SWOT, and screens are the product's closest approach to a signal, so the
guardrail is strictest here. Enforced in `guardrails.check_output` + this module's own contract:

- **Descriptive, not prescriptive.** 偏多/中性/偏空 describes *what the factors currently show*,
  not what to do. Never "買/賣/加碼/減碼/進場/停損", never a price target of our own, never
  position sizing. A screen is "符合條件的清單", not "推薦名單".
- **Show the工作.** Every score ships its **factor breakdown + the underlying 描述** so it reads
  as transparent analysis, not an oracle. The number without its basis is never surfaced.
- **Attributed externals.** Analyst targets/ratings (§4) are always "N analysts, as of <date>",
  never restated as ours.
- **Caveated + current.** Every output carries the currentness caveat (score computed at last
  post-close) and the "context-only, not advice" footer.
- **Divergence honesty.** The news factor's 追高風險 caveat and the 假突破 filter exist to *warn*,
  not to hype — the design bias is toward disclosing weakness, never manufacturing conviction.

## Governance obligations
- `analytics/` performs **no network I/O** — derives from the router/store only (dependency
  rule). Sponsor-fed factors are entitlement-masked; the composite discloses partial factors.
- All outputs (scores, summaries, SWOT, screens, rankings) pass `guardrails.check_output`:
  non-trading, caveated, sourced. No module output may become a buy/sell/target/sizing signal.
- LLM theme discovery persists provenance (`source='llm_discovery'` + confidence + change log);
  it proposes *descriptive themes*, never trades.
