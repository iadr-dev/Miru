"""Orchestration: classify a turn's intent, route it to one of four execution frameworks
(`none`/`single`/`subagent`/`team`), and synthesize the result under per-turn governance
ceilings.

Public surface: `Orchestrator.run` is the per-turn entrypoint; the classifier, synthesizer,
ceilings, and contracts are exposed for wiring/testing at the application-composition boundary.
"""

from __future__ import annotations

from orchestrator.ceilings import (
    NoQuotaReserver,
    QuotaReservation,
    QuotaReserver,
    TurnBudget,
    TurnCeilingExceededError,
)
from orchestrator.classifier import IntentClassifier
from orchestrator.models import (
    Finding,
    Framework,
    LaneResult,
    OrchestratorResult,
    Routing,
    SynthesisProvenance,
)
from orchestrator.orchestrator import Orchestrator, PersonalityContext
from orchestrator.synthesis import NoSuccessfulLanesError, Synthesizer

__all__ = [
    "Finding",
    "Framework",
    "IntentClassifier",
    "LaneResult",
    "NoQuotaReserver",
    "NoSuccessfulLanesError",
    "Orchestrator",
    "OrchestratorResult",
    "PersonalityContext",
    "QuotaReservation",
    "QuotaReserver",
    "Routing",
    "SynthesisProvenance",
    "Synthesizer",
    "TurnBudget",
    "TurnCeilingExceededError",
]
