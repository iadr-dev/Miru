"""Accounts, auth, tiers, entitlements, BYOK.

The product-layer package that turns the framework multi-user: OAuth-upsert identity + session
rotation (`auth`), the tier/feature entitlement source of truth (`entitlements`), encrypted
BYOK secret storage (`secrets`/`crypto`), and the `EntitlementResolver` that feeds a real
per-user tier into the sponsor-masking gate `tools.base.dispatch_tool` already owns (`hooks`).
"""

from __future__ import annotations

from accounts.entitlements import (
    chat_limit_for,
    entitlement_view,
    feature_allowed,
    finmind_class_for,
)
from accounts.hooks import EntitlementResolver
from accounts.models import EntitlementView, OAuthProvider, SecretKind, User

__all__ = [
    "EntitlementResolver",
    "EntitlementView",
    "OAuthProvider",
    "SecretKind",
    "User",
    "chat_limit_for",
    "entitlement_view",
    "feature_allowed",
    "finmind_class_for",
]
