"""What a fired rule says: the stock's name from the roster, thousands separators, and the layout
that carries the same checked strings to Telegram and LINE."""

from __future__ import annotations

import html
import re
from datetime import date

import pytest

from config.settings import Settings
from governance.textscan import check_output
from ingest.jobs import MATERIAL_ANNOUNCEMENTS_DATASET, PRICE_EOD_JOB, STOCK_INFO_JOB
from ingest.us_jobs import US_PRICE_EOD_DATASET
from scheduler.delivery.base import DeliveryNotification, DeliveryRecipient
from scheduler.delivery.channels import LineMessagingChannel, TelegramChannel
from scheduler.delivery.layout import render_html, render_text
from scheduler.evaluator import Evaluation, SnapshotView, evaluate_condition
from scheduler.notification_messages import alert_layout, safe_alert_message
from scheduler.templates import (
    ClosePrice,
    ConditionModel,
    DailyChange,
    MarginBalanceChange,
    MaterialAnnouncementPublished,
    describe,
)
from schemas.alerts import NON_ADVICE_FOOTER, format_number
from schemas.enums import Market

_DAY = date(2026, 10, 29)
_PRIOR = date(2026, 10, 28)
_ROSTER_DAY = date(2026, 10, 27)
_PRICE = PRICE_EOD_JOB.dataset
_ROSTER = STOCK_INFO_JOB.dataset


class _Reader:
    def __init__(self, parts: dict[tuple[str, str, date], list[dict[str, object]]]) -> None:
        self._parts = parts

    def read_partition(self, dataset: str, market: str, run_date: date) -> list[dict[str, object]]:
        return list(self._parts.get((dataset, market, run_date), []))

    def has_partition(self, dataset: str, market: str, run_date: date) -> bool:
        return (dataset, market, run_date) in self._parts


def _tw_reader(*, close: float = 1020.0, name: str | None = "台積電") -> _Reader:
    parts: dict[tuple[str, str, date], list[dict[str, object]]] = {
        (_PRICE, "TW", _DAY): [{"symbol": "2330", "close": close, "volume": 1000}],
        (_PRICE, "TW", _PRIOR): [{"symbol": "2330", "close": 1000.0, "volume": 1000}],
    }
    if name is not None:
        parts[(_ROSTER, "TW", _ROSTER_DAY)] = [{"symbol": "2330", "name": name}]
    return _Reader(parts)


def _evaluate(
    condition: ConditionModel, reader: _Reader, *, symbol: str = "2330", market: Market = Market.TW
) -> Evaluation:
    result = evaluate_condition(
        condition, symbol=symbol, market=market, as_of=_DAY, view=SnapshotView(reader)
    )
    assert result is not None
    return result


_ABOVE = ClosePrice(comparator="above", value=1000)


def test_a_fired_rule_names_the_stock_and_groups_its_numbers() -> None:
    result = _evaluate(_ABOVE, _tw_reader())
    assert (
        result.message
        == "2330 台積電 收盤價 1,020，高於設定值 1,000（你設定的條件：收盤價高於 1,000）"
    )
    body, layout = safe_alert_message(result, _DAY)
    assert body == f"{result.message}\n{NON_ADVICE_FOOTER}"
    assert layout is not None
    assert render_text(layout, NON_ADVICE_FOOTER) == (
        "🔔 2330 台積電\n"
        "收盤價 1,020，高於設定值 1,000\n"
        "條件：收盤價高於 1,000\n"
        "資料日期：2026-10-29\n\n"
        f"{NON_ADVICE_FOOTER}"
    )


@pytest.mark.parametrize("name", [None, ""])
def test_a_taiwan_symbol_with_no_name_on_file_is_shown_by_its_code(name: str | None) -> None:
    result = _evaluate(_ABOVE, _tw_reader(name=name))
    assert result.name == ""
    assert result.message.startswith("2330 收盤價 1,020，")
    _, layout = safe_alert_message(result, _DAY)
    assert layout is not None
    assert layout.blocks[0].head == "🔔 2330"


def test_a_us_ticker_has_no_name_even_when_a_taiwan_roster_exists() -> None:
    reader = _Reader(
        {
            (US_PRICE_EOD_DATASET, "US", _DAY): [{"symbol": "AAPL", "close": 1234.5, "volume": 1}],
            (_ROSTER, "TW", _ROSTER_DAY): [{"symbol": "AAPL", "name": "不該出現"}],
        }
    )
    result = _evaluate(_ABOVE, reader, symbol="AAPL", market=Market.US)
    assert result.message.startswith("AAPL 收盤價 1,234.5，高於設定值 1,000")
    _, layout = safe_alert_message(result, _DAY)
    assert layout is not None
    assert layout.blocks[0].head == "🔔 AAPL"


