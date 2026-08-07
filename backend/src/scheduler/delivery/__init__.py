"""Notification delivery channels — one config-selected impl per channel behind the
`DeliveryChannel` protocol."""

from __future__ import annotations

from scheduler.delivery.base import (
    DeliveryChannel,
    DeliveryNotification,
    DeliveryReceipt,
    DeliveryRecipient,
)
from scheduler.delivery.channels import (
    ChannelNotConfiguredError,
    EmailChannel,
    InAppChannel,
    LineMessagingChannel,
    OutboundRequest,
    PushChannel,
    TelegramChannel,
    build_default_channels,
)

__all__ = [
    "ChannelNotConfiguredError",
    "DeliveryChannel",
    "DeliveryNotification",
    "DeliveryReceipt",
    "DeliveryRecipient",
    "EmailChannel",
    "InAppChannel",
    "LineMessagingChannel",
    "OutboundRequest",
    "PushChannel",
    "TelegramChannel",
    "build_default_channels",
]
