"""Delivery channels: Telegram/LINE linking, devices, and the inbound channel webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from accounts.models import User
from api.dependencies import (
    AppState,
    get_state,
    require_user,
    system_scope,
)
from api.errors import ApiError
from api.routers.scheduler._common import HTTP_422_UNPROCESSABLE as _HTTP_422_UNPROCESSABLE
from api.routers.scheduler._common import store as _store
from config.settings import reveal
from scheduler.delivery.push_endpoint import subscription_endpoint_allowed
from scheduler.delivery.transport import HttpxTransport, OutboundTransport
from scheduler.models import (
    DevicePlatform,
    DevicePushToken,
    EmailBlockReason,
)
from scheduler.store import (
    SchedulerStore,
)

router = APIRouter()


_LINK_TOKEN_TTL = timedelta(minutes=10)


_TELEGRAM = "telegram"


_LINE = "line"


class ChannelStatus(BaseModel):
    """Which external channels can actually reach this user right now."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: bool
    push: bool
    line: bool
    telegram: bool
    telegram_available: bool
    # LINE: `line` = a message can be pushed now; `line_linked` = the user linked this account by
    # sending the official account a code; `line_available` = the server has the bot credentials.
    line_linked: bool = False
    line_available: bool = False
    # Why this account's address is not emailed: 'bounced' | 'complained' | 'unsubscribed'.
    email_blocked: EmailBlockReason | None = None
    # Browser notifications: the VAPID public key the browser subscribes with (null = not set up),
    # and the LINE official account's add-friend link (null = not set up).
    web_push_public_key: str | None = None
    line_add_friend_url: str | None = None


def _real_from_address(value: str | None) -> bool:
    """A from-address that can actually send: present, and not on a reserved `.example` domain (the
    placeholder in `.env.example`), which no mail provider will ever verify."""
    if not value or "@" not in value:
        return False
    domain = value.rsplit("@", 1)[1].strip(" >").lower()
    return bool(domain) and not domain.endswith(".example")


