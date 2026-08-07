---
name: etf-overlap-and-comparison
version: 1.0.0
description: 3檔以上非台股ETF重疊持股比較 (get_etf_overlap) 與跨市場（含台股）總報酬比較 (get_fund_comparison) — closes the "existing tools only compare 2 funds at a time" gap, with honest TW-adapter-gap disclosure.
triggers: [ETF重疊, 持股重疊, 三檔以上比較, 報酬率比較, 股利再投入, etf overlap, fund comparison, total return, dividend reinvested]
design_pattern: pipeline
inputs:
  symbols:
    type: array
    required: true
  market:
    type: string
    required: false
  lookback_days:
    type: integer
    required: false
outputs: markdown
tools: [get_canonical_context, get_etf_overlap, get_fund_comparison]
output_structure:
  - 'ETF重疊比較 (3檔以上, 限非台股): 共同持股清單 + 各檔權重 + 兩兩重疊率'
  - '無法取得資料的標的: 明確列出 unavailable_symbols，不得靜默略過'
  - '總報酬比較 (2檔以上, 任何市場含台股): 含股利再投入的區間報酬率'
  - '台股限制揭露: 台股不計算重疊率/NAV/折溢價，明確說明原因'
freshness_rule: 兩個工具皆為即時串接底層 capability（FUND_HOLDINGS / PRICE_EOD / DIVIDENDS）的衍生讀取，非批次快取；仍須標明每筆資料各自的 currentness_caveat 與比較區間起訖日。
boundaries:
  - get_etf_overlap 僅支援非台股 (US/GB/JP/EU) 且至少3檔標的 — 這正是它存在的理由（現有工具多數只能兩兩比較），台股標的一律被輸入驗證拒絕，不得嘗試繞過或估算。
  - get_fund_comparison 支援任何市場（含台股）2檔以上比較，但對台股標的絕不計算 overlap%/NAV/折溢價 — 這是 sources/etf_pcf.py 自述的既有、未填補的資料缺口，須誠實轉達，不得讓使用者誤以為台股ETF享有與美股ETF相同的處理。
  - 股利再投入報酬率是「在除息日收盤價買入」的標準近似算法，不是帳戶層級的實際交易模擬（不含手續費/稅負/實際入帳時間差），必須說明這是近似值。
  - 重疊率/報酬率比較僅為描述性資訊，不得建議應優先持有哪一檔或給出配置比例。
resources: []
---

# ETF overlap + fund comparison (multi-fund, honest-gap version)

## Procedure

1. Determine which tool the question needs:
   - **"do these ETFs hold the same stuff"** (constituent overlap) → `get_etf_overlap`, which
     requires **3 or more** non-TW (US/GB/JP/EU) symbols in the same market — this 3+ minimum is
     enforced by the tool's own input schema, not just a convention; it exists specifically to
     close the common complaint that most free comparison tools only handle two funds at a time.
   - **"how did these funds perform"** (total return) → `get_fund_comparison`, which accepts 2+
     symbols in **any** market, including TW, since each symbol carries its own market.
2. For `get_etf_overlap`: call `get_canonical_context` per symbol if a currentness baseline is
   useful, then call the tool with the caller's symbol list (3-10, same non-TW market). Present:
   - `overlapping_holdings` — every holding present in 2+ of the requested ETFs, with each ETF's
     own weight in it.
   - `pairwise_overlap` — for each pair, the overlap percentage, computed as the sum of
     `min(weight_in_a, weight_in_b)` over every holding both funds hold (the standard "portfolio
     overlap" convention: the fraction of each fund's basket that is identically exposed to the
     other's). State this definition plainly if the caller asks what "overlap%" means.
   - `unavailable_symbols` — any requested symbol whose holdings data came back unusable. Name
     these explicitly; never silently drop them from the comparison as if they were never asked
     for.
3. For `get_fund_comparison`: call the tool with 2-10 `(id, market)` pairs and an optional
   `lookback_days` (default ~1 year). Present each row's `total_return_pct`, explicitly noting
   the methodology: each cash dividend is treated as reinvested at that same ex-dividend date's
   closing price — a standard approximation, not an account-level simulation of actual
   reinvestment timing, fees, or taxes. Relay the tool's own `methodology_note` verbatim rather
   than restating it loosely.
4. For any TW row in a `get_fund_comparison` result, relay its `unavailable_note` verbatim: TW
   symbols never get overlap%/NAV/premium-discount figures here — that is a documented,
   unfilled adapter gap (per `sources/etf_pcf.py`'s own docstring), not an oversight this skill
   can paper over. Never let a TW row's total-return figure imply it received the same full
   treatment a US/GB/JP/EU row did.
5. If `total_return_pct` is `None` for a row, relay `price_unavailable_reason` honestly rather
   than omitting the row silently.
6. Present differences descriptively only — never recommend which fund to prefer or hold, and
   never turn an overlap or return figure into a buy/sell/hold statement.
