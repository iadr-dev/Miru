"""`ThemeStore` — the SQLAlchemy-backed repository over `themes`/`theme_memberships`.

The caller owns the session and the transaction boundary (per-request unit of work, per
`db/base.py::session_scope`); this class never opens its own session, matching the convention the
other relational stores in this codebase follow. The one non-obvious method is
`replace_theme_members`: it deletes a source's rows for a theme and inserts the fresh snapshot
**within the caller's single transaction**, so a re-run is a clean replace rather than an ever-
growing union, and a concurrent reader never sees a half-deleted set.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.themes.models import (
    EdgeConfidence,
    EdgeSource,
    EdgeType,
    SupplyChainEdge,
    SymbolTheme,
    Theme,
    ThemeChangeEntry,
    ThemeKind,
    ThemeMember,
    ThemeSource,
    ThemeStatus,
    ThemeTier,
    ThemeTierName,
    ThemeTierSource,
)
from db.models import (
    SupplyChainEdgeRow,
    ThemeChangeLogRow,
    ThemeMembershipRow,
    ThemeRow,
    ThemeTierRow,
)

logger = structlog.get_logger(__name__)


@runtime_checkable
class ThemeStore(Protocol):
    async def get_theme_by_key(self, *, key: str) -> Theme | None: ...

    async def list_themes(
        self, *, kind: ThemeKind | None = None, status: ThemeStatus | None = None
    ) -> list[Theme]: ...

    async def get_theme_members(self, *, theme_key: str) -> list[str]: ...

    async def count_theme_members(self, *, theme_key: str) -> int: ...

    async def get_theme_member_details(self, *, theme_key: str) -> list[ThemeMember]: ...

    async def get_symbol_themes(self, *, symbol: str, market: str) -> list[SymbolTheme]: ...

    async def upsert_theme(  # noqa: PLR0913 — one field per persisted theme dimension
        self,
        *,
        key: str,
        name_zh: str,
        kind: ThemeKind,
        source: ThemeSource,
        name_en: str = "",
        status: ThemeStatus = ThemeStatus.ACTIVE,
    ) -> Theme: ...

    async def replace_theme_members(
        self, *, theme_key: str, source: ThemeSource, members: list[ThemeMember]
    ) -> int: ...

    async def get_theme_tiers(self, *, theme_key: str) -> list[ThemeTier]: ...

    async def list_uncurated_sub_industries(
        self, *, covered: set[tuple[str, str]]
    ) -> list[tuple[str, str]]: ...

    async def upsert_theme_tier(
        self, *, theme_id: str, sub_industry: str, tier: ThemeTierName, source: ThemeTierSource
    ) -> None: ...

    async def list_all_membership_symbols(self) -> set[str]: ...

    async def find_symbols_by_sub_industry_keyword(
        self, *, keywords: list[str]
    ) -> list[tuple[str, str, str]]: ...

    async def replace_edges_for_source(
        self, *, source: EdgeSource, edges: list[SupplyChainEdge]
    ) -> int: ...

    async def get_edges_from(self, *, from_symbol: str) -> list[SupplyChainEdge]: ...

    async def get_edges_to(self, *, to_symbol: str) -> list[SupplyChainEdge]: ...

    async def record_change(self, *, entry: ThemeChangeEntry) -> None: ...


def _row_to_theme(row: ThemeRow) -> Theme:
    return Theme(
        id=row.id,
        key=row.key,
        name_zh=row.name_zh,
        name_en=row.name_en,
        kind=ThemeKind(row.kind),
        status=ThemeStatus(row.status),
        source=ThemeSource(row.source),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyThemeStore:
    """`ThemeStore` over one `AsyncSession`. The caller owns the session/transaction boundary —
    this class never opens or commits its own session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_theme_by_key(self, *, key: str) -> Theme | None:
        row = await self._session.scalar(select(ThemeRow).where(ThemeRow.key == key))
        return _row_to_theme(row) if row is not None else None

    async def list_themes(
        self, *, kind: ThemeKind | None = None, status: ThemeStatus | None = None
    ) -> list[Theme]:
        stmt = select(ThemeRow)
        if kind is not None:
            stmt = stmt.where(ThemeRow.kind == kind.value)
        if status is not None:
            stmt = stmt.where(ThemeRow.status == status.value)
        stmt = stmt.order_by(ThemeRow.key)
        rows = (await self._session.scalars(stmt)).all()
        return [_row_to_theme(row) for row in rows]

    async def get_theme_members(self, *, theme_key: str) -> list[str]:
        """The screener's hot-path read: the symbols in a theme, resolved through the
        `UNIQUE (theme_id, symbol)` index. An unknown key returns an empty list, not an error."""
        stmt = (
            select(ThemeMembershipRow.symbol)
            .join(ThemeRow, ThemeRow.id == ThemeMembershipRow.theme_id)
            .where(ThemeRow.key == theme_key)
            .order_by(ThemeMembershipRow.symbol)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_theme_members(self, *, theme_key: str) -> int:
        """The member count for one theme — a lighter read than `get_theme_member_details` for a
        caller (the `list_themes` discovery card) that only needs the count, not every row."""
        stmt = (
            select(func.count(ThemeMembershipRow.id))
            .join(ThemeRow, ThemeRow.id == ThemeMembershipRow.theme_id)
            .where(ThemeRow.key == theme_key)
        )
        return (await self._session.scalar(stmt)) or 0

    async def get_theme_member_details(self, *, theme_key: str) -> list[ThemeMember]:
        """The full membership rows for a theme — each symbol with its per-membership
        `sub_industry`/`confidence`/`source`, so a caller can distinguish an editorially-sourced
        industry-chain member from an AI-researched concept member. The bare `get_theme_members`
        read returns symbols only for the screener hot path; this fuller read backs the agent
        tool and REST detail view. An unknown key returns an empty list, not an error."""
        stmt = (
            select(ThemeMembershipRow)
            .join(ThemeRow, ThemeRow.id == ThemeMembershipRow.theme_id)
            .where(ThemeRow.key == theme_key)
            .order_by(ThemeMembershipRow.symbol)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            ThemeMember(
                symbol=row.symbol,
                market=row.market,
                sub_industry=row.sub_industry,
                confidence=row.confidence,
                source=ThemeSource(row.source),
            )
            for row in rows
        ]

    async def get_symbol_themes(self, *, symbol: str, market: str) -> list[SymbolTheme]:
        """The reverse lookup: the themes a symbol belongs to, resolved through the
        `(symbol, market)` index."""
        stmt = (
            select(
                ThemeRow.key,
                ThemeRow.name_zh,
                ThemeRow.kind,
                ThemeMembershipRow.sub_industry,
                ThemeMembershipRow.confidence,
            )
            .join(ThemeRow, ThemeRow.id == ThemeMembershipRow.theme_id)
            .where(ThemeMembershipRow.symbol == symbol, ThemeMembershipRow.market == market)
            .order_by(ThemeRow.key)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            SymbolTheme(
                theme_key=key,
                name_zh=name_zh,
                kind=ThemeKind(kind),
                sub_industry=sub_industry,
                confidence=confidence,
            )
            for key, name_zh, kind, sub_industry, confidence in rows
        ]

    async def upsert_theme(  # noqa: PLR0913 — matches the Protocol's own shape above
        self,
        *,
        key: str,
        name_zh: str,
        kind: ThemeKind,
        source: ThemeSource,
        name_en: str = "",
        status: ThemeStatus = ThemeStatus.ACTIVE,
    ) -> Theme:
        """Insert a theme keyed on `key`, or update the mutable columns of the existing one. Keyed
        on the natural slug rather than a surrogate id so a re-seed matches the same theme."""
        now = datetime.now(UTC)
        row = await self._session.scalar(select(ThemeRow).where(ThemeRow.key == key))
        if row is None:
            row = ThemeRow(
                id=str(uuid.uuid4()),
                key=key,
                name_zh=name_zh,
                name_en=name_en,
                kind=kind.value,
                status=status.value,
                source=source.value,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.name_zh = name_zh
            row.name_en = name_en
            row.kind = kind.value
            row.status = status.value
            row.source = source.value
            row.updated_at = now
        await self._session.flush()
        return _row_to_theme(row)

    async def replace_theme_members(
        self, *, theme_key: str, source: ThemeSource, members: list[ThemeMember]
    ) -> int:
        """Replace this source's membership rows for the theme with a fresh snapshot, in the
        caller's one transaction: delete the `(theme_id, source)` partition, then insert `members`.
        A re-run is therefore a clean replace, never a growing union — and because it is one
        transaction a reader never observes the intermediate empty set. Returns the number of rows
        inserted. Members carrying a different `source` than the argument are ignored."""
        theme = await self._session.scalar(select(ThemeRow).where(ThemeRow.key == theme_key))
        if theme is None:
            raise ThemeNotFoundError(theme_key)
        await self._session.execute(
            delete(ThemeMembershipRow).where(
                ThemeMembershipRow.theme_id == theme.id,
                ThemeMembershipRow.source == source.value,
            )
        )
        now = datetime.now(UTC)
        inserted = 0
        seen: set[str] = set()
        for member in members:
            if member.source is not source or member.symbol in seen:
                # The `UNIQUE (theme_id, symbol)` guard means a snapshot with a symbol twice would
                # otherwise abort the whole transaction — dedupe defensively, keeping the first.
                continue
            seen.add(member.symbol)
            self._session.add(
                ThemeMembershipRow(
                    id=str(uuid.uuid4()),
                    theme_id=theme.id,
                    symbol=member.symbol,
                    market=member.market,
                    sub_industry=member.sub_industry,
                    confidence=member.confidence,
                    source=source.value,
                    created_at=now,
                )
            )
            inserted += 1
        await self._session.flush()
        return inserted

    async def get_theme_tiers(self, *, theme_key: str) -> list[ThemeTier]:
        """The tier rows for a theme, ordered by sub-industry. An unknown key returns an empty
        list, not an error — the screener reads a symbol's tier through this."""
        stmt = (
            select(ThemeTierRow)
            .join(ThemeRow, ThemeRow.id == ThemeTierRow.theme_id)
            .where(ThemeRow.key == theme_key)
            .order_by(ThemeTierRow.sub_industry)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            ThemeTier(
                sub_industry=row.sub_industry,
                tier=ThemeTierName(row.tier),
                source=ThemeTierSource(row.source),
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def list_uncurated_sub_industries(
        self, *, covered: set[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Every distinct `(theme_id, sub_industry)` pair present in `theme_memberships` that is
        NOT in `covered` (the set the curated map already wrote). This is what the heuristic
        backfill iterates: the sub-industries that exist in real memberships but no editor tiered.
        A blank `sub_industry` is skipped — an industry-chain total row carries no tier."""
        stmt = (
            select(ThemeMembershipRow.theme_id, ThemeMembershipRow.sub_industry)
            .where(ThemeMembershipRow.sub_industry != "")
            .distinct()
            .order_by(ThemeMembershipRow.theme_id, ThemeMembershipRow.sub_industry)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            (theme_id, sub_industry)
            for theme_id, sub_industry in rows
            if (theme_id, sub_industry) not in covered
        ]

    async def upsert_theme_tier(
        self, *, theme_id: str, sub_industry: str, tier: ThemeTierName, source: ThemeTierSource
    ) -> None:
        """Insert or update one `(theme_id, sub_industry)` tier row, keyed on the unique pair so a
        re-run never duplicates. Curated-wins is enforced here at the write, not only in the seed
        ordering: a `heuristic` write against a row already marked `curated` is a no-op, so no
        re-run — in any order — can flip an editor's decision back to a guess."""
        now = datetime.now(UTC)
        row = await self._session.scalar(
            select(ThemeTierRow).where(
                ThemeTierRow.theme_id == theme_id,
                ThemeTierRow.sub_industry == sub_industry,
            )
        )
        if row is None:
            self._session.add(
                ThemeTierRow(
                    id=str(uuid.uuid4()),
                    theme_id=theme_id,
                    sub_industry=sub_industry,
                    tier=tier.value,
                    source=source.value,
                    updated_at=now,
                )
            )
        else:
            if source is ThemeTierSource.HEURISTIC and row.source == ThemeTierSource.CURATED.value:
                return
            row.tier = tier.value
            row.source = source.value
            row.updated_at = now
        await self._session.flush()

    async def list_all_membership_symbols(self) -> set[str]:
        """Every symbol present in any `theme_memberships` row — the real, stored universe the
        fabrication safeguard checks a model's proposed symbols against. This set is populated by
        the industry-chain seed, which covers the whole listed roster, so a proposed code absent
        from it is not a real listed symbol this platform knows and must never be persisted."""
        stmt = select(ThemeMembershipRow.symbol).distinct()
        return set((await self._session.scalars(stmt)).all())

    async def find_symbols_by_sub_industry_keyword(
        self, *, keywords: list[str]
    ) -> list[tuple[str, str, str]]:
        """Distinct `(symbol, market, sub_industry)` rows whose `sub_industry` contains any of
        `keywords` (a cheap substring match, not a new capability) — the local cross-reference that
        grounds the research prompt in already-classified stocks alongside the web evidence. An
        empty keyword list matches nothing."""
        if not keywords:
            return []
        clauses = [ThemeMembershipRow.sub_industry.contains(kw) for kw in keywords if kw]
        if not clauses:
            return []
        stmt = (
            select(
                ThemeMembershipRow.symbol,
                ThemeMembershipRow.market,
                ThemeMembershipRow.sub_industry,
            )
            .where(or_(*clauses))
            .distinct()
            .order_by(ThemeMembershipRow.symbol)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(symbol, market, sub_industry) for symbol, market, sub_industry in rows]

    async def replace_edges_for_source(
        self, *, source: EdgeSource, edges: list[SupplyChainEdge]
    ) -> int:
        """Replace this source's `supply_chain_edges` rows with a fresh snapshot, in the caller's
        one transaction: delete the `source` partition, then insert `edges`. A re-run is a clean
        replace, never a growing union. Returns the number of rows inserted. Edges carrying a
        different `source` than the argument are ignored; a duplicated `(from,to,type)` within one
        snapshot keeps the first."""
        await self._session.execute(
            delete(SupplyChainEdgeRow).where(SupplyChainEdgeRow.source == source.value)
        )
        now = datetime.now(UTC)
        inserted = 0
        seen: set[tuple[str, str, str]] = set()
        for edge in edges:
            if edge.source is not source:
                continue
            dedupe_key = (edge.from_symbol, edge.to_symbol, edge.edge_type.value)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            self._session.add(
                SupplyChainEdgeRow(
                    id=str(uuid.uuid4()),
                    from_symbol=edge.from_symbol,
                    to_symbol=edge.to_symbol,
                    market=edge.market,
                    edge_type=edge.edge_type.value,
                    confidence=edge.confidence.value,
                    revenue_pct=edge.revenue_pct,
                    source=source.value,
                    from_name=edge.from_name,
                    to_name=edge.to_name,
                    created_at=now,
                    updated_at=now,
                )
            )
            inserted += 1
        await self._session.flush()
        return inserted

    async def get_edges_from(self, *, from_symbol: str) -> list[SupplyChainEdge]:
        """The edges originating at a symbol, resolved through the `from_symbol` index."""
        stmt = (
            select(SupplyChainEdgeRow)
            .where(SupplyChainEdgeRow.from_symbol == from_symbol)
            .order_by(SupplyChainEdgeRow.to_symbol)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            SupplyChainEdge(
                from_symbol=row.from_symbol,
                to_symbol=row.to_symbol,
                market=row.market,
                edge_type=EdgeType(row.edge_type),
                confidence=EdgeConfidence(row.confidence),
                revenue_pct=row.revenue_pct,
                source=EdgeSource(row.source),
                from_name=row.from_name,
                to_name=row.to_name,
            )
            for row in rows
        ]

    async def get_edges_to(self, *, to_symbol: str) -> list[SupplyChainEdge]:
        """The edges terminating at a symbol, resolved through the `to_symbol` index — the
        reverse of `get_edges_from`, needed so a focus symbol's graph shows edges where it's
        either the supplier or the customer/competitor side."""
        stmt = (
            select(SupplyChainEdgeRow)
            .where(SupplyChainEdgeRow.to_symbol == to_symbol)
            .order_by(SupplyChainEdgeRow.from_symbol)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            SupplyChainEdge(
                from_symbol=row.from_symbol,
                to_symbol=row.to_symbol,
                market=row.market,
                edge_type=EdgeType(row.edge_type),
                confidence=EdgeConfidence(row.confidence),
                revenue_pct=row.revenue_pct,
                source=EdgeSource(row.source),
                from_name=row.from_name,
                to_name=row.to_name,
            )
            for row in rows
        ]

    async def record_change(self, *, entry: ThemeChangeEntry) -> None:
        """Append one audit-log row. Never raises on a benign duplicate — the log is append-only and
        every row is a fresh id, so repeated runs simply accrue history keyed by `run_id`."""
        self._session.add(
            ThemeChangeLogRow(
                id=str(uuid.uuid4()),
                entity=entry.entity.value,
                entity_ref=entry.entity_ref,
                action=entry.action.value,
                source=entry.source,
                detail=entry.detail[:200],
                run_id=entry.run_id,
                created_at=datetime.now(UTC),
            )
        )
        await self._session.flush()


class ThemeNotFoundError(LookupError):
    """Raised when a membership write names a theme key with no `themes` row — the theme must be
    upserted first."""


__all__ = ["SqlAlchemyThemeStore", "ThemeNotFoundError", "ThemeStore"]
