# ADR 0019 — Market/stock calendar as a 3-layer derived module with an event state model

## Status
Accepted. Adds [modules/18](../modules/18-calendar-events.md); a `calendar_ext` source adapter
(14th) for forward-dated gaps.

## Context
A forward-looking events calendar (除權息/配息, 股東會, 法說會, 財報/月營收, 期貨結算, index
rebalances, macro releases, IPO/上市, 處置) is high retail value. Research showed TW events split
by *how their future date is knowable*: some are deterministic rules, some are published ahead
by feeds we already have, and a few (index rebalances, macro release dates, IPO lottery) have no
feed. Crucially, **月營收/財報 have no per-company scheduled date — only statutory deadlines** — so
a naive "expected report date" would be wrong.

## Decision
1. **3-layer assembly:** (1) a **rule engine** generates deterministic events with zero feeds
   (settlement=3rd Wed, filing deadlines incl. 金融業 Q2 8/31, 月營收 by-10th window, 融券回補
   =停止過戶−6營業日, holidays); (2) **existing feeds** supply published forward dates (FinMind
   dividend/CB/disposition, MOPS 法說/股東會/庫藏股, TPEX/TWSE pre-announce); (3) **new scrapers**
   (`calendar_ext`) close the gaps: TIP/MSCI/FTSE **index rebalance**, **macro release calendar**,
   IPO 承銷/抽籤.
2. **Event state model:** `scheduled → expected(deadline) → confirmed → released → revised`, so
   rule-derived deadline markers upgrade when a real feed lands and late/early filings surface as
   signals. 月營收/財報 render as deadline markers, never fake per-company dates.
3. **Calendar module composes; adapters do I/O** — `calendar/` performs no network I/O itself
   (ADR-0002 holds); scrapers live in `sources/calendar_ext.py`.

## Consequences
- ~80% of the calendar ships from Layers 1–2 with near-zero new work; Layer-3 scrapers are the
  incremental cost, prioritized TIP → MSCI/FTSE → macro.
- 14th source adapter; `data_class: public`, non-trading (an events list, not a signal); scraped
  dates carry source + as-of caveats, reconciliation state journaled.
- Tools `get_calendar_events`/`get_dividend_schedule`; skill `market-calendar`.
