"""The agent runtime kernel: the Step loop, planner, context-window compaction, the mandatory
governance hooks, and the audit journal.

Sits directly above `tools/` and `llm/` in the dependency order and directly below the
interaction layer (not yet built) — it never imports `context/`/`dataaccess/`/`sources/`
directly, only through `tools/`.
"""

from __future__ import annotations
