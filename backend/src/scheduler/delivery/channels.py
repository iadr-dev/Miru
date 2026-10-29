"""Concrete `DeliveryChannel` implementations, one per channel.

Every external channel follows the same two-step shape: `build_request` shapes the exact provider
request (endpoint, headers, body — real and unit-testable with no socket), then `send` hands it to
an injected transport. A channel with no credential configured reports a no-network success, so a
development profile with no provider accounts behaves exactly as before; a configured channel
really sends, and maps the provider's answer to a receipt (`retryable` for a network failure, 429
or 5xx; permanent for a 4xx such as a blocked bot or a stale device token).

- **in-app** — the durable `notifications` row the worker writes *is* the delivery.
- **LINE / Telegram** — one JSON POST to the provider's bot API.
- **email** — Resend's HTTP API, with the notification id as the idempotency key.
- **push** — browser Web Push (VAPID), one encrypted message per subscribed browser.

Secrets discipline: channel credentials and recipient addresses/tokens are never bound into a log
call. A channel logs only its kind and a boolean outcome.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable
from urllib.parse import quote

import structlog

from config.settings import Settings, reveal
from scheduler.delivery.base import (
    DeliveryChannel,
    DeliveryNotification,
    DeliveryReceipt,
    DeliveryRecipient,
)
from scheduler.delivery.layout import (
    LINE_MAX_UNITS,
    TELEGRAM_MAX_UNITS,
    fit_html,
    fit_text,
    utf16_units,
)
from scheduler.delivery.push_endpoint import subscription_endpoint_allowed
from scheduler.delivery.transport import (
    HttpxTransport,
    OutboundTransport,
    PywebpushSender,
    TransportResult,
    WebPushSender,
)
from scheduler.models import DeliveryChannelKind
from schemas.alerts import NON_ADVICE_FOOTER

logger = structlog.get_logger(__name__)

_JSON_CONTENT_TYPE = "application/json"
_NO_NETWORK = "no-network profile"


class ChannelNotConfiguredError(RuntimeError):
    """A channel was asked to send but its credential/recipient is missing — surfaced as a
    non-retryable receipt by the worker, never a crash."""


class OutboundRequest:
    """The fully-shaped request a transport sends. `headers`/`json` carry credentials; the channel
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


def _text(notification: DeliveryNotification) -> str:
    return f"{notification.title}\n{notification.body}".strip()


def _line_text(notification: DeliveryNotification) -> str:
    """The LINE text: the layout as markup-free text when there is one (and it fits), else the
    title and body, either way within LINE's 5000 characters."""
    laid_out = (
        fit_text(notification.layout, NON_ADVICE_FOOTER, pointer=_TRIM_POINTER)
        if notification.layout is not None
        else None
    )
    return laid_out if laid_out is not None else _fit_plain(_text(notification), LINE_MAX_UNITS)


def _receipt(
    kind: DeliveryChannelKind, result: TransportResult, *, address_gone: bool = False
) -> DeliveryReceipt:
    logger.info(
        "delivery_sent",
        channel=kind.value,
        ok=result.ok,
        status=result.status,
        address_gone=address_gone,
    )
    return DeliveryReceipt(
        channel=kind,
        delivered=result.ok,
        retryable=result.retryable,
        provider_message_id=result.message_id,
        detail=result.detail,
        address_gone=address_gone,
    )


def _unaddressable(kind: DeliveryChannelKind, exc: ChannelNotConfiguredError) -> DeliveryReceipt:
    return DeliveryReceipt(channel=kind, delivered=False, retryable=False, detail=str(exc))


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


_OPEN_IN_APP = "在 Miru 開啟"
_TRIM_POINTER = "…（完整內容請見 Miru）"


def _fit_plain(text: str, limit: int) -> str:
    """`text` as one message within `limit` UTF-16 code units (an emoji is two; Telegram allows
    4096, LINE 5000). A longer text is cut at a line boundary, then a pointer to the full content
    and the non-advice footer, which always survives the cut, are appended."""
    if utf16_units(text) <= limit:
        return text
    has_footer = NON_ADVICE_FOOTER in text
    head = text.replace(NON_ADVICE_FOOTER, "").rstrip() if has_footer else text
    tail = f"\n{_TRIM_POINTER}\n{NON_ADVICE_FOOTER}" if has_footer else f"\n{_TRIM_POINTER}"
    budget = limit - utf16_units(tail)
    kept: list[str] = []
    used = 0
    for line in head.split("\n"):
        cost = utf16_units(line) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    if not kept:
        kept.append(_cut_units(head, budget))
    return "\n".join(kept).rstrip() + tail


