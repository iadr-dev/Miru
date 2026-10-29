"""Delivery-channel behaviour: in-app always delivers; the external channels build a real,
correctly-shaped request and report a no-network success in the default profile; a missing
recipient address is a non-retryable receipt, never a crash."""

from __future__ import annotations

import html
import json
import re

import httpx
import pytest

import scheduler.delivery.transport as transport_module
from config.settings import Settings
from scheduler.delivery.base import DeliveryNotification, DeliveryReceipt, DeliveryRecipient
from scheduler.delivery.channels import (
    EmailChannel,
    InAppChannel,
    LineMessagingChannel,
    PushChannel,
    TelegramChannel,
)
from scheduler.delivery.layout import Layout, LayoutBlock, fit_html, render_text
from scheduler.delivery.transport import HttpxTransport, TransportResult
from scheduler.models import DeliveryChannelKind
from schemas.alerts import NON_ADVICE_FOOTER
from tests.http_support import OwnedLease


def _notification() -> DeliveryNotification:
    return DeliveryNotification(
        notification_id="n1", title="排程摘要", body="台積電外資買超連 3 日。非買賣建議。"
    )


async def test_in_app_delivers() -> None:
    receipt = await InAppChannel().send(_notification(), DeliveryRecipient(user_id="u1"))
    assert receipt.delivered is True
    assert receipt.channel is DeliveryChannelKind.IN_APP


async def test_line_builds_messaging_request_shape() -> None:
    channel = LineMessagingChannel(Settings(line_messaging_channel_access_token="tok"))
    request = channel.build_request(
        _notification(), DeliveryRecipient(user_id="u1", line_user_id="Uabc")
    )
    assert request.url == "https://api.line.me/v2/bot/message/push"
    assert request.json["to"] == "Uabc"
    assert request.headers["Authorization"].startswith("Bearer ")


async def test_telegram_builds_request_shape() -> None:
    channel = TelegramChannel(Settings(telegram_bot_token="bot-tok"))
    request = channel.build_request(
        _notification(), DeliveryRecipient(user_id="u1", telegram_chat_id="12345")
    )
    assert request.url.endswith("/sendMessage")
    assert request.json["chat_id"] == "12345"


async def test_missing_recipient_is_non_retryable_not_a_crash() -> None:
    channel = EmailChannel(Settings(resend_api_key="re_key", email_from_address="Miru <a@b.test>"))
    # No email address on the recipient → graceful non-retryable receipt.
    receipt = await channel.send(_notification(), DeliveryRecipient(user_id="u1"))
    assert receipt.delivered is False
    assert receipt.retryable is False


async def test_unconfigured_channel_reports_no_network_success() -> None:
    # No token configured, but recipient is addressable → shaped, no-network delivery.
    channel = TelegramChannel(Settings())
    receipt = await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", telegram_chat_id="12345")
    )
    assert receipt.delivered is True


class _FakeTransport:
    def __init__(self, *results: TransportResult) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def post_json(
        self, url: str, *, headers: dict[str, str], body: dict[str, object]
    ) -> TransportResult:
        self.calls.append((url, headers, body))
        return self._results.pop(0)


async def test_a_configured_telegram_channel_really_posts_and_reports_the_outcome() -> None:
    transport = _FakeTransport(TransportResult(ok=True, status=200))
    channel = TelegramChannel(Settings(telegram_bot_token="bot-tok"), transport)
    receipt = await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", telegram_chat_id="12345")
    )
    assert receipt.delivered is True
    assert transport.calls[0][0] == "https://api.telegram.org/botbot-tok/sendMessage"
    assert transport.calls[0][2]["chat_id"] == "12345"


async def test_a_transient_provider_failure_is_retryable_and_a_4xx_is_not() -> None:
    settings = Settings(line_messaging_channel_access_token="tok")
    recipient = DeliveryRecipient(user_id="u1", line_user_id="Uabc")
    busy = LineMessagingChannel(
        settings, _FakeTransport(TransportResult(ok=False, status=503, retryable=True))
    )
    blocked = LineMessagingChannel(
        settings, _FakeTransport(TransportResult(ok=False, status=403, retryable=False))
    )
    assert (await busy.send(_notification(), recipient)).retryable is True
    receipt = await blocked.send(_notification(), recipient)
    assert receipt.delivered is False
    assert receipt.retryable is False


