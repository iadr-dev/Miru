"""`propose_alert` is propose-only: it validates and echoes a rule for a confirmation card and
performs no write. The confirm gate is the card (the frontend calls POST /alerts), so these tests
pin (a) the proposal contract, (b) that an invalid proposal is rejected *in the tool arguments* so
the model can correct it, and (c) that the governed card payload keeps its fact phrasing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.loop import _build_card
from governance.textscan import check_output, scan_structured_payload
from schemas.alerts import TriggerMode
from schemas.enums import DataClass, Market
from tools.base import ToolContext
from tools.propose_alert_tools import (
    ProposeAlertInput,
    ProposeAlertTool,
    build_registration_table,
)


def price_proposal(**overrides: object) -> ProposeAlertInput:
    base: dict[str, object] = {
        "symbol": "2330",
        "market": Market.TW,
        "condition": {"kind": "close_price", "comparator": "above", "value": 600.5},
    }
    return ProposeAlertInput.model_validate({**base, **overrides})


async def test_echoes_the_proposal_with_a_fact_phrased_summary() -> None:
    output = await ProposeAlertTool().run(price_proposal(), ToolContext())
    dumped = output.model_dump(mode="json")
    assert dumped["symbol"] == "2330"
    assert dumped["condition"] == {"kind": "close_price", "comparator": "above", "value": 600.5}
    assert dumped["summary"] == "2330 收盤價高於 600.5"
    assert dumped["trigger_mode"] == TriggerMode.ONCE_PER_DAY.value
    assert dumped["expires_in_days"] == 90


async def test_output_always_says_user_confirmation_is_required() -> None:
    output = await ProposeAlertTool().run(price_proposal(), ToolContext())
    assert output.model_dump(mode="json")["requires_user_confirmation"] is True


async def test_runs_without_a_user_or_any_store() -> None:
    # Anonymous context, no session factory: the tool must not need identity or I/O, because the
    # *signed-in user* creates the rule from the card, not the agent.
    output = await ProposeAlertTool().run(price_proposal(), ToolContext(user_id=None))
    assert output is not None


def test_opts_into_the_alert_proposal_card_and_is_public() -> None:
    tool = ProposeAlertTool()
    assert tool.card_type == "alert_proposal"
    assert tool.data_class is DataClass.PUBLIC


def test_description_tells_the_model_it_cannot_create_the_alert() -> None:
    description = ProposeAlertTool().description
    assert "creates nothing" in description
    assert "never say the alert was created" in description.lower()


def test_registered_as_one_deferred_tool() -> None:
    [(tool, immediate)] = build_registration_table()
    assert tool.name == "propose_alert"
    assert immediate is False


@pytest.mark.parametrize(
    "condition",
    [
        {"kind": "institutional_streak", "investor": "foreign", "direction": "buy", "days": 3},
        {"kind": "attention_entry"},
        {"kind": "disposition_entry"},
        {"kind": "month_revenue_published"},
        {"kind": "margin_balance_change", "side": "short", "direction": "down", "percent": 5},
        {"kind": "etf_premium_discount", "direction": "discount", "percent": 2},
    ],
)
def test_tw_only_templates_are_rejected_for_us_symbols(condition: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="TW market only"):
        price_proposal(symbol="AAPL", market=Market.US, condition=condition)


def test_us_symbols_still_get_price_templates() -> None:
    assert price_proposal(symbol="AAPL", market=Market.US).market is Market.US


def test_unsupported_markets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="market"):
        price_proposal(market=Market.JP)


@pytest.mark.parametrize(
    "condition",
    [
        {"kind": "close_price", "comparator": "above", "value": -1},
        {"kind": "daily_change", "direction": "up", "percent": 101},
        {"kind": "volume_surge", "multiple": 1},
        {"kind": "institutional_streak", "investor": "foreign", "direction": "buy", "days": 1},
        {"kind": "made_up"},
        {"type": "price", "metric": "close", "comparator": "above", "threshold": 600},  # legacy
    ],
)
def test_out_of_range_or_unknown_conditions_are_rejected(condition: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        price_proposal(condition=condition)


def test_unknown_input_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        price_proposal(watchlist_id="wl-1")  # scope beyond one symbol is not proposable here


async def test_card_payload_keeps_fact_phrasing_through_the_governance_scan() -> None:
    # The card is built through scan_structured_payload: a string matching a trading pattern is
    # replaced wholesale, which would corrupt the proposal. Fact phrasing must survive untouched.
    tool = ProposeAlertTool()
    conditions = [
        {"kind": "close_price", "comparator": "below", "value": 90},
        {"kind": "daily_change", "direction": "down", "percent": 5},
        {"kind": "volume_surge", "multiple": 2},
        {"kind": "institutional_streak", "investor": "foreign", "direction": "buy", "days": 3},
        {"kind": "attention_entry"},
        {"kind": "disposition_entry"},
        {"kind": "month_revenue_published"},
        {"kind": "margin_balance_change", "side": "margin", "direction": "up", "percent": 10},
        {"kind": "etf_premium_discount", "direction": "premium", "percent": 1},
    ]
    for condition in conditions:
        output = await tool.run(price_proposal(condition=condition), ToolContext())
        card = _build_card("alert_proposal", output)
        assert card.card_type == "alert_proposal"
        assert card.data["condition"] == output.model_dump(mode="json")["condition"]
        assert card.data["summary"] == output.model_dump(mode="json")["summary"]
        assert card.data["requires_user_confirmation"] is True
        _, redacted = scan_structured_payload(output.model_dump(mode="json"))
        assert redacted is False, condition
        verdict = check_output(str(card.data["summary"]), has_market_data=False)
        assert verdict.reason != "trading_language_redacted"


async def test_symbol_is_normalised_like_post_alerts_so_the_card_shows_what_gets_created() -> None:
    output = await ProposeAlertTool().run(
        price_proposal(symbol="  aapl ", market=Market.US), ToolContext()
    )
    dumped = output.model_dump(mode="json")
    assert dumped["symbol"] == "AAPL"
    assert dumped["summary"].startswith("AAPL ")


def test_the_model_cannot_choose_the_rules_lifetime() -> None:
    # A live model once sent `expires_in_days: null` unprompted and the resulting rule never
    # expired. The lifetime is fixed, so the argument must not exist for a model to misuse.
    assert "expires_in_days" not in ProposeAlertTool().to_tool_spec().parameters["properties"]
    with pytest.raises(ValidationError):
        price_proposal(expires_in_days=None)


async def test_every_proposal_carries_the_default_90_day_lifetime() -> None:
    output = await ProposeAlertTool().run(price_proposal(), ToolContext())
    assert output.model_dump(mode="json")["expires_in_days"] == 90


def test_trigger_mode_is_not_a_model_argument() -> None:
    """A model once set 'once' unprompted; the frequency is not its call (extra="forbid")."""
    with pytest.raises(ValidationError):
        ProposeAlertInput.model_validate(
            {
                "symbol": "2317",
                "market": "TW",
                "condition": {"kind": "close_price", "comparator": "above", "value": 300},
                "trigger_mode": "once",
            }
        )
