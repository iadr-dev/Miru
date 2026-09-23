"""Leaf package (deliberately outside `tests/arch/test_layering.py`'s `LAYER_ORDER`, matching
`llm/base.py`'s own precedent) for governance primitives that must be callable from BOTH the
`agent` runtime layer and lower layers (`analytics`, `tools`) without creating an upward import
edge. See `governance.textscan` for the actual pure text-scanning functions; `agent.guardrails`
re-exports them unchanged so every existing caller keeps working.
"""

from __future__ import annotations