async def test_email_posts_to_resend_with_an_idempotency_key_and_the_footer() -> None:
    transport = _FakeTransport(TransportResult(ok=True, status=200))
    channel = EmailChannel(
        Settings(resend_api_key="re_key", email_from_address="Miru <alerts@miru.test>"), transport
    )
    receipt = await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", email_address="a@example.test")
    )
    assert receipt.delivered is True
    url, headers, body = transport.calls[0]
    assert url == "https://api.resend.com/emails"
    assert headers["Authorization"] == "Bearer re_key"
    assert headers["Idempotency-Key"] == "n1"  # a retry after a timeout cannot double-send
    assert body["to"] == ["a@example.test"]
    assert body["from"] == "Miru <alerts@miru.test>"
    assert "非投資建議" in str(body["text"])
    html_part = str(body["html"])
    assert 'lang="zh-Hant"' in html_part
    assert "<title>排程摘要</title>" in html_part


async def test_email_html_escapes_the_body() -> None:
    transport = _FakeTransport(TransportResult(ok=True))
    channel = EmailChannel(Settings(resend_api_key="k", email_from_address="a@b.test"), transport)
    await channel.send(
        DeliveryNotification(notification_id="n2", title="<b>t</b>", body="<script>x</script>"),
        DeliveryRecipient(user_id="u1", email_address="a@example.test"),
    )
    html_part = str(transport.calls[0][2]["html"])
    assert "<script>" not in html_part
    assert "&lt;script&gt;" in html_part


async def test_an_unconfigured_email_channel_reports_no_network_success() -> None:
    receipt = await EmailChannel(Settings()).send(
        _notification(), DeliveryRecipient(user_id="u1", email_address="a@example.test")
    )
    assert receipt.delivered is True


class _FakePush:
    def __init__(self, *results: TransportResult) -> None:
        self._results = list(results)
        self.sent: list[tuple[dict[str, object], str]] = []

    async def send(self, *, subscription: dict[str, object], payload: str) -> TransportResult:
        self.sent.append((subscription, payload))
        return self._results.pop(0)


_VAPID = Settings(vapid_public_key="pub", vapid_private_key="priv")


def _subscription(name: str) -> str:
    return json.dumps(
        {
            "endpoint": f"https://fcm.googleapis.com/fcm/send/{name}",
            "keys": {"p256dh": "k", "auth": "a"},
        }
    )


async def test_push_is_delivered_when_any_browser_accepts_and_a_gone_one_is_reported_stale() -> (
    None
):
    stale, good = _subscription("stale"), _subscription("good")
    sender = _FakePush(
        TransportResult(ok=False, status=410, gone=True), TransportResult(ok=True, status=201)
    )
    channel = PushChannel(_VAPID, sender)
    receipt = await channel.send(
        DeliveryNotification(
            notification_id="n9",
            title="提醒",
            body="2330 觀察",
            deep_link={"route": "stock", "symbol": "2330"},
        ),
        DeliveryRecipient(user_id="u1", device_tokens=(stale, good)),
    )
    assert receipt.delivered is True
    assert receipt.stale_tokens == (stale,)
    payload = json.loads(sender.sent[0][1])
    assert payload["url"] == "/stock/2330"
    assert payload["title"] == "提醒"


async def test_a_schedule_notification_opens_its_thread() -> None:
    sender = _FakePush(TransportResult(ok=True))
    await PushChannel(_VAPID, sender).send(
        DeliveryNotification(
            notification_id="n1",
            title="排程摘要",
            body="x",
            deep_link={"route": "thread", "thread_id": "t-1"},
        ),
        DeliveryRecipient(user_id="u1", device_tokens=(_subscription("a"),)),
    )
    assert json.loads(sender.sent[0][1])["url"] == "/chat?thread=t-1"


async def test_push_with_only_a_non_subscription_token_marks_it_stale_and_fails() -> None:
    channel = PushChannel(_VAPID, _FakePush())
    receipt = await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", device_tokens=("native-token",))
    )
    assert receipt.delivered is False
    assert receipt.stale_tokens == ("native-token",)
    assert receipt.retryable is False


async def test_a_transient_push_failure_is_retryable() -> None:
    channel = PushChannel(_VAPID, _FakePush(TransportResult(ok=False, status=503, retryable=True)))
    receipt = await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", device_tokens=(_subscription("a"),))
    )
    assert receipt.delivered is False
    assert receipt.retryable is True


