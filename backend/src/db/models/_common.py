"""Column types and string constants shared by the ORM model modules."""

from __future__ import annotations

from sqlalchemy import DateTime

UTC_TIMESTAMP = DateTime(timezone=True)

CASCADE_DELETE_ORPHAN = "all, delete-orphan"
TURNS_ID_FK = "turns.id"
USERS_ID_FK = "users.id"
MARKET_TW_US_CHECK = "market IN ('TW','US')"
