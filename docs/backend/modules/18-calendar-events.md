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
  gracefully: with no `known_trading_dates` list (no ingested `TaiwanStockTradingDate` cache
  exists yet) and no NCDR closure overlay wired in, it falls back to plain Mon–Fri weekday math
  — there is no live 交易日曆/休市 data behind these rules today, and no `除權息旺季`
  (Jul–Sep) label generator exists in the real code — that line in an earlier draft of this doc
  was aspirational, not built.

> **Product correction:** 月營收 and 財報 have **no per-company pre-scheduled date** — only
> statutory deadlines. Render them as **deadline markers** (state `expected`) and flip to
> `released` when the FinMind row lands; surface early/late filing as a signal.

## Layer 2 — existing feeds (forward dates we already have)

| Event | Source (existing) |
|-------|-------------------|
| 除權息交易日 / 基準日 / 現金股利發放日 / 停止過戶 | FinMind `TaiwanStockDividend` (+ `tpex_exright_prepost` / TWSE 除權息預告; MOPS 董事會決議 = earliest trigger) |
| **ETF 配息** (季/月配 — 收益分配基準日/除息日/發放日; 0056/00878/00919/00929) | FinMind `TaiwanStockDividend` (ETFs) — the same `Capability.DIVIDENDS` row real code actually composes (`calendar/events.py::_dividend_calendar_events`). No issuer-announcement scrape exists for ETF distribution freshness — `sources/etf_pcf.py` covers only the listed-ETF roster (issuer/listing date/tracked index), not dividend dates; "scraped for freshness" in an earlier draft of this row was aspirational, not built. |
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

- `get_calendar_events` (`tools/calendar_tools.py`) — forward events for **one** stock/ETF/CB
  symbol over a bounded date range (`DateRangeInput`, the same per-symbol, no-full-market-scan
  input shape every capability-scoped tool uses — never a watchlist/whole-market batch call).
  Real output is exactly the feed-sourced half of `calendar/events.py::get_calendar_events`
  (除權息/ETF配息 + CB 賣回權/到期); it does not accept a type/state filter argument today —
  every composed event is returned and the caller filters client-side if needed.
- `get_dividend_schedule` (`tools/market/`) — per-stock/ETF 除權息 + 配息 timeline
  (wraps `Capability.DIVIDENDS` directly, `public`) — the #1 retail view.
- **No `market-calendar` skill exists yet.** `backend/src/skills/built_ins/` has no calendar-
  themed skill today; a "what's coming up for my watchlist / this week" brief is aspirational,
  not built.

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
- **Layer 2 除權息/ETF配息 + CB 賣回權/到期 are now real.** `calendar/events.py`'s
  `get_calendar_events` composes, in one call: the `calendar_events`-registered feed adapter
  (still only `sources/ncdr_closure.py` for TW — typhoon closures, contributing no rows of this
  shape), plus two already-wired capabilities fetched directly — `Capability.DIVIDENDS`
  (FinMind `TaiwanStockDividend`, the same capability `get_dividend_schedule` uses) mapped to
  `EventType.DIVIDEND_EX_RIGHTS`, and `Capability.CB_PUT_PROVISION` (FinMind
  `TaiwanStockConvertibleBondPutProvision`, the same capability `get_convertible_bond_profile`
  uses) mapped to the newly added `EventType.CB_PUT_OR_MATURITY`. Each mapped event's state is
  `scheduled` while its feed-confirmed date is still ahead of the composition's `as_of` and
  `released` once that date has passed — never a raw field carried straight through. This is
  exposed as a real tool, `get_calendar_events` (`tools/calendar_tools.py`; classed `sponsor` as
  a whole per this registry's mixed-tier bundling convention, since CB data is Sponsor-tier at
  the source even though dividend data is `public`).
- **股東會 (AGM) and 法說會 (investor conference) remain an honest, disclosed gap.** MOPS's only
  real TW adapter (`sources/mops.py`'s `CORPORATE_ACTIONS` capability, 重大訊息 material
  announcements) returns free-text `subject`/`detail` fields, not a structured
  company/date/event-type triple — some announcements happen to describe an upcoming meeting
  or filing date in prose, but there is no reliable way to pick "this row is an AGM/法說會
  schedule" out of that stream without guessing. Wiring these two event types for real needs
  either a dedicated MOPS AGM/法說會-schedule endpoint (`t100sb02_1`) or a free-text
  classification step this composition deliberately does not attempt. Index rebalances, macro
  releases, and IPO lottery dates (Layer 3) remain entirely unbuilt, as before.
- **Layer 1's TW-only-in-content gap is unchanged** — `calendar/rules.py`'s generators still
  compute TW statutory/exchange rules unconditionally regardless of the `market` value passed
  in; no multi-market rule content exists.

## `market_hours.py` — intraday session gate (undocumented in earlier drafts, real code)

`calendar/market_hours.py`'s `MarketHours.is_open(now)` is a separate, narrower primitive from
everything above — not a calendar-events concern, but the day-level `TradingDayCalendar` plus a
wall-clock session window (default: TWSE continuous auction, 09:00–13:30 Asia/Taipei), used by
the live-quote poller ([16](16-infrastructure.md) §2a) to decide whether a subscribed symbol is
worth polling right now. Pre-open call-auction and the 13:30 closing call are treated as closed.
Accepts any timezone-aware instant and converts to local time itself. This module has no
dependency on the forward-events composition in `calendar/events.py`.

## v1 scope (recommended)
Layer-1 rules + Layer-2 feeds cover ~80% with near-zero new work: **ETF 配息 schedule**,
除權息+發放日, 法說會, 股東會+融券回補日 (derived), 期貨/選擇權結算, 財報/月營收 deadline markers,
交易日曆/休市. Add Layer-3 **TIP index rebalance** next (highest demand), then MSCI/FTSE +
macro release calendar.