async def test_push_without_a_browser_device_is_a_clean_non_retryable_receipt() -> None:
    receipt = await PushChannel(_VAPID, _FakePush()).send(
        _notification(), DeliveryRecipient(user_id="u1")
    )
    assert receipt.delivered is False
    assert receipt.retryable is False


async def test_a_stored_subscription_pointing_at_an_internal_address_is_never_posted_to() -> None:
    evil = json.dumps(
        {"endpoint": "http://169.254.169.254/latest", "keys": {"p256dh": "k", "auth": "a"}}
    )
    sender = _FakePush()
    receipt = await PushChannel(_VAPID, sender).send(
        _notification(), DeliveryRecipient(user_id="u1", device_tokens=(evil,))
    )
    assert sender.sent == []  # nothing left the server
    assert receipt.delivered is False
    assert receipt.stale_tokens == (evil,)  # and the bad row gets pruned


async def test_the_email_carries_a_one_click_unsubscribe_header_and_link() -> None:
    transport = _FakeTransport(TransportResult(ok=True))
    channel = EmailChannel(Settings(resend_api_key="k", email_from_address="a@b.test"), transport)
    link = "https://miru.example/api/v1/email/unsubscribe?token=u.sig"
    await channel.send(
        _notification(),
        DeliveryRecipient(user_id="u1", email_address="a@example.test", unsubscribe_url=link),
    )
    body = transport.calls[0][2]
    assert body["headers"] == {
        "List-Unsubscribe": f"<{link}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert link in str(body["text"])  # and a visible link for clients that ignore the header


async def test_no_unsubscribe_header_without_a_link() -> None:
    transport = _FakeTransport(TransportResult(ok=True))
    channel = EmailChannel(Settings(resend_api_key="k", email_from_address="a@b.test"), transport)
    await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", email_address="a@example.test")
    )
    assert "headers" not in transport.calls[0][2]


_TELEGRAM_LIMIT = 4096
_POINTER = "…（完整內容請見 Miru）"


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _telegram_text(body: str, *, title: str = "排程摘要") -> str:
    channel = TelegramChannel(Settings(telegram_bot_token="bot-tok"))
    request = channel.build_request(
        DeliveryNotification(notification_id="n1", title=title, body=body),
        DeliveryRecipient(user_id="u1", telegram_chat_id="12345"),
    )
    return str(request.json["text"])


def test_a_short_telegram_message_is_sent_whole() -> None:
    body = f"短訊息\n{NON_ADVICE_FOOTER}"
    assert _telegram_text(body) == f"排程摘要\n{body}"


def test_a_long_telegram_message_is_cut_at_a_line_and_keeps_the_footer() -> None:
    lines = [f"第 {i} 行 📈 台積電外資買超連 3 日" for i in range(600)]
    text = _telegram_text("\n".join(lines) + f"\n{NON_ADVICE_FOOTER}")
    assert _units(text) <= _TELEGRAM_LIMIT
    assert text.endswith(f"{_POINTER}\n{NON_ADVICE_FOOTER}")
    kept = text.removesuffix(f"\n{_POINTER}\n{NON_ADVICE_FOOTER}").split("\n")[1:]
    assert kept == lines[: len(kept)]
    assert 0 < len(kept) < len(lines)
    assert _TELEGRAM_LIMIT - _units(text) < _units(lines[len(kept)]) + 1  # as much as fits was kept


def test_the_limit_counts_utf16_units_so_emoji_cost_two() -> None:
    body = "📈" * 3000 + f"\n{NON_ADVICE_FOOTER}"
    text = _telegram_text(body)
    assert _units(text) <= _TELEGRAM_LIMIT
    assert len(text) < _TELEGRAM_LIMIT  # code points, not units, would have fit more
    assert text.endswith(NON_ADVICE_FOOTER)


def test_one_enormous_line_is_cut_on_a_character_boundary() -> None:
    text = _telegram_text("字" * 9000 + f"\n{NON_ADVICE_FOOTER}")
    assert _units(text) <= _TELEGRAM_LIMIT
    assert text.endswith(f"{_POINTER}\n{NON_ADVICE_FOOTER}")


