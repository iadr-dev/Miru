"""Delivery-channel behaviour: in-app always delivers; the external channels build a real,
correctly-shaped request and report a no-network success in the default profile; a missing
recipient address is a non-retryable receipt, never a crash."""

from __future__ import annotations

import json

from config.settings import Settings
from scheduler.delivery.base import DeliveryNotification, DeliveryRecipient
from scheduler.delivery.channels import (
    EmailChannel,
    InAppChannel,
    LineMessagingChannel,
    PushChannel,
    TelegramChannel,
)
from scheduler.delivery.transport import TransportResult
from scheduler.models import DeliveryChannelKind


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
