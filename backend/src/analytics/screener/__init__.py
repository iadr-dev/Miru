"""Tag-based end-of-day stock screener: a tap-tile, named-boolean-condition screener for TW.

A user selects named tags (e.g. "外資連3買", "5日均線回升"), combines them AND/OR, and gets back
matching symbols. Every buildable tag resolves at end of day — there is no intraday tag. Matches
are served from a nightly pre-computed flag dataset (`batch.py`), not evaluated live per query,
which is what makes "tap tag -> instant results" fast.

Modules:
- `catalog.py` — the fixed, typed tag catalog (definitions + bilingual labels + categories).
- `events.py` — pure indicator-event detection (a cross/slope-flip/breakout on the last bar).
- `chip_streaks.py` — pure chip-flow streak/window predicates.
- `batch.py` — the nightly job: evaluate every symbol against every tag, write true flags.
- `store.py` — the query-time read seam over the flag partition (AND/OR match).
"""

from __future__ import annotations
