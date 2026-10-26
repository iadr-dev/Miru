"""Email unsubscribe pages and the Resend webhook."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse

from accounts.models import User
from accounts.store import UserNotFoundError, UserStore
from api.dependencies import (
    AppState,
    get_state,
    get_user_store,
    system_scope,
)
from api.errors import ApiError
from api.routers.scheduler._common import store as _store
from config.settings import reveal
from scheduler.store import (
    SchedulerStore,
)
from scheduler.unsubscribe import split_token, verify_token

router = APIRouter()


_RESEND_TOLERANCE_SECONDS = 300


_UNSUB_PAGE = (
    '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>{title}</title></head>"
    '<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;'
    'padding:0 1rem">'
    "<h1>{title}</h1><p>{body}</p>{form}</body></html>"
)


async def _unsubscribe_target(
    token: str, state: AppState, users: UserStore
) -> tuple[User, str] | None:
    """The account a valid unsubscribe token names and its email, or None if it is invalid."""
    secret = reveal(state.settings.jwt_secret_key)
    parts = split_token(token)
    if parts is None or not secret:
        return None
    try:
        user = await users.get(user_id=parts[0])
    except UserNotFoundError:
        return None
    if not user.email or not verify_token(secret, token, user.id, user.email):
        return None
    return user, user.email


@router.get("/email/unsubscribe", response_class=HTMLResponse, dependencies=[Depends(system_scope)])
async def unsubscribe_page(
    token: str = Query(min_length=3, max_length=200),
    state: AppState = Depends(get_state),
    users: UserStore = Depends(get_user_store),
    store: SchedulerStore = Depends(_store),
) -> HTMLResponse:
    """A confirmation page, not the action: mail scanners open links, so a GET must not unsubscribe
    anyone. The button posts to the same URL. An address that already unsubscribed sees the
    opposite button (resubscribe) instead."""
    target = await _unsubscribe_target(token, state, users)
    if target is None:
        return HTMLResponse(
            _UNSUB_PAGE.format(title="連結無效", body="這個取消訂閱連結無效或已過期。", form=""),
            status_code=400,
        )
    if await store.email_suppression_reason(address=target[1]) == "unsubscribed":
        return _unsubscribed_page(token)
    form = (
        f'<form method="post" action="/api/v1/email/unsubscribe?token={html.escape(token)}">'
        '<button type="submit" style="padding:.6rem 1rem">確認取消電子郵件通知</button></form>'
    )
    return HTMLResponse(
        _UNSUB_PAGE.format(
            title="取消電子郵件通知", body="確認後，我們不會再寄提醒信給你。", form=form
        )
    )


@router.post(
    "/email/unsubscribe", response_class=HTMLResponse, dependencies=[Depends(system_scope)]
)
async def unsubscribe_action(
    token: str = Query(min_length=3, max_length=200),
    state: AppState = Depends(get_state),
    users: UserStore = Depends(get_user_store),
    store: SchedulerStore = Depends(_store),
) -> HTMLResponse:
    """The one-click unsubscribe (RFC 8058: mail clients POST `List-Unsubscribe=One-Click` here) and
    the confirmation button's target. The address stops receiving email; the user can turn it back
    on by ticking 電子郵件 in settings."""
    target = await _unsubscribe_target(token, state, users)
    if target is None:
        return HTMLResponse(
            _UNSUB_PAGE.format(title="連結無效", body="這個取消訂閱連結無效或已過期。", form=""),
            status_code=400,
        )
    await store.suppress_email(address=target[1], reason="unsubscribed")
    return _unsubscribed_page(token)


@router.post(
    "/email/resubscribe", response_class=HTMLResponse, dependencies=[Depends(system_scope)]
)
async def resubscribe_action(
    token: str = Query(min_length=3, max_length=200),
    state: AppState = Depends(get_state),
    users: UserStore = Depends(get_user_store),
    store: SchedulerStore = Depends(_store),
) -> HTMLResponse:
    """Undo an unsubscribe with the same signed link. Only an *unsubscribe* is undone — a bounce or
    spam complaint stays (`clear_unsubscribe`). Email also needs to be ticked in settings, so the
    page points there when the channel is off."""
    target = await _unsubscribe_target(token, state, users)
    if target is None:
        return HTMLResponse(
            _UNSUB_PAGE.format(title="連結無效", body="這個連結無效或已過期。", form=""),
            status_code=400,
        )
    await store.clear_unsubscribe(address=target[1])
    settings_url = f"{state.settings.frontend_base_url.rstrip('/')}/zh-TW/settings/notifications"
    return HTMLResponse(
        _UNSUB_PAGE.format(
            title="已重新訂閱電子郵件通知",
            body="之後會再寄提醒信給你。",
            form=(
                f'<p><a href="{html.escape(settings_url)}">前往通知設定確認電子郵件已勾選</a></p>'
                '<form method="post" action="/api/v1/email/unsubscribe'
                f'?token={html.escape(token)}">'
                '<button type="submit" style="padding:.6rem 1rem">取消電子郵件通知</button></form>'
            ),
        )
    )


def _unsubscribed_page(token: str) -> HTMLResponse:
    return HTMLResponse(
        _UNSUB_PAGE.format(
            title="已取消電子郵件通知",
            body="之後不會再寄提醒信給你。改變心意了？",
            form=(
                '<form method="post" action="/api/v1/email/resubscribe'
                f'?token={html.escape(token)}">'
                '<button type="submit" style="padding:.6rem 1rem">重新訂閱</button></form>'
            ),
        )
    )


def _resend_signature_ok(secret: str, headers: dict[str, str], body: bytes) -> bool:
    """Resend signs webhooks with Svix: `svix-signature` holds one or more `v1,<base64>` values,
    each the HMAC-SHA256 of `"{svix-id}.{svix-timestamp}.{body}"` under the secret's bytes (the
    base64 after `whsec_`). A stale timestamp is refused so a captured request is not replayed."""
    msg_id, stamp, provided = (
        headers.get("svix-id"),
        headers.get("svix-timestamp"),
        headers.get("svix-signature"),
    )
    if not (msg_id and stamp and provided):
        return False
    try:
        if abs(datetime.now(UTC).timestamp() - int(stamp)) > _RESEND_TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    candidates = [part.split(",", 1)[1] for part in provided.split() if "," in part]
    return any(hmac.compare_digest(c.encode(), expected.encode()) for c in candidates)


@router.post("/webhooks/resend", dependencies=[Depends(system_scope)])
async def resend_webhook(
    request: Request,
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> dict[str, int]:
    """Resend reports what happened to each email. Three outcomes matter here: a hard bounce
    (`email.bounced`, type Permanent), a spam complaint (`email.complained`) and Resend's own
    `email.suppressed`. The address is recorded and never emailed again. A soft bounce, delivery
    and open events are acknowledged and ignored. The request must carry a valid Svix signature;
    with no secret configured every request is refused."""
    secret = reveal(state.settings.resend_webhook_secret)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not secret or not _resend_signature_ok(secret, headers, body):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Bad webhook signature.",
            retryable=False,
        )
    parsed = _parse_event(body)
    if parsed is None:
        return {"suppressed": 0}
    kind, payload = parsed
    reason = _suppression_reason(kind, payload)
    if reason is None:
        return {"suppressed": 0}
    event_id = str(payload.get("email_id") or "") or None
    count = 0
    for address in _recipient_addresses(payload):
        await store.suppress_email(address=address, reason=reason, event_id=event_id)
        count += 1
    return {"suppressed": count}


def _parse_event(body: bytes) -> tuple[object, dict[str, object]] | None:
    """The event's `type` and its `data` object, or None for a body that is not JSON or has no
    `data` object (acknowledged and ignored)."""
    try:
        event = cast("dict[str, object]", json.loads(body or b"{}"))
    except ValueError:
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    return event.get("type"), cast("dict[str, object]", data)


def _suppression_reason(kind: object, payload: dict[str, object]) -> str | None:
    """Why the event's addresses must never be emailed again, or None for an event to ignore."""
    bounce = payload.get("bounce")
    hard_bounce = (
        kind == "email.bounced"
        and isinstance(bounce, dict)
        and str(cast("dict[str, object]", bounce).get("type", "")).lower() == "permanent"
    )
    # `email.suppressed`: Resend itself declined to send because the address is already on its own
    # suppression list — it is just as unreachable for us, so it is recorded the same way.
    if not (hard_bounce or kind in ("email.complained", "email.suppressed")):
        return None
    return "complained" if kind == "email.complained" else "bounced"


def _recipient_addresses(payload: dict[str, object]) -> list[str]:
    recipients = payload.get("to")
    if not isinstance(recipients, list):
        return []
    return [
        address
        for address in cast("list[object]", recipients)
        if isinstance(address, str) and address.strip()
    ]


__all__ = ["router"]