def test_a_message_exactly_at_the_limit_is_untouched() -> None:
    title = "題"
    fixed = _units(f"{title}\n\n{NON_ADVICE_FOOTER}")
    body = "字" * (_TELEGRAM_LIMIT - fixed)
    text = _telegram_text(f"{body}\n{NON_ADVICE_FOOTER}", title=title)
    assert _units(text) == _TELEGRAM_LIMIT
    assert _POINTER not in text


def test_a_long_message_without_a_footer_still_fits() -> None:
    text = _telegram_text("\n".join("字" * 50 for _ in range(200)))
    assert _units(text) <= _TELEGRAM_LIMIT
    assert text.endswith(_POINTER)


_APP = "https://miru.example"


def _telegram_request_json(
    deep_link: dict[str, object], *, app_url: str | None = _APP
) -> dict[str, object]:
    channel = TelegramChannel(Settings(telegram_bot_token="bot-tok"))
    request = channel.build_request(
        DeliveryNotification(notification_id="n1", title="t", body="b", deep_link=deep_link),
        DeliveryRecipient(user_id="u1", telegram_chat_id="12345", app_url=app_url),
    )
    return request.json


@pytest.mark.parametrize(
    ("deep_link", "path"),
    [
        ({"route": "stock", "symbol": "2330"}, "/stock/2330"),
        ({"route": "thread", "thread_id": "t-1"}, "/chat?thread=t-1"),
        ({"route": "watch", "symbol": "2330", "strategy": "x"}, "/watch"),
        ({"route": "notifications"}, "/settings/alerts"),
        ({}, "/settings/alerts"),
    ],
)
def test_telegram_carries_one_open_in_miru_button_for_the_deep_link(
    deep_link: dict[str, object], path: str
) -> None:
    payload = _telegram_request_json(deep_link)
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "在 Miru 開啟", "url": f"{_APP}{path}"}]]
    }
    assert "parse_mode" not in payload


def test_the_button_url_is_built_from_the_base_without_a_double_slash() -> None:
    payload = _telegram_request_json({"route": "stock", "symbol": "2330"}, app_url=f"{_APP}/")
    markup = payload["reply_markup"]
    assert markup == {"inline_keyboard": [[{"text": "在 Miru 開啟", "url": f"{_APP}/stock/2330"}]]}


def test_a_symbol_in_the_button_url_is_escaped() -> None:
    payload = _telegram_request_json({"route": "stock", "symbol": "a/b?c"})
    assert "a%2Fb%3Fc" in str(payload["reply_markup"])


def test_without_a_public_https_app_url_telegram_sends_no_keyboard() -> None:
    payload = _telegram_request_json({"route": "stock", "symbol": "2330"}, app_url=None)
    assert "reply_markup" not in payload


_BLOCKED = "Forbidden: bot was blocked by the user"
_CHAT_GONE = "Bad Request: chat not found"


def _telegram_error(status: int, description: str) -> TransportResult:
    return TransportResult(
        ok=False, status=status, detail=f"http {status}", description=description
    )


async def _telegram_receipt(result: TransportResult) -> DeliveryReceipt:
    channel = TelegramChannel(Settings(telegram_bot_token="bot-tok"), _FakeTransport(result))
    return await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", telegram_chat_id="12345")
    )


@pytest.mark.parametrize(
    ("status", "description"),
    [(403, _BLOCKED), (403, "Forbidden: user is deactivated"), (400, _CHAT_GONE)],
)
async def test_a_blocked_bot_or_missing_chat_marks_the_telegram_address_gone(
    status: int, description: str
) -> None:
    receipt = await _telegram_receipt(_telegram_error(status, description))
    assert receipt.delivered is False
    assert receipt.retryable is False
    assert receipt.address_gone is True


@pytest.mark.parametrize(
    "result",
    [
        TransportResult(ok=False, status=429, retryable=True, description="Too Many Requests"),
        TransportResult(ok=False, status=500, retryable=True),
        TransportResult(ok=False, status=502, retryable=True),
        TransportResult(ok=False, retryable=True, detail="network error"),
        _telegram_error(400, "Bad Request: message is too long"),
        _telegram_error(401, "Unauthorized"),
        _telegram_error(404, "Not Found"),
        TransportResult(ok=True, status=200),
    ],
)
async def test_other_telegram_outcomes_never_mark_the_address_gone(
    result: TransportResult,
) -> None:
    assert (await _telegram_receipt(result)).address_gone is False


