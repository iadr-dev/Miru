"""Delivery-channel behaviour: in-app always delivers; the external channels build a real,
correctly-shaped request and report a no-network success in the default profile; a missing
recipient address is a non-retryable receipt, never a crash."""

from __future__ import annotations

from config.settings import Settings
from scheduler.delivery.base import DeliveryNotification, DeliveryRecipient
from scheduler.delivery.channels import (
    EmailChannel,
    InAppChannel,
    LineMessagingChannel,
    PushChannel,
    TelegramChannel,
)
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


async def test_push_carries_deep_link() -> None:
    channel = PushChannel(Settings(fcm_service_account_json="{}"))
    notification = DeliveryNotification(
        notification_id="n2",
        title="提醒",
        body="2330 觀察",
        deep_link={"route": "stock", "symbol": "2330"},
    )
    request = channel.build_request(
        notification, DeliveryRecipient(user_id="u1", device_tokens=("dtok",))
    )
    assert request.json["data"]["deep_link"]["symbol"] == "2330"  # type: ignore[index]


async def test_missing_recipient_is_non_retryable_not_a_crash() -> None:
    channel = EmailChannel(Settings(email_smtp_host="smtp.example.test"))
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
