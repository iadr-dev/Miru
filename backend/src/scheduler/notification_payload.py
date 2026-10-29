"""The typed shape the inbox serves for a notification's stored payload.

The row keeps an open JSON object (`title`, `body`, `deep_link`, optionally `held` and `layout`).
`read_payload` is the one place that object becomes a model, field by field: a missing or
mistyped field takes its default and a stored `layout` that no longer validates is dropped, so the
bell then draws the plain body instead of failing. A layout is served with the non-advice footer
it renders under, so the web app never carries governance copy of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from scheduler.delivery.layout import Layout
from schemas.alerts import NON_ADVICE_FOOTER


class DeepLink(BaseModel):
    """Where opening the notification goes. Each route is one the web app has a page for."""

    model_config = ConfigDict(frozen=True)

    route: Literal["stock", "thread", "watch", "notifications"]
    symbol: str | None = None
    strategy: str | None = None
    thread_id: str | None = None


class NotificationPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = ""
    body: str = ""
    deep_link: DeepLink | None = None
    held: bool = False
    layout: Layout | None = None
    footer: str = ""


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def _deep_link(value: object) -> DeepLink | None:
    if not isinstance(value, dict):
        return None
    try:
        return DeepLink.model_validate(value)
    except ValidationError:
        return None


def read_layout(value: object) -> Layout | None:
    if not isinstance(value, dict):
        return None
    try:
        return Layout.model_validate(value)
    except ValidationError:
        return None


def read_payload(raw: Mapping[str, object]) -> NotificationPayload:
    layout = read_layout(raw.get("layout"))
    return NotificationPayload(
        title=_text(raw, "title"),
        body=_text(raw, "body"),
        deep_link=_deep_link(raw.get("deep_link")),
        held=raw.get("held") is True,
        layout=layout,
        footer=NON_ADVICE_FOOTER if layout is not None else "",
    )


__all__ = ["DeepLink", "NotificationPayload", "read_layout", "read_payload"]