@router.get("/me/channels")
async def get_channel_status(
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ChannelStatus:
    settings = state.settings
    blocked = await store.email_suppression_reason(address=user.email) if user.email else None
    email_ready = bool(
        user.email
        and settings.resend_api_key
        and _real_from_address(settings.email_from_address)
        and blocked is None
    )
    return ChannelStatus(
        email=email_ready,
        email_blocked=blocked,
        push=bool(settings.vapid_private_key and await store.list_devices(user_id=user.id)),
        line=bool(
            settings.line_messaging_channel_access_token
            and await store.linked_line_user_id(user_id=user.id)
        ),
        telegram=await store.channel_address(user_id=user.id, channel=_TELEGRAM) is not None,
        line_linked=await store.channel_address(user_id=user.id, channel=_LINE) is not None,
        line_available=bool(
            settings.line_messaging_channel_access_token and settings.line_messaging_channel_secret
        ),
        telegram_available=bool(settings.telegram_bot_token and settings.telegram_bot_username),
        web_push_public_key=(settings.vapid_public_key if settings.vapid_private_key else None),
        line_add_friend_url=(
            f"https://line.me/R/ti/p/{settings.line_bot_basic_id}"
            if settings.line_messaging_channel_access_token and settings.line_bot_basic_id
            else None
        ),
    )


class TelegramLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    expires_in_seconds: int


@router.post("/me/channels/telegram/link")
async def start_telegram_link(
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> TelegramLink:
    """Issue a one-time link: the user opens it, taps Start in the bot, and the webhook below binds
    that chat to their account. Nothing is linked until they do."""
    username = state.settings.telegram_bot_username
    if not (state.settings.telegram_bot_token and username):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="channel_unavailable",
            message="Telegram delivery is not set up on this server.",
            retryable=False,
        )
    token = secrets.token_urlsafe(16)
    await store.start_channel_link(
        user_id=user.id,
        channel=_TELEGRAM,
        token=token,
        expires_at=datetime.now(UTC) + _LINK_TOKEN_TTL,
    )
    return TelegramLink(
        # `.env` may hold the handle as shown in Telegram ("@name"); the deep link wants it bare.
        url=f"https://t.me/{username.removeprefix('@')}?start={token}",
        expires_in_seconds=int(_LINK_TOKEN_TTL.total_seconds()),
    )


@router.delete("/me/channels/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_telegram(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unlink_channel(user_id=user.id, channel=_TELEGRAM)


_START_PREFIX = "/start "


_LINE_CODE = re.compile(r"\b([A-Z2-7]{8})\b")


_LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


_LINK_HELP = "請到 Miru 的「設定 → 通知」按「連結 LINE」，取得驗證碼後傳給我，就能收到提醒通知。"


class LineLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    add_friend_url: str | None
    expires_in_seconds: int


@router.post("/me/channels/line/link")
async def start_line_link(
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> LineLink:
    """Issue a one-time code. The user adds the official account and sends it the code; the
    webhook below binds the sender's LINE id to this account. Nothing is linked until they do."""
    settings = state.settings
    if not (
        settings.line_messaging_channel_access_token and settings.line_messaging_channel_secret
    ):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="channel_unavailable",
            message="LINE delivery is not set up on this server.",
            retryable=False,
        )
    code = base64.b32encode(secrets.token_bytes(5)).decode()  # 8 chars of A-Z2-7, 40 bits
    await store.start_channel_link(
        user_id=user.id,
        channel=_LINE,
        token=code,
        expires_at=datetime.now(UTC) + _LINK_TOKEN_TTL,
    )
    return LineLink(
        code=code,
        add_friend_url=(
            f"https://line.me/R/ti/p/{settings.line_bot_basic_id}"
            if settings.line_bot_basic_id
            else None
        ),
        expires_in_seconds=int(_LINK_TOKEN_TTL.total_seconds()),
    )


@router.delete("/me/channels/line", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_line(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unlink_channel(user_id=user.id, channel=_LINE)


def get_line_transport() -> OutboundTransport:
    """The transport the LINE webhook replies with (overridden in tests)."""
    return HttpxTransport()


async def _line_reply(
    transport: OutboundTransport, token: str, reply_token: str, text: str
) -> None:
    """Best effort: a failed reply must never turn the webhook into an error (LINE would retry)."""
    await transport.post_json(
        _LINE_REPLY_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
    )


@router.post("/channels/line/webhook", dependencies=[Depends(system_scope)])
async def line_webhook(
    request: Request,
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
    transport: OutboundTransport = Depends(get_line_transport),
    signature: str | None = Header(default=None, alias="X-Line-Signature"),
) -> dict[str, int]:
    """LINE calls this for every event on the official account (the URL is registered in the LINE
    console). The `X-Line-Signature` header is base64(HMAC-SHA256(channel secret, raw body)); a
    request without a valid one is refused before the body is read as JSON. Handled events: a
    message carrying a link code binds the sender; `follow` sends instructions; `unfollow` drops
    the link (the user blocked the account, so pushes would fail)."""
    settings = state.settings
    secret, token = (
        reveal(settings.line_messaging_channel_secret),
        reveal(settings.line_messaging_channel_access_token),
    )
    body = await request.body()
    expected = (
        base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        if secret
        else None
    )
    if (
        expected is None
        or signature is None
        or not hmac.compare_digest(signature.encode(), expected.encode())
    ):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Bad webhook signature.",
            retryable=False,
        )
    try:
        payload = cast("dict[str, object]", json.loads(body or b"{}"))
    except ValueError:
        return {"handled": 0}
    events = payload.get("events")
    handled = 0
    for raw in cast("list[object]", events) if isinstance(events, list) else []:
        if not isinstance(raw, dict):
            continue
        event = cast("dict[str, object]", raw)
        source = event.get("source")
        line_user = (
            cast("dict[str, object]", source).get("userId") if isinstance(source, dict) else None
        )
        reply_token = event.get("replyToken")
        kind = event.get("type")
        if not isinstance(line_user, str):
            continue
        if kind == "unfollow":
            await store.unlink_line_user(line_user_id=line_user)
            handled += 1
        elif kind in ("follow", "message"):
            message = event.get("message")
            text = (
                cast("dict[str, object]", message).get("text")
                if isinstance(message, dict)
                else None
            )
            match = _LINE_CODE.search(text.upper()) if isinstance(text, str) else None
            linked = False
            if match is not None:
                user_id = await store.complete_channel_link(
                    channel=_LINE, token=match.group(1), address=line_user, now=datetime.now(UTC)
                )
                linked = user_id is not None
            if token and isinstance(reply_token, str):
                reply = "已連結完成，之後的提醒會傳到這裡。" if linked else _LINK_HELP
                await _line_reply(transport, token, reply_token, reply)
            handled += 1
    return {"handled": handled}


@router.post("/webhooks/telegram", dependencies=[Depends(system_scope)])
async def telegram_webhook(
    update: dict[str, object],
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, bool]:
    """Telegram calls this for every message to the bot. Only `/start <token>` matters: it binds
    the sender's chat to the account that issued the token. The secret header proves the call is
    Telegram's; anything else is ignored with a 200 so Telegram does not retry."""
    expected = reveal(state.settings.telegram_webhook_secret)
    if (
        not expected
        or secret is None
        or not hmac.compare_digest(secret.encode(), expected.encode())
    ):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Bad webhook secret.",
            retryable=False,
        )
    message = update.get("message")
    if not isinstance(message, dict):
        return {"linked": False}
    text = cast("dict[str, object]", message).get("text")
    chat = cast("dict[str, object]", message).get("chat")
    chat_id = cast("dict[str, object]", chat).get("id") if isinstance(chat, dict) else None
    if not (isinstance(text, str) and text.startswith(_START_PREFIX) and chat_id is not None):
        return {"linked": False}
    user_id = await store.complete_channel_link(
        channel=_TELEGRAM,
        token=text[len(_START_PREFIX) :].strip(),
        address=str(chat_id),
        now=datetime.now(UTC),
    )
    return {"linked": user_id is not None}


class RegisterDeviceRequest(BaseModel):
    platform: DevicePlatform
    # A web `PushSubscription` serialised to JSON (endpoint + keys) is well over 512 bytes.
    token: str = Field(min_length=1, max_length=4096)


@router.post("/me/devices", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: RegisterDeviceRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> DevicePushToken:
    # A web token is a push-service URL the server will later POST to, so only real push services
    # are accepted (https + an allow-listed host) — otherwise it is an SSRF primitive.
    if body.platform is DevicePlatform.WEB and not subscription_endpoint_allowed(body.token):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_push_subscription",
            message="That is not a browser push subscription from a supported push service.",
            retryable=False,
        )
    return await store.register_device(user_id=user.id, platform=body.platform, token=body.token)


@router.delete("/me/devices/{token:path}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    token: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unregister_device(user_id=user.id, token=token)


__all__ = ["router"]