def test_the_name_comes_from_the_newest_roster_on_or_before_the_data_day() -> None:
    reader = _tw_reader()
    reader._parts[(_ROSTER, "TW", date(2026, 10, 20))] = [{"symbol": "2330", "name": "舊名"}]
    assert _evaluate(_ABOVE, reader).name == "台積電"
    far = _Reader({**_tw_reader()._parts, (_ROSTER, "TW", date(2026, 8, 1)): [{"symbol": "2330"}]})
    far._parts.pop((_ROSTER, "TW", _ROSTER_DAY))
    assert _evaluate(_ABOVE, far).name == ""


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0"),
        (12.5, "12.5"),
        (1000, "1,000"),
        (1020.0, "1,020"),
        (1234567.891, "1,234,567.891"),
        (0.000001, "0.000001"),
        (-1500.25, "-1,500.25"),
    ],
)
def test_format_number_groups_thousands_and_drops_trailing_zeros(value: float, text: str) -> None:
    assert format_number(value) == text


def test_describe_groups_numbers_only_when_asked_and_is_otherwise_unchanged() -> None:
    condition = ClosePrice(comparator="above", value=1000)
    assert describe(condition) == "收盤價高於 1000"
    assert describe(condition, grouped=True) == "收盤價高於 1,000"
    margin = MarginBalanceChange(side="margin", direction="up", percent=7.5)
    assert describe(margin, grouped=True) == describe(margin)
    move = DailyChange(direction="down", percent=12.5)
    assert describe(move, grouped=True) == describe(move) == "單日跌幅達 12.5%"


_MARKUP_NAMES = ["A&B <C> 'D'", "x</b><script>", "&amp;"]


@pytest.mark.parametrize("name", _MARKUP_NAMES)
def test_a_name_with_markup_characters_is_escaped_in_html_and_literal_in_text(name: str) -> None:
    result = _evaluate(_ABOVE, _tw_reader(name=name))
    _, layout = safe_alert_message(result, _DAY)
    assert layout is not None
    markup = render_html(layout, NON_ADVICE_FOOTER)
    assert markup.startswith(f"<b>{html.escape(f'🔔 2330 {name}', quote=False)}</b>")
    assert set(re.findall(r"</?(\w+)", markup)) == {"b", "i"}
    assert render_text(layout, NON_ADVICE_FOOTER).startswith(f"🔔 2330 {name}\n")


@pytest.mark.parametrize("close", [1020.0, 999.0, 123456.0])
def test_every_generated_text_passes_the_output_scan(close: float) -> None:
    result = _evaluate(ClosePrice(comparator="above", value=100), _tw_reader(close=close))
    body, layout = safe_alert_message(result, _DAY)
    assert layout is not None
    for text in (body, render_text(layout, NON_ADVICE_FOOTER)):
        assert check_output(text, has_market_data=False).reason is None


def test_a_quote_the_scan_rejects_falls_back_to_the_text_without_it() -> None:
    subject = "建議買進 目標價 1200"
    reader = _Reader(
        {
            (MATERIAL_ANNOUNCEMENTS_DATASET, "TW", _DAY): [
                {
                    "symbol": "2330",
                    "subject": subject,
                    "announced_date": _DAY.isoformat(),
                    "announced_time": "17:00",
                }
            ],
            (_ROSTER, "TW", _ROSTER_DAY): [{"symbol": "2330", "name": "台積電"}],
        }
    )
    result = _evaluate(MaterialAnnouncementPublished(), reader)
    assert result.plain_message is not None
    body, layout = safe_alert_message(result, _DAY)
    assert "目標價" not in body
    assert layout is not None
    assert "目標價" not in render_text(layout, NON_ADVICE_FOOTER)
    assert layout.blocks[0].lines[0] == "公布 1 則重大訊息"
    assert layout == alert_layout(result, _DAY, quoted=False)


def test_a_flagged_fact_leaves_only_the_fixed_line_and_no_layout() -> None:
    flagged = Evaluation(
        met=True,
        symbol="2330",
        name="",
        fact="建議買進，目標價 700",
        condition_text="收盤價高於 600",
    )
    body, layout = safe_alert_message(flagged, _DAY)
    assert layout is None
    assert body == f"2330 的條件已達成：收盤價高於 600\n{NON_ADVICE_FOOTER}"


def test_the_alert_reaches_telegram_and_line_as_the_same_four_lines() -> None:
    result = _evaluate(_ABOVE, _tw_reader())
    body, layout = safe_alert_message(result, _DAY)
    notification = DeliveryNotification(
        notification_id="n1", title="2330 - 收盤價高於 1000", body=body, layout=layout
    )
    line = LineMessagingChannel(Settings(line_messaging_channel_access_token="t")).build_request(
        notification, DeliveryRecipient(user_id="u", line_user_id="U1")
    )
    telegram = TelegramChannel(Settings(telegram_bot_token="t")).build_request(
        notification, DeliveryRecipient(user_id="u", telegram_chat_id="1")
    )
    [message] = line.json["messages"]  # type: ignore[misc]
    assert str(message["text"]).splitlines()[:4] == [
        "🔔 2330 台積電",
        "收盤價 1,020，高於設定值 1,000",
        "條件：收盤價高於 1,000",
        "資料日期：2026-10-29",
    ]
    assert str(telegram.json["text"]).startswith("<b>🔔 2330 台積電</b>\n收盤價 1,020")
