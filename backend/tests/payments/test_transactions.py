"""The billing-history projection: only what the billing page shows leaves the server."""

from __future__ import annotations

from payments.transactions import BillingTransaction, project_transaction


def test_a_paddle_transaction_is_reduced_to_the_displayed_facts() -> None:
    row = project_transaction(
        {
            "id": "txn_1",
            "status": "completed",
            "currency_code": "USD",
            "created_at": "2026-10-04T10:00:00Z",
            "billed_at": "2026-10-05T12:00:00Z",
            "customer_id": "ctm_secret",
            "address_id": "add_secret",
            "details": {"totals": {"grand_total": "1099", "total": "999", "currency_code": "EUR"}},
            "items": [{"price": {"id": "pri_1"}}],
        }
    )
    assert row == BillingTransaction(
        id="txn_1",
        status="completed",
        created_at="2026-10-04T10:00:00Z",
        billed_at="2026-10-05T12:00:00Z",
        currency_code="USD",
        total="1099",
    )


def test_the_total_falls_back_and_the_currency_comes_from_the_totals_when_absent() -> None:
    row = project_transaction(
        {
            "id": "txn_2",
            "status": "paid",
            "details": {"totals": {"total": "500", "currency_code": "TWD"}},
        }
    )
    assert row is not None
    assert (row.total, row.currency_code) == ("500", "TWD")


def test_a_sparse_transaction_keeps_its_id_and_status_and_nulls_the_rest() -> None:
    row = project_transaction({"id": "txn_3", "status": "billed", "details": "not-a-mapping"})
    assert row == BillingTransaction(
        id="txn_3",
        status="billed",
        created_at=None,
        billed_at=None,
        currency_code=None,
        total=None,
    )


def test_a_transaction_without_an_id_or_status_is_dropped() -> None:
    assert project_transaction({"status": "paid"}) is None
    assert project_transaction({"id": "txn_4"}) is None
    assert project_transaction({"id": "", "status": "paid"}) is None
