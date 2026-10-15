"""策略盯盤 (strategy watch): the 08:00 screen that picks today's watchlist with each stock's
trigger levels, and the intraday check that compares a live snapshot against those levels.

Every strategy is a set of conditions the user subscribes to, stated as facts ("價格高於上軌",
"量比 ≥ 1.5"). Nothing here ranks stocks as buys or sells: an alert says a set of conditions the
user chose was met, never what to do about it (non-trading rule,
docs/backend/modules/25-governance-enforcement.md).
"""
