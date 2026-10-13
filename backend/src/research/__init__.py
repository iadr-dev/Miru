"""Deep research: a budgeted, cited, multi-step investigation of the web and Miru data (Pro).

See `research.controller` for the loop and the rules it enforces.
"""

from __future__ import annotations

from research.controller import DeepResearch, Progress, ProgressFn
from research.models import FinalReport, ResearchBudget, ResearchFailedError
from research.ports import DataPort, FetchedPage, ResearchModel, SearchHit, WebPort

__all__ = [
    "DataPort",
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
