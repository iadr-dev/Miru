"""Deep research: a budgeted, cited, multi-step web investigation (Pro).

See `research.controller` for the loop and the rules it enforces.
"""

from __future__ import annotations

from research.controller import DeepResearch, Progress, ProgressFn
from research.models import FinalReport, ResearchBudget, ResearchFailedError
from research.ports import FetchedPage, ResearchModel, SearchHit, WebPort

__all__ = [
    "DeepResearch",
    "FetchedPage",
    "FinalReport",
    "Progress",
    "ProgressFn",
    "ResearchBudget",
    "ResearchFailedError",
    "ResearchModel",
    "SearchHit",
    "WebPort",
]
