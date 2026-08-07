from __future__ import annotations

# Side-effect import: registers every ORM model on Base.metadata so Alembic/create_all see
# the full schema.
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from db.base import Base, make_engine, make_session_factory, session_scope

__all__ = ["Base", "make_engine", "make_session_factory", "session_scope"]
