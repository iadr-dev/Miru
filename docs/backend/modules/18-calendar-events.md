# Module: Calendar & upcoming events (`backend/src/calendar/`)

A forward-looking **market/stock/ETF events calendar** (除權息, 股東會, 法說會, 財報/月營收
deadlines, 期貨結算, index rebalances, macro releases, IPO/上市, 處置…). Mostly **assembled and
derived** from existing sources + a rule engine; a few forward-dated events would need new scrapers.

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

Real generators in `calendar/rules.py`, each pure/no-I/O:
- **期貨結算日** (`generate_derivatives_settlement_events`) = 3rd Wednesday/month (TX);
  **選擇權週結算** = every Wednesday (TXO). (No per-contract 最後交易日 override exists yet —
  every contract uses the same 3rd-Wednesday/every-Wednesday rule.)
- **財報 statutory deadlines** (`generate_financial_statement_deadline_event`) — Q1 5/15 · Q2
  **8/14 (金融/保險業 8/31)** · Q3 11/14 · 年報 3/31 (industry-aware Q2 split via
  `is_finance_or_insurance`).
- **月營收 deadline** (`generate_monthly_revenue_deadline_event`) = 10th of following month (a
  **window**, not a per-company scheduled day).
- **停止過戶起始日 / 融券強制回補最後日** (`generate_agm_derived_events`) = AGM − 60 days /
  停止過戶開始日 − 6 營業日 — derived from an already-known AGM date (a Layer-2 feed datum), not
  invented by this module.
- **Business-day math** runs off `calendar/trading_days.py::TradingDayCalendar`, which degrades
  gracefully: with no `known_trading_dates` list and no closure overlay supplied it falls back to
  plain Mon–Fri weekday math (`calendar_from_known_dates` builds one from ingested
  `TaiwanStockTradingDate` dates; the ingest daemon does this). `calendar/holidays.py` adds a
  rule-based TW/NYSE holiday overlay (`is_market_holiday`, `is_market_open_day`) for schedule
  `next_run` computation. No `除權息旺季` label generator exists.

> **Product correction:** 月營收 and 財報 have **no per-company pre-scheduled date** — only
> statutory deadlines. Render them as **deadline markers** (state `expected`) and flip to
> `released` when the FinMind row lands; surface early/late filing as a signal.

## Layer 2 — existing feeds (forward dates we already have)

| Event | Source (existing) |
|-------|-------------------|
| 除權息交易日 / 基準日 / 現金股利發放日 / 現增認購基準日 | FinMind `TaiwanStockDividend` (`Capability.DIVIDENDS`) and TWSE open data `TWT48U_ALL` via `sources/twse_openapi.py` |
| **ETF 配息** (季/月配 — 收益分配基準日/除息日/發放日; 0056/00878/00919/00929) | FinMind `TaiwanStockDividend` (ETFs) — the same `Capability.DIVIDENDS` row real code actually composes (`calendar/events.py::fetch_dividend_events`). No issuer-announcement scrape exists for ETF distribution freshness — `sources/etf_pcf.py` covers only the listed-ETF roster, not dividend dates. |
| 股東會 (AGM/EGM) | TWSE open data `t187ap41_L` via `sources/twse_openapi.py` (real; 電子投票窗口 not covered) |
| 法說會 | not built — would need MOPS `t100sb02_1` |
| 庫藏股買回期間 | not built — would need MOPS `t35sc09` |
| 處置期間 起訖 / 停資停券預告 | FinMind `TaiwanStockDispositionSecuritiesPeriod`, TPEX `tpex_margin_trading_term`, MOPS |
| 減資/分割/面額變更 恢復買賣日 | FinMind `CapitalReductionReferencePrice`/`SplitPrice`/`ParValueChange` |
| CB 賣回權/到期 | FinMind `TaiwanStockConvertibleBondPutProvision`/`Info` |
| 新股上市/興櫃/下市 | TWSE/TPEX listing + FinMind `TaiwanStockDelisting` |
| 期貨最終結算價 | FinMind `TaiwanFuturesFinalSettlementPrice` (on the rule-derived day) |

## Layer 3 — gap scrapers (new, forward-dated, high value)

Not wired into this module today (the `INDEX_REBALANCE` / `MACRO_RELEASE` event types exist but no adapter emits them); all scheduled ahead → ideal calendar content (`data_class: public`):
1. **Index rebalance dates (biggest gap):** **台灣指數公司 (TIP)** 定期審核 (0050/0056/00878/00919
   underlyings), **MSCI** review (Feb/May/Aug/Nov), **FTSE** review (Mar/Jun/Sep/Dec, 3rd Fri).
