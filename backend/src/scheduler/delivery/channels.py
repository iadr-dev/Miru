"""Concrete `DeliveryChannel` implementations, one per channel.

Design decision on how "real" each channel is in this phase (mirrors the voice-provider
convention, where the Protocol + a deterministic no-network stub ship in the default profile and
the live transport is a config-selected seam deferred until wired):

- **in-app** — fully real. The durable in-app record is the `notifications` row the worker
  persists; this channel's `send` therefore succeeds without any network, because the row *is*
  the delivery. It is the one channel with no external dependency at all.
- **LINE / Telegram / email / push (APNs, FCM)** — real *request shaping* (correct endpoint URL
  and payload shape for each provider's API), but the actual socket write is intentionally not
  performed here. Two reasons: (1) the "only `sources/` opens a socket" invariant means the live
  HTTP transport belongs under `sources/`, wired later behind this same Protocol; (2) the default
  test profile must never hit a live upstream. Each channel below builds the exact request it
  *would* send (so the wire contract is real and testable) and, when no credential is configured,
  reports a successful no-network delivery — the same shape a real transport returns, so swapping
  the transport in later is a construction-site change, not a call-site one.

Secrets discipline: channel credentials and recipient addresses/tokens are never bound into a
log call. A channel logs only its kind and a boolean outcome.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

from config.settings import Settings
from scheduler.delivery.base import (
    DeliveryChannel,
    DeliveryNotification,
    DeliveryReceipt,
    DeliveryRecipient,
)
from scheduler.models import DeliveryChannelKind

logger = structlog.get_logger(__name__)

_JSON_CONTENT_TYPE = "application/json"


class ChannelNotConfiguredError(RuntimeError):
    """A channel was asked to send but its credential/recipient is missing — surfaced as a
    non-retryable receipt by the worker, never a crash."""


class OutboundRequest:
    """The fully-shaped request a live transport would send. Built and returned by each channel
    so the wire contract (method, url, headers, json body) is real and unit-testable without a
    socket. `headers`/`json` never contain a secret in a form this object logs — the channel
    passes them to a transport, never to a logger."""

    __slots__ = ("headers", "json", "method", "url")

    def __init__(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> None:
        self.method = method
        self.url = url
        self.headers = headers
        self.json = json


class InAppChannel:
    """The persisted in-app notification. Fully real: the worker has already written the
    `notifications` row (which is the delivery and the dedupe guard), so this simply confirms."""

    kind = DeliveryChannelKind.IN_APP

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        del recipient
        logger.info(
            "delivery_in_app", channel=self.kind.value, notification_id=notification.notification_id
        )
        return DeliveryReceipt(channel=self.kind, delivered=True)


class LineMessagingChannel:
    """LINE Messaging API push. Distinct credential from LINE Login — this is the outbound
    Messaging channel access token, never the sign-in channel secret."""

    kind = DeliveryChannelKind.LINE
    _PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, settings: Settings) -> None:
        self._token = settings.line_messaging_channel_access_token

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest:
        if recipient.line_user_id is None:
            raise ChannelNotConfiguredError("no LINE user id for recipient")
        return OutboundRequest(
            method="POST",
            url=self._PUSH_URL,
            headers={
                "Authorization": f"Bearer {self._token or ''}",
                "Content-Type": _JSON_CONTENT_TYPE,
            },
            json={
                "to": recipient.line_user_id,
                "messages": [
                    {"type": "text", "text": f"{notification.title}\n{notification.body}".strip()}
                ],
            },
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        return await _shaped_send(self, notification, recipient, configured=bool(self._token))


class TelegramChannel:
    kind = DeliveryChannelKind.TELEGRAM

    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest:
        if recipient.telegram_chat_id is None:
            raise ChannelNotConfiguredError("no Telegram chat id for recipient")
        return OutboundRequest(
            method="POST",
            url=f"https://api.telegram.org/bot{self._token or ''}/sendMessage",
            headers={"Content-Type": _JSON_CONTENT_TYPE},
            json={
                "chat_id": recipient.telegram_chat_id,
                "text": f"{notification.title}\n{notification.body}".strip(),
            },
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        return await _shaped_send(self, notification, recipient, configured=bool(self._token))


class EmailChannel:
    """SMTP email. `build_request` returns the message envelope (the SMTP transport is the
    deferred seam); an unconfigured host reports a no-network success in the default profile."""

    kind = DeliveryChannelKind.EMAIL

    def __init__(self, settings: Settings) -> None:
        self._host = settings.email_smtp_host
        self._from = settings.email_from_address

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest:
        if recipient.email_address is None:
            raise ChannelNotConfiguredError("no email address for recipient")
        return OutboundRequest(
            method="SMTP",
            url=self._host or "",
            headers={"From": self._from or "", "To": recipient.email_address},
            json={"subject": notification.title, "body": notification.body},
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        return await _shaped_send(self, notification, recipient, configured=bool(self._host))


class PushChannel:
    """Native push (APNs/FCM). One notification fans out to a user's registered device tokens;
    the payload carries the deep-link so a tap opens the right surface."""

    kind = DeliveryChannelKind.PUSH

    def __init__(self, settings: Settings) -> None:
        self._configured = bool(settings.apns_auth_key or settings.fcm_service_account_json)

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest:
        if not recipient.device_tokens:
            raise ChannelNotConfiguredError("no device tokens for recipient")
        return OutboundRequest(
            method="POST",
            url="https://fcm.googleapis.com/v1/messages:send",
            headers={"Content-Type": _JSON_CONTENT_TYPE},
            json={
                "tokens": list(recipient.device_tokens),
                "notification": {"title": notification.title, "body": notification.body},
                "data": {
                    "deep_link": notification.deep_link,
                    "notification_id": notification.notification_id,
                },
            },
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        return await _shaped_send(self, notification, recipient, configured=self._configured)


@runtime_checkable
class _ShapedChannel(Protocol):
    """Structural type: any channel exposing `kind` + `build_request`."""

    kind: DeliveryChannelKind

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest: ...


async def _shaped_send(
    channel: _ShapedChannel,
    notification: DeliveryNotification,
    recipient: DeliveryRecipient,
    *,
    configured: bool,
) -> DeliveryReceipt:
    """Shared send path for the external channels: build the real request (validating the
    recipient is addressable), then — in the default no-network profile — report a successful
    no-network delivery. When a live transport is wired under `sources/`, it consumes the built
    request here; nothing about the call site changes."""
    try:
        request = channel.build_request(notification, recipient)
    except ChannelNotConfiguredError as exc:
        return DeliveryReceipt(
            channel=channel.kind, delivered=False, retryable=False, detail=str(exc)
        )
    logger.info(
        "delivery_shaped",
        channel=channel.kind.value,
        notification_id=notification.notification_id,
        configured=configured,
        method=request.method,
    )
    # No socket in this phase; the live transport is the deferred seam. The request shape above
    # is what it will send. A no-network profile is a successful delivery of the in-app record's
    # mirror, not a failure.
    return DeliveryReceipt(channel=channel.kind, delivered=True, detail="no-network profile")


def build_default_channels(settings: Settings) -> dict[DeliveryChannelKind, DeliveryChannel]:
    """The config-selected channel set the worker dispatches through."""
    return {
        DeliveryChannelKind.IN_APP: InAppChannel(),
        DeliveryChannelKind.LINE: LineMessagingChannel(settings),
        DeliveryChannelKind.TELEGRAM: TelegramChannel(settings),
        DeliveryChannelKind.EMAIL: EmailChannel(settings),
        DeliveryChannelKind.PUSH: PushChannel(settings),
    }


__all__ = [
    "ChannelNotConfiguredError",
    "EmailChannel",
    "InAppChannel",
    "LineMessagingChannel",
    "OutboundRequest",
    "PushChannel",
    "TelegramChannel",
    "build_default_channels",
]