def _cut_units(text: str, budget: int) -> str:
    """The longest prefix of `text` within `budget` UTF-16 units, never splitting a character."""
    used = 0
    for index, char in enumerate(text):
        used += utf16_units(char)
        if used > budget:
            return text[:index]
    return text


class LineMessagingChannel:
    """LINE Messaging API push. Distinct credential from LINE Login — this is the outbound
    Messaging channel access token, never the sign-in channel secret. One text message: a
    notification with a `layout` goes as its markup-free text, anything else as title and body,
    both cut to LINE's 5000-character limit."""

    kind = DeliveryChannelKind.LINE
    _PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, settings: Settings, transport: OutboundTransport | None = None) -> None:
        self._token = reveal(settings.line_messaging_channel_access_token)
        self._transport = transport

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
                "messages": [{"type": "text", "text": _line_text(notification)}],
            },
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        return await _post_json_send(
            self, self._transport, notification, recipient, configured=bool(self._token)
        )


_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_CHAT_NOT_FOUND = "chat not found"
_PARSE_ERROR = "parse entities"


def _never_gone(result: TransportResult) -> bool:
    del result
    return False


def _telegram_chat_gone(result: TransportResult) -> bool:
    """Telegram answers 403 when the user blocked or deleted the bot (or was deactivated) and 400
    "chat not found" when the chat no longer exists. Neither ever recovers on its own; a 429, a 5xx
    or a network failure says nothing about the chat and never counts."""
    if result.ok or result.retryable:
        return False
    if result.status == _HTTP_FORBIDDEN:
        return True
    return result.status == _HTTP_BAD_REQUEST and _CHAT_NOT_FOUND in result.description.lower()


def _no_retry(result: TransportResult) -> OutboundRequest | None:
    del result
    return None


def _markup_rejected(result: TransportResult) -> bool:
    return (
        not result.ok
        and result.status == _HTTP_BAD_REQUEST
        and _PARSE_ERROR in result.description.lower()
    )