2. **Macro release calendar:** DGBAS (CPI/GDP), 財政部 (出口), 國發會 景氣信號 (~month-end), **央行
   理監事會議** (quarterly) — a once-a-year scrape of each agency's published schedule; plus
   external **FOMC/US CPI** (Fed/BLS calendar). We already have the *values* (FinMind macro /
   `TaiwanBusinessIndicator`) — this adds the forward *dates*. (A separate `get_macro_calendar` tool, backed by `sources/financecalendar.py`, already serves US/global macro releases outside this module.)
3. **IPO 承銷/抽籤 (subscription/lottery) dates** — TWSE/TPEX 承銷 announcement scrape.

These attach to `sources/` (scraped adapters, honest-staleness via M5Q); the calendar module
composes Layers 1–3 and never does I/O itself.

## Tools & skill

- `get_calendar_events` (`tools/calendar_tools.py`) — forward events for **one** stock/ETF/CB
  symbol over a bounded date range (`DateRangeInput`, the same per-symbol, no-full-market-scan
  input shape every capability-scoped tool uses — never a watchlist/whole-market batch call).
  Real output is exactly the feed-sourced half of `calendar/events.py::get_calendar_events`
  (除權息/ETF配息 + CB 賣回權/到期); it does not accept a type/state filter argument today —
  every composed event is returned and the caller filters client-side if needed.
- `get_dividend_schedule` (`tools/market/`) — per-stock/ETF 除權息 + 配息 timeline
  (wraps `Capability.DIVIDENDS` directly, `public`) — the #1 retail view.
- No calendar-specific skill exists in `backend/src/skills/built_ins/`.

## Governance
- `public` throughout; non-trading (an events list, not a signal). Dates from external
  scrapers (index/macro) carry a source + as-of caveat; rule-derived markers are labeled as
  deadlines, not confirmed dates. Reconciliation state is journaled.

## Implementation status vs. this spec (real code)

- **Layer 1 is TW-only in content, not just in the current build.** `calendar/rules.py`'s
  generators (`generate_financial_statement_deadline_event`, `generate_monthly_revenue_
  deadline_event`, `generate_derivatives_settlement_events`) take a `market: Market` parameter,
  but every date they compute (財報 5/15·8/14·11/14·3/31, 月營收 10th-of-following-month, TX 3rd-
  Wednesday settlement) is a hardcoded TW statutory/exchange rule applied unconditionally
  regardless of the `market` value passed in — there is no US/GB/JP/EU rule set. Passing
  `Market.US` produces TW-shaped deadlines labeled as that market's, not a `not_supported`
  result. Multi-market rule content (e.g. SEC 10-Q/10-K deadlines) does not exist and is not
  scoped by this doc's v1/Layer-3 plan.
- **Layer 2 is real for 除權息/ETF配息, CB 賣回權/到期, 股東會 and 現增認購.** `calendar/events.py`'s
  `get_calendar_events` composes, in one call: the `calendar_events` feed adapters
  (`sources/ncdr_closure.py` typhoon closures and `sources/twse_openapi.py` 股東會/除權息/現增認購,
  merged via `router.fetch_all` rather than first-healthy-wins), plus two capabilities fetched
  directly — `Capability.DIVIDENDS` (FinMind `TaiwanStockDividend`) mapped to
  `EventType.DIVIDEND_EX_RIGHTS`, and `Capability.CB_PUT_PROVISION` (FinMind
  `TaiwanStockConvertibleBondPutProvision`) mapped to `EventType.CB_PUT_OR_MATURITY`. A dividend
  event is `scheduled` while its ex-date is ahead of `as_of` and `released` once passed.
  Caller-supplied Layer-1 `rule_engine_events` are merged and range-filtered; the tool itself
  passes none. Exposed as the tool `get_calendar_events` (`tools/calendar_tools.py`; classed
  `sponsor` as a whole, since CB data is Sponsor-tier at the source while dividend data is
  `public`).
- **法說會 (investor conference) remains a disclosed gap.** MOPS's only adapter
  (`sources/mops.py`, 重大訊息) returns free-text subject/detail, not a structured
  company/date/event-type triple, so no reliable 法說會 schedule can be picked out of it without
  guessing. Index rebalances, TW macro-release dates, and IPO lottery dates (Layer 3) are
  unbuilt.

## `market_hours.py` — intraday session gate

`calendar/market_hours.py`'s `MarketHours.is_open(now)` is a separate, narrower primitive from
everything above — not a calendar-events concern, but the day-level `TradingDayCalendar` plus a
wall-clock session window (default: TWSE continuous auction, 09:00–13:30 Asia/Taipei), used by
the live-quote poller ([16](16-infrastructure.md) §2a) to decide whether a subscribed symbol is
worth polling right now. Pre-open call-auction and the 13:30 closing call are treated as closed.
Accepts any timezone-aware instant and converts to local time itself. This module has no
dependency on the forward-events composition in `calendar/events.py`.

## Next steps
Highest-demand missing pieces: 法說會 schedule, then Layer-3 **TIP index rebalance**, then
MSCI/FTSE and the TW macro release calendar.
