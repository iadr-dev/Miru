"""SQLAlchemy ORM models for the first tables this codebase persists: conversations
(threads/turns/messages), the SSE replay log (`stream_events`), long-term memory (`memories`),
and the first-class watchlist store (`watchlists`/`watchlist_items`).

Every table follows the project's dual-dialect conventions (see `db/base.py`): text UUID PKs
(app-generated, never a DB-native `gen_random_uuid()`), UTC timestamps, `text + CHECK` in place
of native enums, JSON (not JSONB) for portable structured columns, and `ON DELETE CASCADE` from
`users`/`threads` (SQLite needs `PRAGMA foreign_keys=ON`, wired in `db/sqlite_pragma.py`).

The `users` table now exists (Phase 9, accounts/entitlements). The deferred-FK decision is
resolved as follows: the **new** account/pet tables added this phase (`oauth_accounts`,
`user_secrets`, `sessions`, `user_pets`, `user_pet_stats`) carry a real
`ForeignKey(_USERS_ID_FK, ondelete="CASCADE")` — they are pure children of a user with no
independent lifecycle. The **pre-existing** per-row `user_id` columns on `threads`/`turns`/
`messages`/`stream_events`/`memories`/`watchlists`/`watchlist_items` are deliberately left as
bare indexed columns and are *not* retrofitted with a FK: they are denormalized
isolation-scoping keys (the `WHERE user_id = ?` store wrapper + Postgres RLS is the isolation
mechanism, per `backend/CLAUDE.md` §6, not a DB-level FK), and retrofitting a hard FK onto a
denormalized copy would both fight that design and force every conversation/memory row to
already have a `users` parent — a coupling those stores intentionally don't take. Identity is
the `users` table; these columns point at it by convention, scoped in SQL.

`Base.metadata.create_all()` (called once at process startup, see `db/base.py`) is this project's
sole schema-provisioning mechanism — no Alembic migration chain. Every partial (`WHERE`-qualified)
index/constraint below is expressed directly via `Index(..., sqlite_where=..., postgresql_where=
...)`, which SQLAlchemy Core supports natively on both dialects, so `create_all()` alone produces
a complete, dual-dialect-correct schema with no DDL that only a migration could express.
"""

from __future__ import annotations

from db.models.accounts import (
    OAuthAccountRow,
    SessionRow,
    UserPetRow,
    UserPetStatsRow,
    UserRow,
    UserSecretRow,
)
from db.models.billing import (
    BillingEventRow,
    LlmUsageRow,
    SubscriptionRow,
    UsageCounterRow,
    UsageEventRow,
)
from db.models.conversation import (
    MessageRow,
    StreamEventRow,
    ThreadRow,
    TurnFeedbackRow,
    TurnRow,
)
from db.models.memory import (
    MemoryRow,
    ScreenerScreenRow,
    WatchlistItemRow,
    WatchlistRow,
)
from db.models.oauth_server import (
    OAuthAuthCodeRow,
    OAuthAuthorizationRequestRow,
    OAuthClientRow,
    OAuthGrantRow,
    OAuthRefreshTokenRow,
)
from db.models.scheduler import (
    AlertEvalMarkRow,
    AlertPreferenceRow,
    AlertRuleRow,
    ChannelLinkRow,
    DevicePushTokenRow,
    EmailSuppressionRow,
    NotificationRow,
    ScheduleJobRow,
    StrategySubscriptionRow,
)
from db.models.scores import (
    StockScoreRow,
    StockSummaryRow,
    TrendScoreRow,
)
from db.models.supply_chain import (
    ScConceptMemberRow,
    ScConceptThemeRow,
    ScEntityMentionRow,
    ScEntityRow,
    ScEvidenceRow,
    ScPositionRow,
    ScRelationRow,
)
from db.models.themes import (
    ThemeChangeLogRow,
    ThemeMembershipRow,
    ThemeRow,
    ThemeTierRow,
)

__all__ = [
    "AlertEvalMarkRow",
    "AlertPreferenceRow",
    "AlertRuleRow",
    "BillingEventRow",
    "ChannelLinkRow",
    "DevicePushTokenRow",
    "EmailSuppressionRow",
    "LlmUsageRow",
    "MemoryRow",
    "MessageRow",
    "NotificationRow",
    "OAuthAccountRow",
    "OAuthAuthCodeRow",
    "OAuthAuthorizationRequestRow",
    "OAuthClientRow",
    "OAuthGrantRow",
    "OAuthRefreshTokenRow",
    "ScConceptMemberRow",
    "ScConceptThemeRow",
    "ScEntityMentionRow",
    "ScEntityRow",
    "ScEvidenceRow",
    "ScPositionRow",
    "ScRelationRow",
    "ScheduleJobRow",
    "ScreenerScreenRow",
    "SessionRow",
    "StockScoreRow",
    "StockSummaryRow",
    "StrategySubscriptionRow",
    "StreamEventRow",
    "SubscriptionRow",
    "ThemeChangeLogRow",
    "ThemeMembershipRow",
    "ThemeRow",
    "ThemeTierRow",
    "ThreadRow",
    "TrendScoreRow",
    "TurnFeedbackRow",
    "TurnRow",
    "UsageCounterRow",
    "UsageEventRow",
    "UserPetRow",
    "UserPetStatsRow",
    "UserRow",
    "UserSecretRow",
    "WatchlistItemRow",
    "WatchlistRow",
]
