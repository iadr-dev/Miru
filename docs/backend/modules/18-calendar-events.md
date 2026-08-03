# Module: Calendar & upcoming events (`backend/src/calendar/`)

A forward-looking **market/stock/ETF events calendar** (除權息, 股東會, 法說會, 財報/月營收
deadlines, 期貨結算, index rebalances, macro releases, IPO/上市, 處置…). Mostly **assembled and
derived** from existing sources + a rule engine; a few forward-dated events need new scrapers.
See [../adr/0019](../adr/0019-calendar-events.md).

## Design principle — 3 layers + a state model

A calendar needs **forward-dated** events, but TW events split three ways by how their future
date is known:

| Layer | How the future date is known | Cost |
|-------|------------------------------|------|
| **1 — Rule engine** (`calendar/rules.py`) | deterministic from a rule → generate the whole forward year with **zero feeds** | free |
| **2 — Existing feeds** | the specific future date is **published ahead** (FinMind/MOPS/TPEX pre-announce) | already have |
| **3 — Gap scrapers** | scheduled far ahead but no feed in our stack yet (index rebalances, macro release calendar) | new, small |

**Event state model** — every event carries a state so rule-derived markers upgrade gracefully
and delays/early-releases become their own signal:

```
scheduled            # exact future date published (法說會, 股東會, 除權息預告, index rebalance)
 | expected(deadline)# only a statutory deadline/window known (財報, 月營收) — a marker, not a date
 └─► confirmed        # a feed confirmed the specific date
     └─► released     # the event happened / data landed (reconcile: on-time / late / early)
         └─► revised   # date changed after announcement
```
`CalendarEvent{id, stock_id|market, type, title_zh/en, date|window, state, source, data_class}`.

## Layer 1 — rule engine (no feeds)

Deterministic, holiday-aware (business-day math off `TaiwanStockTradingDate`):
- **期貨結算日** = 3rd Wednesday/month (TX); **選擇權週結算** = every Wednesday (TXO); 最後交易日
  = per-contract spec.
- **財報 statutory deadlines** — Q1 5/15 · Q2 **8/14 (金融/保險業 8/31)** · Q3 11/14 · 年報 3/31
  (industry-aware Q2 split).
- **月營收 deadline** = 10th of following month (a **window**, not a per-company scheduled day).
- **停止過戶起始日** = AGM − 60 days; **融券強制回補最後日** = 停止過戶開始日 − 6 營業日 (derived).
- **除權息旺季** label = Jul–Sep; holidays/休市 from `TaiwanStockTradingDate` + NCDR typhoon overlay.

> **Product correction:** 月營收 and 財報 have **no per-company pre-scheduled date** — only
> statutory deadlines. Render them as **deadline markers** (state `expected`) and flip to
> `released` when the FinMind row lands; surface early/late filing as a signal.

## Layer 2 — existing feeds (forward dates we already have)

| Event | Source (existing) |
|-------|-------------------|
| 除權息交易日 / 基準日 / 現金股利發放日 / 停止過戶 | FinMind `TaiwanStockDividend` (+ `tpex_exright_prepost` / TWSE 除權息預告; MOPS 董事會決議 = earliest trigger) |
| **ETF 配息** (季/月配 — 收益分配基準日/除息日/發放日; 0056/00878/00919/00929) | FinMind `TaiwanStockDividend` (ETFs) — **issuer announcement scraped for freshness** (FinMind lags) |
| 股東會 (AGM/EGM) + 開會通知 + 電子投票窗口 | MOPS 股東會 (e-voting window may need TDCC scrape) |
| 法說會 | MOPS `t100sb02_1` (schedule + decks) |
| 庫藏股買回期間 | MOPS `t35sc09` |
| 處置期間 起訖 / 停資停券預告 | FinMind `TaiwanStockDispositionSecuritiesPeriod`, TPEX `tpex_margin_trading_term`, MOPS |
| 減資/分割/面額變更 恢復買賣日 | FinMind `CapitalReductionReferencePrice`/`SplitPrice`/`ParValueChange` |
| CB 賣回權/到期 | FinMind `TaiwanStockConvertibleBondPutProvision`/`Info` |
| 新股上市/興櫃/下市 | TWSE/TPEX listing + FinMind `TaiwanStockDelisting` |
| 期貨最終結算價 | FinMind `TaiwanFuturesFinalSettlementPrice` (on the rule-derived day) |

## Layer 3 — gap scrapers (new, forward-dated, high value)

Not in the stack today; all scheduled ahead → ideal calendar content (`data_class: public`):
1. **Index rebalance dates (biggest gap):** **台灣指數公司 (TIP)** 定期審核 (0050/0056/00878/00919
   underlyings), **MSCI** review (Feb/May/Aug/Nov), **FTSE** review (Mar/Jun/Sep/Dec, 3rd Fri).
2. **Macro release calendar:** DGBAS (CPI/GDP), 財政部 (出口), 國發會 景氣信號 (~month-end), **央行
   理監事會議** (quarterly) — a once-a-year scrape of each agency's published schedule; plus
   external **FOMC/US CPI** (Fed/BLS calendar). We already have the *values* (FinMind macro /
   `TaiwanBusinessIndicator`) — this adds the forward *dates*.
3. **IPO 承銷/抽籤 (subscription/lottery) dates** — TWSE/TPEX 承銷 announcement scrape.

These attach to `sources/` (scraped adapters, honest-staleness via M5Q); the calendar module
composes Layers 1–3 and never does I/O itself.

## Tools & skill

- `get_calendar_events` — forward events for a stock/ETF/watchlist/market over a date range,
  filterable by type + state (`scheduled|expected|confirmed|released`).
- `get_dividend_schedule` — per-stock/ETF 除權息 + 配息 timeline (the #1 retail view).
- Skill `market-calendar` — "what's coming up for my watchlist / this week" brief.

## Governance
- `public` throughout; non-trading (an events list, not a signal). Dates from external
  scrapers (index/macro) carry a source + as-of caveat; rule-derived markers are labeled as
  deadlines, not confirmed dates. Reconciliation state is journaled.

## v1 scope (recommended)
Layer-1 rules + Layer-2 feeds cover ~80% with near-zero new work: **ETF 配息 schedule**,
除權息+發放日, 法說會, 股東會+融券回補日 (derived), 期貨/選擇權結算, 財報/月營收 deadline markers,
交易日曆/休市. Add Layer-3 **TIP index rebalance** next (highest demand), then MSCI/FTSE +
macro release calendar.
