"""`DateRangeInput` / `MarketOnlyInput` reject inverted or absurdly wide ranges at validation time.

An anonymous caller used to be able to ask an upstream for decades of rows, and every distinct range
is a new cache key. The cap is generous on purpose: the web's dividend history asks for 30 years
("all"), which must keep working.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from schemas.enums import Market
from tools.common import MAX_DATE_RANGE_DAYS, DateRangeInput, MarketOnlyInput

_TODAY = date(2026, 10, 11)


def test_the_web_dividend_all_range_of_thirty_years_is_accepted() -> None:
    thirty_years_ago = date(_TODAY.year - 30, _TODAY.month, _TODAY.day)

    parsed = DateRangeInput(
        id="2330", market=Market.TW, start_date=thirty_years_ago, end_date=_TODAY
    )

    assert parsed.start_date == thirty_years_ago


def test_open_ended_ranges_are_accepted() -> None:
    assert DateRangeInput(id="2330", market=Market.TW).start_date is None
    assert DateRangeInput(id="2330", market=Market.TW, start_date=_TODAY).end_date is None
    assert MarketOnlyInput(market=Market.TW, end_date=_TODAY).start_date is None


@pytest.mark.parametrize("model", ["symbol", "market"])
def test_an_inverted_range_is_rejected(model: str) -> None:
    with pytest.raises(ValidationError, match="is after end_date"):
        if model == "symbol":
            DateRangeInput(
                id="2330", market=Market.TW, start_date=_TODAY, end_date=date(2026, 1, 1)
            )
        else:
            MarketOnlyInput(market=Market.TW, start_date=_TODAY, end_date=date(2026, 1, 1))


@pytest.mark.parametrize("model", ["symbol", "market"])
def test_a_range_wider_than_the_cap_is_rejected(model: str) -> None:
    start = date(1900, 1, 1)
    assert (_TODAY - start).days > MAX_DATE_RANGE_DAYS

    with pytest.raises(ValidationError, match="wider than"):
        if model == "symbol":
            DateRangeInput(id="2330", market=Market.TW, start_date=start, end_date=_TODAY)
        else:
            MarketOnlyInput(market=Market.TW, start_date=start, end_date=_TODAY)
