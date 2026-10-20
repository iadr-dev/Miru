from __future__ import annotations

import pytest
from pydantic import ValidationError

from analytics.supply_chain.domain.models import Endpoint, Evidence, Relation, Trust
from tests.analytics.supply_chain.builders import ALPHA, relation


def test_market_must_be_tw_or_us() -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate({"symbol": "1", "market": "JP"})


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate({"symbol": "1", "market": "TW", "bogus": 1})


def test_evidence_url_and_excerpt_limits() -> None:
    base = {"publisher": "p", "trust": Trust.SECONDARY}
    with pytest.raises(ValidationError):
        Evidence.model_validate({**base, "url": "ftp://x"})
    with pytest.raises(ValidationError):
        Evidence.model_validate({**base, "url": "https://x.example", "excerpt": "a" * 301})


def test_relation_needs_two_companies_and_valid_revenue() -> None:
    with pytest.raises(ValidationError):
        relation(ALPHA, ALPHA)
    payload = {**relation().model_dump(), "revenue_pct": 101}
    with pytest.raises(ValidationError):
        Relation.model_validate(payload)


def test_key_uses_empty_string_for_no_product() -> None:
    assert relation().key.product_key == ""
    assert relation(product="widgets").key.product_key == "widgets"