async def test_a_line_403_is_not_an_unlink_signal() -> None:
    channel = LineMessagingChannel(
        Settings(line_messaging_channel_access_token="tok"),
        _FakeTransport(_telegram_error(403, _BLOCKED)),
    )
    receipt = await channel.send(
        _notification(), DeliveryRecipient(user_id="u1", line_user_id="Uabc")
    )
    assert receipt.address_gone is False


async def _post_through_httpx(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response | Exception
) -> TransportResult:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        transport_module,
        "lease_client",
        lambda **_: OwnedLease(httpx.AsyncClient(transport=httpx.MockTransport(handler))),
    )
    return await HttpxTransport().post_json(
        "https://api.telegram.org/botX/sendMessage", headers={}, body={}
    )


async def test_the_transport_keeps_the_error_description_the_provider_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"ok": False, "error_code": 403, "description": _BLOCKED}
    result = await _post_through_httpx(monkeypatch, httpx.Response(403, json=body))
    assert (result.ok, result.status, result.retryable) == (False, 403, False)
    assert result.description == _BLOCKED
    assert result.detail == "http 403"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, content=b"<html>nope</html>"),
        httpx.Response(403, json=["not", "an", "object"]),
        httpx.Response(403, json={"description": 7}),
    ],
)
async def test_an_unreadable_error_body_leaves_the_description_empty(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    assert (await _post_through_httpx(monkeypatch, response)).description == ""


async def test_a_network_failure_is_retryable_with_no_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _post_through_httpx(monkeypatch, httpx.ConnectError("down"))
    assert (result.ok, result.retryable, result.description) == (False, True, "")


_PARSE_ERROR = "Bad Request: can't parse entities: Unsupported start tag"
_RECIPIENT = DeliveryRecipient(user_id="u1", telegram_chat_id="12345")


def _layout_notification(*, name: str = "事欣科") -> DeliveryNotification:
    return DeliveryNotification(
        notification_id="n1",
        title="標題",
        body=f"4916 {name} 條件全數成立\n{NON_ADVICE_FOOTER}",
        layout=Layout(
            blocks=(
                LayoutBlock(head=f"🔔 4916 {name}", lines=("09:30 條件全數成立", "• 量比 14.25")),
                LayoutBlock(head="📖 名詞", lines=("波動率擠壓：布林通道寬度收斂。",), fold=True),
            )
        ),
    )


def _telegram_json(notification: DeliveryNotification) -> dict[str, object]:
    channel = TelegramChannel(Settings(telegram_bot_token="t"))
    return channel.build_request(notification, _RECIPIENT).json


def _strip_tags(markup: str) -> str:
    return html.unescape(re.sub(r"</?(?:b|i|blockquote)(?: expandable)?>", "", markup))


def test_a_layout_is_sent_as_html_that_reads_like_its_text() -> None:
    notification = _layout_notification()
    assert notification.layout is not None
    payload = _telegram_json(notification)
    text = str(payload["text"])
    assert payload["parse_mode"] == "HTML"
    assert text.startswith("<b>🔔 4916 事欣科</b>\n09:30 條件全數成立\n• 量比 14.25\n\n")
    assert (
        "<blockquote expandable><b>📖 名詞</b>\n波動率擠壓：布林通道寬度收斂。</blockquote>" in text
    )
    assert text.endswith(f"<i>{NON_ADVICE_FOOTER}</i>")
    assert _strip_tags(text) == render_text(notification.layout, NON_ADVICE_FOOTER)


def test_a_stock_name_with_markup_characters_is_escaped_not_interpreted() -> None:
    text = str(_telegram_json(_layout_notification(name="A&B <C> 'D'"))["text"])
    assert "A&amp;B &lt;C&gt; 'D'" in text
    assert "<C>" not in text
    assert set(re.findall(r"</?(\w+)", text)) == {"b", "i", "blockquote"}


def test_a_notification_without_a_layout_is_sent_as_plain_text() -> None:
    payload = _telegram_json(_notification())
    assert "parse_mode" not in payload
    assert payload["text"] == f"排程摘要\n{_notification().body}"


async def test_telegram_resends_as_plain_text_once_when_the_markup_is_rejected() -> None:
    transport = _FakeTransport(_telegram_error(400, _PARSE_ERROR), TransportResult(ok=True))
    channel = TelegramChannel(Settings(telegram_bot_token="t"), transport)
    receipt = await channel.send(_layout_notification(), _RECIPIENT)
    assert receipt.delivered is True
    assert [c[2].get("parse_mode") for c in transport.calls] == ["HTML", None]
    assert transport.calls[1][2]["text"] == f"標題\n{_layout_notification().body}"
    assert transport.calls[1][2]["chat_id"] == "12345"


async def test_a_second_rejection_is_final_and_a_transient_failure_keeps_the_retry_path() -> None:
    twice = _FakeTransport(_telegram_error(400, _PARSE_ERROR), _telegram_error(400, _PARSE_ERROR))
    receipt = await TelegramChannel(Settings(telegram_bot_token="t"), twice).send(
        _layout_notification(), _RECIPIENT
    )
    assert (receipt.delivered, receipt.retryable, len(twice.calls)) == (False, False, 2)

    flaky = _FakeTransport(TransportResult(ok=False, status=500, retryable=True))
    receipt = await TelegramChannel(Settings(telegram_bot_token="t"), flaky).send(
        _layout_notification(), _RECIPIENT
    )
    assert (receipt.delivered, receipt.retryable, len(flaky.calls)) == (False, True, 1)


async def test_other_400s_skip_the_plain_resend_and_a_403_still_unlinks() -> None:
    other = _FakeTransport(_telegram_error(400, "Bad Request: message is too long"))
    await TelegramChannel(Settings(telegram_bot_token="t"), other).send(
        _layout_notification(), _RECIPIENT
    )
    assert len(other.calls) == 1
    blocked = _FakeTransport(_telegram_error(403, _BLOCKED))
    receipt = await TelegramChannel(Settings(telegram_bot_token="t"), blocked).send(
        _layout_notification(), _RECIPIENT
    )
    assert receipt.address_gone is True


def _long_layout(rows: int) -> Layout:
    stocks = tuple(f"{i} 名稱 & <{i}>　上軌 1,000.5" for i in range(rows))
    return Layout(
        blocks=(
            LayoutBlock(head="📋 清單", lines=("市場寬度 35%",)),
            LayoutBlock(head="波動率擠壓", lines=stocks),
            LayoutBlock(head="📖 名詞", lines=("定義 " * 40,), fold=True),
        )
    )


@pytest.mark.parametrize("rows", [5, 400, 2000])
def test_a_trimmed_html_message_keeps_its_limit_balanced_tags_and_footer(rows: int) -> None:
    text = fit_html(_long_layout(rows), NON_ADVICE_FOOTER, pointer="…（完整內容請見 Miru）")
    assert text is not None
    assert _units(text) <= 4096
    assert text.endswith(f"<i>{NON_ADVICE_FOOTER}</i>")
    assert text.startswith("<b>📋 清單</b>")
    for tag in ("b", "i", "blockquote"):
        assert len(re.findall(rf"<{tag}[ >]", text)) == len(re.findall(rf"</{tag}>", text))
    assert not re.search(r"&(?![a-z]+;)", text)
    assert ("完整內容請見 Miru" in text) is (rows > 5)


def test_plain_text_is_sent_when_the_first_block_alone_cannot_fit() -> None:
    layout = Layout(blocks=(LayoutBlock(head="字" * 5000),))
    assert fit_html(layout, NON_ADVICE_FOOTER, pointer="…") is None
    notification = DeliveryNotification(
        notification_id="n1", title="t", body="字" * 5000 + f"\n{NON_ADVICE_FOOTER}", layout=layout
    )
    payload = _telegram_json(notification)
    assert "parse_mode" not in payload
    assert _units(str(payload["text"])) <= 4096
    assert str(payload["text"]).endswith(NON_ADVICE_FOOTER)


_LINE_LIMIT = 5000
_LINE_RECIPIENT = DeliveryRecipient(user_id="u1", line_user_id="Uabc")


def _line_text(notification: DeliveryNotification) -> str:
    channel = LineMessagingChannel(Settings(line_messaging_channel_access_token="tok"))
    messages = channel.build_request(notification, _LINE_RECIPIENT).json["messages"]
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["type"] == "text"
    return str(messages[0]["text"])


def _with_layout(layout: Layout) -> DeliveryNotification:
    return DeliveryNotification(
        notification_id="n1", title="t", body=f"純文字\n{NON_ADVICE_FOOTER}", layout=layout
    )


def test_line_sends_the_layout_as_markup_free_text_with_blocks_apart() -> None:
    notification = _layout_notification(name="A&B <C>")
    assert notification.layout is not None
    text = _line_text(notification)
    assert text == render_text(notification.layout, NON_ADVICE_FOOTER)
    assert text.startswith("🔔 4916 A&B <C>\n09:30 條件全數成立\n• 量比 14.25\n\n📖 名詞\n")
    assert text.endswith(f"\n\n{NON_ADVICE_FOOTER}")
    assert "<b>" not in text and "&amp;" not in text


def test_line_without_a_layout_keeps_the_title_and_body_text() -> None:
    assert _line_text(_notification()) == f"排程摘要\n{_notification().body}"


def test_line_shows_a_folded_block_as_a_normal_one_cut_to_a_few_lines() -> None:
    definitions = tuple(f"定義 {i}" for i in range(6))
    layout = Layout(
        blocks=(
            LayoutBlock(head="🔔 標題", lines=("一行",)),
            LayoutBlock(head="📖 名詞", lines=definitions, fold=True),
        )
    )
    text = _line_text(_with_layout(layout))
    assert "定義 0\n定義 1\n定義 2\n…（其餘請見 Miru）" in text
    assert "定義 3" not in text
    short = Layout(blocks=(LayoutBlock(head="📖 名詞", lines=definitions[:3], fold=True),))
    assert "其餘請見" not in _line_text(_with_layout(short))


@pytest.mark.parametrize("rows", [5, 400, 2000])
def test_a_trimmed_line_message_keeps_its_limit_first_block_and_footer(rows: int) -> None:
    text = _line_text(_with_layout(_long_layout(rows)))
    assert _units(text) <= _LINE_LIMIT
    assert text.startswith("📋 清單\n市場寬度 35%")
    assert text.endswith(f"\n\n{NON_ADVICE_FOOTER}")
    assert ("…（完整內容請見 Miru）" in text) is (rows > 5)
    if rows > 5:
        assert re.search(r"\n[^\n]+\n\n…（完整內容請見 Miru）\n\n", text)


def test_line_counts_utf16_units_so_emoji_cost_two() -> None:
    layout = Layout(
        blocks=(LayoutBlock(head="🔔 標題"), LayoutBlock(lines=tuple("📈" * 40 for _ in range(60))))
    )
    text = _line_text(_with_layout(layout))
    assert _units(text) <= _LINE_LIMIT
    assert len(text) < _LINE_LIMIT - 100  # code points alone would have fit more lines
    assert text.endswith(NON_ADVICE_FOOTER)


def test_a_line_message_exactly_at_the_limit_is_untouched() -> None:
    fixed = _units(f"🔔\n\n\n\n{NON_ADVICE_FOOTER}")
    layout = Layout(
        blocks=(LayoutBlock(head="🔔"), LayoutBlock(lines=("字" * (_LINE_LIMIT - fixed),)))
    )
    text = _line_text(_with_layout(layout))
    assert _units(text) == _LINE_LIMIT
    assert "完整內容請見" not in text
    over = Layout(
        blocks=(LayoutBlock(head="🔔"), LayoutBlock(lines=("字" * (_LINE_LIMIT - fixed + 1),)))
    )
    assert "完整內容請見" in _line_text(_with_layout(over))


def test_line_cuts_a_long_plain_text_at_a_line_and_keeps_the_footer() -> None:
    lines = [f"第 {i} 行 📈 台積電外資買超連 3 日" for i in range(900)]
    notification = DeliveryNotification(
        notification_id="n1", title="標題", body="\n".join(lines) + f"\n{NON_ADVICE_FOOTER}"
    )
    text = _line_text(notification)
    assert _units(text) <= _LINE_LIMIT
    assert text.endswith(f"{_POINTER}\n{NON_ADVICE_FOOTER}")
    kept = text.removesuffix(f"\n{_POINTER}\n{NON_ADVICE_FOOTER}").split("\n")[1:]
    assert kept == lines[: len(kept)]


def test_line_falls_back_to_plain_text_when_the_first_block_alone_cannot_fit() -> None:
    layout = Layout(blocks=(LayoutBlock(head="字" * 6000),))
    notification = DeliveryNotification(
        notification_id="n1", title="t", body="字" * 6000 + f"\n{NON_ADVICE_FOOTER}", layout=layout
    )
    text = _line_text(notification)
    assert _units(text) <= _LINE_LIMIT
    assert text.endswith(NON_ADVICE_FOOTER)
