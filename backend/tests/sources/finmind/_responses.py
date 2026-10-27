"""Response bodies FinMind's `/data` endpoint really returned (captured live, 2026-10-27, with
the account on the free "register" level), frozen here so the adapter's classification is
tested against the wire format and not against a guess. The token echo (`token_tail`) is
dropped from the captures."""

from __future__ import annotations

from typing import Any, Final

# HTTP 400 for a dataset (or the whole-market-by-date form) outside the account's level.
PLAN_DENIED_400: Final[dict[str, Any]] = {
    "msg": "Your level is register. Please update your user level. Detail information:"
    "https://finmindtrade.com/analysis/#/Sponsor/sponsor",
    "status": 400,
}
# HTTP 400 for a token FinMind cannot decode (a mangled or invalid key). Same status as the
# plan denial, so the status alone cannot tell the two apart.
BAD_TOKEN_400: Final[dict[str, Any]] = {"msg": "Token is illegal.", "status": 400}
# HTTP 403 while the calling IP is banned for over-use (`retry_after` in seconds).
IP_BANNED_403: Final[dict[str, Any]] = {"msg": "ip banned", "status": 403, "retry_after": 390}
# HTTP 402 when the token's hourly request cap is spent (the documented body).
RATE_LIMIT_402: Final[dict[str, Any]] = {"msg": "Requests reach the upper limit.", "status": 402}
# HTTP 200 for an allowed dataset on a date holding no rows (a weekend, say).
NO_DATA_200: Final[dict[str, Any]] = {"msg": "success", "status": 200, "data": []}
