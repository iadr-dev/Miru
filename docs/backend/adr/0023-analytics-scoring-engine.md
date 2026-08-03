# ADR 0023 — Derived analytics: indicators, multi-factor 多空 score, AI summary/SWOT, screener

## Status
Accepted. Adds [modules/20](../modules/20-analytics-scoring.md); registers tools in
[modules/03](../modules/03-tools.md) + skills in [modules/08](../modules/08-skills.md); reads
the ingest store ([modules/22](../modules/22-data-ingest.md)); hardens governance §1.1 framing.

## Context
The stock-detail page and agent need higher-order analytics the raw governed data doesn't
provide directly: technical indicators, a bull/bear (多空) read, an AI summary + SWOT
(Screenshot_3 reference), and an AI-filtered top-N screener. The Kestrel backend already
implements all of this, TW-tuned (`formulas/`, `ai_scoring.py`, `scoring/`, `screener_service.py`,
`theme_discovery.py`). The risk: a "score" and a "多空 label" are the closest this product comes
to a trading signal, colliding with the non-trading prime directive (§1.1).

## Decision
Add a **derived-compute module** (`analytics/`), adapted from Kestrel, sitting beside `context/`
(no network I/O — reads the router/store only):
- **Indicators** — a pure formula registry (MA/KD/MACD/RSI/Bollinger/ATR/OBV/VWAP + 24 K-line
  patterns + 量能/支撐壓力/N波回檔 structure signals), on a **back-adjusted price series** (TW
  ±10%-limit split-detection trick).
- **多空 score** — a **lean, horizon-aware 5-factor** engine (momentum/RS · trend-quality ·
  value-guard · quality · 籌碼-flow), each a **cross-sectional percentile** (winsorized ±3σ),
  blended with **swing vs position** weight profiles (+ ±5 attributed analyst overlay,
  over-extension penalty, market-regime caveat), mapped to a **偏多/中性/偏空 band** with the full
  factor breakdown. Computed daily post-close. Grounded in how pros actually score (O'Neil RS /
  Minervini / Piotroski / factor consensus): **theme demoted from a scored factor to a tag** (it
  was 追高 fuel), and **value + quality added as explicit guards** against ranking empty rockets.
- **AI summary + SWOT** — an LLM *summarizes the deterministic factors* (never invents numbers)
  into a caveated card; weekly batch + on-demand; cheap-tier model.
- **Screener = a funnel, not a ranker of the whole universe** — **hard gates** (liquidity/price/
  市值/處置-注意-全額交割) → **setup** (rule screens + named presets: Minervini trend template,
  CANSLIM, Weinstein Stage-2, Piotroski F≥7, Magic Formula, TW favorites) → **score & rank the
  survivors** (AI top-N). Liquidity is a *gate*, never a weighted factor. **LLM theme discovery**
  is the only LLM-driven part (proposes descriptive themes with confidence + provenance).

**Non-trading is enforced hardest here (module 20 §7):** descriptive not prescriptive (no
buy/sell/target/sizing); every score ships its factor breakdown + underlying 描述; externals
attributed; caveated + current; divergence disclosed (追高風險 caveat, 假突破 filter). All outputs
pass `guardrails.check_output`.

**Entitlement:** `public` where all factors are public; a `sponsor`-fed factor (分點-based chip)
is masked and the composite is computed over available factors with the gap disclosed. AI
features (summary/SWOT, theme discovery) are Premium+ (tier axis 2), **not** a new data class.

## Consequences
- Delivers the four stock-detail features on the existing governed data + ingest store; no new
  source adapter.
- The strongest signal-adjacent surface in the product is now explicitly bounded by the
  non-trading guardrail and a transparency requirement (show the factor breakdown).
- Scores/summaries are cached derived artifacts (ingest batch), so serving them is cheap and
  the currentness caveat is preserved through the cache tier.
- Cosmetic-only pets ([modules/21](../modules/21-pet-gamification.md)) are deliberately kept out
  of this path — no pet can influence a score or summary.
