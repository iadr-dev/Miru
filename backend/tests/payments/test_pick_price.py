"""Characterization of `payments.pricing._pick_price` / `_as_price`: which amount a visitor is
shown. Paddle states the price; this only chooses the country override or the base price, and an
unusable value must never turn into a made-up amount."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from payments.pricing import PlanPrice, _as_price, _pick_price

BASE = {"amount": "990", "currency_code": "USD"}
TW = {"amount": "29900", "currency_code": "TWD"}
JP = {"amount": "1500", "currency_code": "JPY"}


def price(entry: Mapping[str, str]) -> PlanPrice:
    return PlanPrice(amount=entry["amount"], currency_code=entry["currency_code"])


def override(codes: object, unit_price: object) -> dict[str, object]:
    return {"country_codes": codes, "unit_price": unit_price}


def row(overrides: object = None, unit_price: object = BASE) -> dict[str, object]:
    out: dict[str, object] = {"id": "pri_1"}
    if unit_price is not None:
        out["unit_price"] = unit_price
    if overrides is not None:
        out["unit_price_overrides"] = overrides
    return out


@pytest.mark.parametrize("country", [None, ""])
def test_without_a_country_the_base_price_is_shown_even_if_overrides_exist(
    country: str | None,
) -> None:
    assert _pick_price(row([override(["TW"], TW)]), country) == price(BASE)


@pytest.mark.parametrize("country", ["TW", "tw", "Tw"])
def test_a_matching_country_gets_its_override_regardless_of_the_callers_case(country: str) -> None:
    assert _pick_price(row([override(["TW"], TW)]), country) == price(TW)


def test_override_codes_are_compared_upper_case_only() -> None:
    assert _pick_price(row([override(["tw"], TW)]), "TW") == price(BASE)
    assert _pick_price(row([override(["tw"], TW)]), "tw") == price(BASE)


def test_a_country_without_an_override_gets_the_base_price() -> None:
    assert _pick_price(row([override(["TW"], TW)]), "JP") == price(BASE)


def test_the_override_list_may_carry_several_countries_and_entries() -> None:
    overrides = [
        override(["DE", "FR"], {"amount": "900", "currency_code": "EUR"}),
        override(["JP", "TW"], JP),
    ]
    assert _pick_price(row(overrides), "TW") == price(JP)
    assert _pick_price(row(overrides), "fr") == price({"amount": "900", "currency_code": "EUR"})


def test_the_first_usable_matching_override_wins() -> None:
    overrides = [override(["TW"], TW), override(["TW"], JP)]
    assert _pick_price(row(overrides), "TW") == price(TW)


@pytest.mark.parametrize(
    "bad_unit_price",
    [
        None,
        "29900",
        ["29900", "TWD"],
        {},
        {"amount": "1"},
        {"currency_code": "TWD"},
        {"amount": 29900, "currency_code": "TWD"},
        {"amount": "29900", "currency_code": 901},
        {"amount": None, "currency_code": None},
    ],
)
def test_an_unusable_override_price_is_skipped_for_the_next_match_then_the_base(
    bad_unit_price: object,
) -> None:
    assert _pick_price(row([override(["TW"], bad_unit_price)]), "TW") == price(BASE)
    both = [override(["TW"], bad_unit_price), override(["TW"], JP)]
    assert _pick_price(row(both), "TW") == price(JP)


@pytest.mark.parametrize("overrides", ["TW", {"country_codes": ["TW"]}, 5, True, ("x",)])
def test_overrides_that_are_not_a_list_are_ignored(overrides: object) -> None:
    assert _pick_price(row(overrides), "TW") == price(BASE)


def test_malformed_override_items_are_skipped_without_stopping_the_scan() -> None:
    overrides: list[object] = [
        "TW",
        None,
        7,
        ["TW"],
        {},
        {"unit_price": TW},
        override("TW", TW),
        override(None, TW),
        override({"TW"}, TW),
        override([], TW),
        override([1, None, "tw"], TW),
        override([1, None, "TW"], JP),
    ]
    assert _pick_price(row(overrides), "TW") == price(JP)


def test_an_empty_override_list_gives_the_base_price() -> None:
    assert _pick_price(row([]), "TW") == price(BASE)


@pytest.mark.parametrize(
    "unit_price",
    [None, "990", [], {}, {"amount": "990"}, {"amount": 990, "currency_code": "USD"}],
)
def test_a_missing_or_unusable_base_price_is_none_never_a_guess(unit_price: object) -> None:
    assert _pick_price(row(unit_price=unit_price), None) is None
    assert _pick_price(row(unit_price=unit_price), "TW") is None


def test_an_unusable_base_price_does_not_hide_a_usable_override() -> None:
    assert _pick_price(row([override(["TW"], TW)], unit_price=None), "TW") == price(TW)
    assert _pick_price(row([override(["TW"], TW)], unit_price=None), "JP") is None


def test_the_row_is_not_mutated_and_the_amount_is_returned_verbatim() -> None:
    source = row([override(["TW"], {"amount": "0", "currency_code": "twd", "extra": 1})])
    snapshot = repr(source)

    picked = _pick_price(source, "TW")

    assert picked == PlanPrice(amount="0", currency_code="twd")
    assert repr(source) == snapshot


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (BASE, price(BASE)),
        ({"amount": "", "currency_code": ""}, PlanPrice(amount="", currency_code="")),
        (
            {"amount": "1", "currency_code": "USD", "extra": True},
            PlanPrice(amount="1", currency_code="USD"),
        ),
        (None, None),
        ("990", None),
        (["990", "USD"], None),
        ({}, None),
        ({"amount": "1"}, None),
        ({"currency_code": "USD"}, None),
        ({"amount": 1, "currency_code": "USD"}, None),
        ({"amount": "1", "currency_code": None}, None),
    ],
)
def test_as_price_needs_two_text_fields(value: object, expected: PlanPrice | None) -> None:
    assert _as_price(value) == expected
