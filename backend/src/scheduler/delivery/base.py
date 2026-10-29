"""The `DeliveryChannel` protocol + the notification/receipt shapes every channel shares.

Same config-selected, protocol-plus-impl idiom as `SourceAdapter`/`LLMClient`/`Transcriber`:
one impl per channel, chosen by config; adding a channel is one new impl with no scheduler
change. A channel only knows how to send one payload to one recipient — quiet-hours, daily
limits, dedupe, and quota all live upstream in the worker, never in a channel.

Security: a channel's credentials and a recipient's address/token are secrets or PII. They are
never bound into a structlog call raw — a channel logs only the channel kind and a boolean
outcome, never the token/address/body.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from scheduler.models import DeliveryChannelKind


class DeliveryNotification(BaseModel):
    """The channel-agnostic notification a `DeliveryChannel` renders. `deep_link` mirrors the
    native-push payload shape so a tap resolves to the right surface; non-push channels ignore
    it or embed it as a URL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notification_id: str
    title: str
    body: str = Field(min_length=1)
    deep_link: dict[str, object] = Field(default_factory=dict)


class DeliveryRecipient(BaseModel):
    """Where to send. Exactly one addressing field is populated per channel; the worker resolves
    it from the user's linked identities / preferences / device tokens before calling `send`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    line_user_id: str | None = None
    telegram_chat_id: str | None = None
    email_address: str | None = None
    # One-click unsubscribe link for the email channel (None when no HTTPS public URL is set).
    unsubscribe_url: str | None = None
    device_tokens: tuple[str, ...] = Field(default_factory=tuple)


class DeliveryReceipt(BaseModel):
    """The outcome of one `send`. `delivered=False` with `retryable=True` is what routes a
    transient channel failure to retry/DLQ upstream; a stub/no-network channel reports
    `delivered=True` with `provider_message_id=None`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: DeliveryChannelKind
    delivered: bool
    retryable: bool = False
    provider_message_id: str | None = None
    detail: str = ""
    # Device tokens the provider reported as permanently invalid; the worker unregisters them.
    stale_tokens: tuple[str, ...] = ()
    # The provider says this recipient can never be reached again (a blocked bot, a deleted chat):
    # the worker unlinks the channel so the account stops claiming it works.
    address_gone: bool = False


@runtime_checkable
class DeliveryChannel(Protocol):
    kind: DeliveryChannelKind

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt: ...


__all__ = [
    "DeliveryChannel",
    "DeliveryNotification",
    "DeliveryReceipt",
    "DeliveryRecipient",
]
