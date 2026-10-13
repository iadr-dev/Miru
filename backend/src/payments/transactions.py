"""What the billing-history list shows of a Paddle transaction.

Paddle returns a large object per transaction (customer, addresses, line items, payment details).
The billing page needs five facts, so only those are sent to the browser — typed, so the frontend
checks them at compile time instead of digging through an untyped blob, and nothing else about the
customer leaves the server.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict


class BillingTransaction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: str  # Paddle's: draft | ready | billed | paid | completed | canceled | past_due | ...
    created_at: str | None
    billed_at: str | None
    currency_code: str | None
    total: str | None  # the grand total in the currency's minor units, as Paddle states it


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast("Mapping[str, object]", value)
    return {}


def project_transaction(raw: Mapping[str, object]) -> BillingTransaction | None:
    """The displayable facts of one Paddle transaction, or None when it lacks an id or a status
    (such a row could be neither shown nor looked up for a receipt)."""
    transaction_id = _text(raw.get("id"))
    status = _text(raw.get("status"))
    if transaction_id is None or status is None:
        return None
    totals = _mapping(_mapping(raw.get("details")).get("totals"))
    return BillingTransaction(
        id=transaction_id,
        status=status,
        created_at=_text(raw.get("created_at")),
        billed_at=_text(raw.get("billed_at")),
        currency_code=_text(raw.get("currency_code")) or _text(totals.get("currency_code")),
        total=_text(totals.get("grand_total")) or _text(totals.get("total")),
    )


__all__ = ["BillingTransaction", "project_transaction"]
