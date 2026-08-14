"""Chat-attachment handling: image passthrough (to `llm.base.ImagePart`) and
Office/PDF/etc. text extraction via `markitdown` (`extractor.py`).

Leaf-ish module: no dependency on `conversation/`, `orchestrator/`, or `api/` — those layers
import from here, never the reverse, per the project's one-way dependency rule.
"""

from __future__ import annotations

__all__: list[str] = []
