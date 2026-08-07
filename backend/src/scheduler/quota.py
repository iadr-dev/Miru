"""The scheduler's quota gate — the seam a scheduled run checks before consuming the owner's
tier quota.

A job that would exceed the owner's quota is **skipped and reported**, never retried in a tight
loop. Two implementations sit behind the `QuotaGate` seam:

- `AllowAllQuotaGate` / `DenyAllQuotaGate` — the permissive default and its test double.
- `UsageQuotaGate` — the real gate. A scheduled run counts against the owner's daily-chat
  allowance exactly like an interactive turn, so this reserves one chat against the same durable
  `usage_counters` day-counter the interactive path uses (reserve-before-act: an atomic
  increment-and-check, refunded when over the cap), keyed off the owner's tier limit. `None`
  limit (Pro / unlimited) is never gated. Because it reserves the slot the run will spend, there
  is no TOCTOU window between "has headroom?" and "the run consumes a chat" — the same atomic
  claim is both.

The gate is swapped in at the worker's construction site, never in the loop body.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from accounts.entitlements import TIER_LIMITS
from schemas.enums import Tier
from usage.store import UsageStore


@runtime_checkable
class QuotaGate(Protocol):
    async def allows(self, *, user_id: str, tier: Tier) -> bool:
        """True if the owner has quota headroom for one more scheduled run right now. Never
        blocks; a `False` result means skip-and-report, never retry."""
        ...


class AllowAllQuotaGate:
    """Permissive default until the real usage-layer quota manager is wired."""

    async def allows(self, *, user_id: str, tier: Tier) -> bool:
        del user_id, tier
        return True


class DenyAllQuotaGate:
    """Test double: always reports no headroom, so a test can assert the skip-and-report path."""

    async def allows(self, *, user_id: str, tier: Tier) -> bool:
        del user_id, tier
        return False


class UsageQuotaGate:
    """The real gate: reserves one chat against the owner's durable daily-chat counter, keyed off
    the tier's `chat_limit_per_day`. A run that would exceed the cap is refused (and its claim
    refunded) so the owner's remaining allowance is untouched; an unlimited tier is never gated."""

    def __init__(self, usage_store: UsageStore) -> None:
        self._usage = usage_store

    async def allows(self, *, user_id: str, tier: Tier) -> bool:
        limit = TIER_LIMITS[tier].chat_limit_per_day
        if limit is None:
            return True
        verdict = await self._usage.reserve_daily_chat(
            user_id=user_id, limit=limit, today=datetime.now(UTC).date()
        )
        return verdict.allowed


__all__ = ["AllowAllQuotaGate", "DenyAllQuotaGate", "QuotaGate", "UsageQuotaGate"]