class TelegramChannel:
    """One `sendMessage`. A notification with a `layout` goes as HTML; if Telegram rejects the
    markup ("can't parse entities") the same message is sent once more as plain text, so a
    formatting fault can never cost the user the notification."""

    kind = DeliveryChannelKind.TELEGRAM

    def __init__(self, settings: Settings, transport: OutboundTransport | None = None) -> None:
        self._token = reveal(settings.telegram_bot_token)
        self._transport = transport

    def build_request(
        self,
        notification: DeliveryNotification,
        recipient: DeliveryRecipient,
        *,
        formatted: bool = True,
    ) -> OutboundRequest:
        if recipient.telegram_chat_id is None:
            raise ChannelNotConfiguredError("no Telegram chat id for recipient")
        html_text = (
            fit_html(notification.layout, NON_ADVICE_FOOTER, pointer=_TRIM_POINTER)
            if formatted and notification.layout is not None
            else None
        )
        payload: dict[str, object] = {
            "chat_id": recipient.telegram_chat_id,
            "text": html_text
            if html_text is not None
            else _fit_plain(_text(notification), TELEGRAM_MAX_UNITS),
        }
        if html_text is not None:
            payload["parse_mode"] = "HTML"
        if recipient.app_url is not None:
            button = {
                "text": _OPEN_IN_APP,
                "url": f"{recipient.app_url.rstrip('/')}{_push_url(notification.deep_link)}",
            }
            payload["reply_markup"] = {"inline_keyboard": [[button]]}
        return OutboundRequest(
            method="POST",
            url=f"https://api.telegram.org/bot{self._token or ''}/sendMessage",
            headers={"Content-Type": _JSON_CONTENT_TYPE},
            json=payload,
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        def plain_retry(result: TransportResult) -> OutboundRequest | None:
            if notification.layout is None or not _markup_rejected(result):
                return None
            logger.warning("telegram_markup_rejected")
            return self.build_request(notification, recipient, formatted=False)

        return await _post_json_send(
            self,
            self._transport,
            notification,
            recipient,
            configured=bool(self._token),
            address_gone=_telegram_chat_gone,
            retry=plain_retry,
        )


class EmailChannel:
    """Email through Resend's HTTP API (`POST /emails`). It is a transactional message — one the
    user asked for by setting an alert or schedule — so it carries a plain-text body plus a small
    accessible HTML copy, and the non-advice footer. The notification id is the `Idempotency-Key`,
    so a retried send after a timeout can never deliver the same email twice."""

    kind = DeliveryChannelKind.EMAIL
    _URL = "https://api.resend.com/emails"

    def __init__(self, settings: Settings, transport: OutboundTransport | None = None) -> None:
        self._key = reveal(settings.resend_api_key)
        self._from = settings.email_from_address
        self._transport = transport

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest:
        if recipient.email_address is None:
            raise ChannelNotConfiguredError("no email address for recipient")
        text = (
            notification.body
            if NON_ADVICE_FOOTER in notification.body
            else f"{notification.body}\n\n{NON_ADVICE_FOOTER}"
        )
        link = recipient.unsubscribe_url
        if link is not None:
            text = f"{text}\n\n不想再收到電子郵件通知？一鍵取消：{link}"
        payload: dict[str, object] = {
            "from": self._from or "",
            "to": [recipient.email_address],
            "subject": notification.title,
            "text": text,
            "html": _email_html(notification.title, text),
        }
        if link is not None:
            # RFC 8058: the mail client shows its own "unsubscribe" button and POSTs to the link.
            payload["headers"] = {
                "List-Unsubscribe": f"<{link}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        return OutboundRequest(
            method="POST",
            url=self._URL,
            headers={
                "Authorization": f"Bearer {self._key or ''}",
                "Content-Type": _JSON_CONTENT_TYPE,
                "Idempotency-Key": notification.notification_id,
            },
            json=payload,
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        return await _post_json_send(
            self,
            self._transport,
            notification,
            recipient,
            configured=bool(self._key and self._from),
        )


def _email_html(title: str, text: str) -> str:
    """A minimal, accessible HTML copy: declared language, a <title>, real paragraphs, no images
    and no colour-only meaning — it reads the same as the plain-text part."""
    paragraphs = "".join(
        f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>"
        for block in text.split("\n\n")
        if block.strip()
    )
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1>{paragraphs}</body></html>"
    )


class PushChannel:
    """Browser notifications via Web Push. A user's registered web devices are JSON-encoded
    `PushSubscription`s; one encrypted push is sent to each. The receipt is delivered when at least
    one browser accepted it, retryable only when none did and a failure was transient, and it lists
    the subscriptions the push service reported gone so the worker can prune them."""

    kind = DeliveryChannelKind.PUSH

    def __init__(self, settings: Settings, sender: WebPushSender | None = None) -> None:
        self._configured = bool(settings.vapid_public_key and settings.vapid_private_key)
        self._sender = sender

    @staticmethod
    def build_payload(notification: DeliveryNotification) -> str:
        """The JSON the service worker shows as a notification: short text plus the tap target."""
        return json.dumps(
            {
                "title": notification.title,
                "body": notification.body,
                "url": _push_url(notification.deep_link),
                "id": notification.notification_id,
            },
            ensure_ascii=False,
        )

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        if not recipient.device_tokens:
            return _unaddressable(self.kind, ChannelNotConfiguredError("no device tokens"))
        if not (self._configured and self._sender):
            logger.info("delivery_shaped", channel=self.kind.value, configured=False)
            return DeliveryReceipt(channel=self.kind, delivered=True, detail=_NO_NETWORK)
        payload = self.build_payload(notification)
        results: list[TransportResult] = []
        stale: list[str] = []
        for token in recipient.device_tokens:
            subscription = _parse_subscription(token)
            if subscription is None:
                stale.append(token)  # not a subscription at all (e.g. a leftover native token)
                continue
            result = await self._sender.send(subscription=subscription, payload=payload)
            results.append(result)
            if result.gone:
                stale.append(token)
        accepted = [r for r in results if r.ok]
        logger.info(
            "delivery_sent", channel=self.kind.value, ok=bool(accepted), devices=len(results)
        )
        return DeliveryReceipt(
            channel=self.kind,
            delivered=bool(accepted),
            retryable=not accepted and any(r.retryable for r in results),
            detail="" if accepted else "no device accepted",
            stale_tokens=tuple(stale),
        )


def _parse_subscription(token: str) -> dict[str, object] | None:
    """The subscription, or None if the token is not one — including an endpoint that is not an
    allow-listed push service (checked again here so a row stored before the registration check, or
    written some other way, can never make the server call an arbitrary URL)."""
    if not subscription_endpoint_allowed(token):
        return None
    parsed = json.loads(token)
    if isinstance(parsed, dict) and parsed.get("keys"):
        return cast("dict[str, object]", parsed)
    return None


_LINK_ROUTES = {"stock": "/stock/{detail}", "thread": "/chat?thread={detail}", "watch": "/watch"}


def _push_url(deep_link: dict[str, object]) -> str:
    """Where a tap on the browser notification goes (locale-less; the app resolves the prefix)."""
    route = deep_link.get("route")
    detail = deep_link.get("symbol") or deep_link.get("thread_id")
    template = _LINK_ROUTES.get(str(route))
    if template is not None and "{detail}" not in template:
        return template
    if template is not None and detail:
        return template.format(detail=quote(str(detail), safe=""))
    return "/settings/alerts"


@runtime_checkable
class _ShapedChannel(Protocol):
    """Structural type: any channel exposing `kind` + `build_request`."""

    kind: DeliveryChannelKind

    def build_request(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> OutboundRequest: ...


async def _post_json_send(  # noqa: PLR0913 — one argument per send dimension
    channel: _ShapedChannel,
    transport: OutboundTransport | None,
    notification: DeliveryNotification,
    recipient: DeliveryRecipient,
    *,
    configured: bool,
    address_gone: Callable[[TransportResult], bool] = _never_gone,
    retry: Callable[[TransportResult], OutboundRequest | None] = _no_retry,
) -> DeliveryReceipt:
    """Shared send path for the JSON-POST channels: build the real request (validating that the
    recipient is addressable), then send it — once more if `retry` turns the first answer into a
    different request — or, with no credential or transport, report a no-network success so an
    unconfigured profile is not a failure."""
    try:
        request = channel.build_request(notification, recipient)
    except ChannelNotConfiguredError as exc:
        return _unaddressable(channel.kind, exc)
    if not (configured and transport is not None):
        logger.info("delivery_shaped", channel=channel.kind.value, configured=configured)
        return DeliveryReceipt(channel=channel.kind, delivered=True, detail=_NO_NETWORK)
    result = await transport.post_json(request.url, headers=request.headers, body=request.json)
    again = retry(result)
    if again is not None:
        result = await transport.post_json(again.url, headers=again.headers, body=again.json)
    return _receipt(channel.kind, result, address_gone=address_gone(result))


def build_default_channels(
    settings: Settings, *, live: bool = False
) -> dict[DeliveryChannelKind, DeliveryChannel]:
    """The config-selected channel set the worker dispatches through. `live=True` (the scheduler
    daemon) wires the real transports; everything else — tests, the API process — keeps the
    no-network behaviour, so constructing channels never opens a socket by accident."""
    transport = HttpxTransport() if live else None
    web_push: WebPushSender | None = None
    if live and settings.vapid_private_key and settings.vapid_public_key:
        web_push = PywebpushSender(
            private_key=settings.vapid_private_key.get_secret_value(),
            subject=settings.vapid_subject,
        )
    return {
        DeliveryChannelKind.IN_APP: InAppChannel(),
        DeliveryChannelKind.LINE: LineMessagingChannel(settings, transport),
        DeliveryChannelKind.TELEGRAM: TelegramChannel(settings, transport),
        DeliveryChannelKind.EMAIL: EmailChannel(settings, transport),
        DeliveryChannelKind.PUSH: PushChannel(settings, web_push),
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
