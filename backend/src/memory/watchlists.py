"""The first-class relational watchlist store (`watchlists`/`watchlist_items`) — named groups,
lexorank ordering, the idempotent star toggle, and the reverse query (`does user X watch
symbol Y`), plus the read-through `snapshot()` projection `memory.recall` renders into the
compact memory block agent recall used to read from a `memories(type='watchlist')` row.

Lexorank: a fractional sort key inserted strictly between two neighbors' keys, so two
concurrent reorders on different items never rewrite the same row (unlike integer-shift
ordering). `midpoint_key` below is the minimal correct implementation — lexicographic string
midpoint over the base-36 alphabet — sufficient here; a full bulk-reshuffle REST endpoint is a
transport-layer concern this module does not build.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.watchlist_presets import presets_for
from db.models import WatchlistItemRow, WatchlistRow
from schemas.enums import Market

logger = structlog.get_logger(__name__)


class WatchlistNotFoundError(LookupError):
    pass


class WatchlistItemNotFoundError(LookupError):
    pass


class StaleWatchlistVersionError(RuntimeError):
    """A group mutation carried an `If-Match` version that no longer matches the stored row —
    another device reordered/renamed first. Surfaced as a 409 so the client refetches."""


_LEXORANK_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_LEXORANK_START = "m"  # midpoint of the alphabet — first item's initial key
_LEXORANK_MIN = _LEXORANK_ALPHABET[0]
_LEXORANK_MAX = _LEXORANK_ALPHABET[-1]


def _char_at(key: str, index: int, pad: str) -> str:
    return key[index] if index < len(key) else pad


def midpoint_key(lo: str | None, hi: str | None) -> str:
    """A lexorank key strictly between `lo` and `hi` (either bound may be `None` for "no
    neighbor on this side"). Walks both keys character-by-character in the base-36 alphabet
    and returns the midpoint at the first differing position, falling back to appending a
    mid-alphabet character when the two keys are adjacent with no room between them — so two
    concurrent inserts between the same neighbors never produce the same key (jitter/retry on
    the rare exact-collision case is the caller's job)."""
    if lo is None and hi is None:
        return _LEXORANK_START
    resolved_lo = lo if lo is not None else _LEXORANK_MIN
    resolved_hi = hi if hi is not None else resolved_lo + _LEXORANK_MAX
    lo, hi = resolved_lo, resolved_hi
    if lo >= hi:
        raise ValueError(f"midpoint_key requires lo < hi, got {lo!r} >= {hi!r}")

    length = max(len(lo), len(hi)) + 1
    result_chars: list[str] = []
    for index in range(length):
        lo_char = _char_at(lo, index, _LEXORANK_MIN)
        hi_char = _char_at(hi, index, _LEXORANK_MIN)
        lo_pos = _LEXORANK_ALPHABET.index(lo_char)
        hi_pos = _LEXORANK_ALPHABET.index(hi_char)
        if lo_pos == hi_pos:
            result_chars.append(lo_char)
            continue
        if hi_pos - lo_pos > 1:
            result_chars.append(_LEXORANK_ALPHABET[(lo_pos + hi_pos) // 2])
            return "".join(result_chars)
        # Adjacent at this position — fix this character to lo_pos and keep resolving deeper.
        result_chars.append(lo_char)
    return "".join(result_chars) + _LEXORANK_START


class WatchlistGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    name: str
    market: Market
    asset_class: str
    sort_order: str
    is_default: bool
    version: int
    created_at: datetime
    updated_at: datetime


class WatchlistItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    watchlist_id: str
    user_id: str
    symbol: str
    market: Market
    asset_class: str
    note: str | None = None
    sort_order: str
    added_at: datetime


class WatchlistSnapshotGroup(BaseModel):
    """One rendered group in the compact `snapshot()` projection — `name -> [symbols]`, the
    same shape agent recall's memory block used before watchlists became relational."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    market: Market
    asset_class: str
    symbols: tuple[str, ...] = Field(default_factory=tuple)


@runtime_checkable
class WatchlistStoreProtocol(Protocol):
    async def snapshot(self, user_id: str) -> list[WatchlistSnapshotGroup]: ...

    async def watches(self, *, user_id: str, symbol: str) -> bool: ...


def _group_to_model(row: WatchlistRow) -> WatchlistGroup:
    return WatchlistGroup(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        market=Market(row.market),
        asset_class=row.asset_class,
        sort_order=row.sort_order,
        is_default=row.is_default,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_to_model(row: WatchlistItemRow) -> WatchlistItem:
    return WatchlistItem(
        id=row.id,
        watchlist_id=row.watchlist_id,
        user_id=row.user_id,
        symbol=row.symbol,
        market=Market(row.market),
        asset_class=row.asset_class,
        note=row.note,
        sort_order=row.sort_order,
        added_at=row.added_at,
    )


class WatchlistStore:
    """SQLAlchemy-backed watchlist store. The caller owns the session/transaction boundary,
    same convention as `memory.store.SqlAlchemyMemoryStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_group(
        self, *, user_id: str, name: str, market: Market, asset_class: str, is_default: bool = False
    ) -> WatchlistGroup:
        now = datetime.now(UTC)
        row = WatchlistRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            market=market.value,
            asset_class=asset_class,
            sort_order=midpoint_key(None, None),
            is_default=is_default,
            version=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("watchlist_group_created", user_id=user_id, watchlist_id=row.id, name=name)
        return _group_to_model(row)

    async def seed_default_presets(
        self, *, user_id: str, market: Market, asset_class: str
    ) -> list[WatchlistGroup]:
        """Seed `config.watchlist_presets.presets_for(market, asset_class)` for this user, but
        ONLY when they have zero non-deleted groups for this exact `(market, asset_class)` —
        never re-seeds over a user's own edits/deletions (a user who deleted every preset group
        stays empty, not silently repopulated). Called from the `GET /watchlists` route so a
        brand-new user's rail is never cold-empty, mirroring ADR-0027 §6's "server-seeded"
        intent without needing a dedicated register-time hook. Returns the newly created groups
        (empty list if the user already had groups here — a no-op, not an error)."""
        existing = await self._session.execute(
            select(WatchlistRow.id).where(
                WatchlistRow.user_id == user_id,
                WatchlistRow.market == market.value,
                WatchlistRow.asset_class == asset_class,
                WatchlistRow.deleted_at.is_(None),
            )
        )
        if existing.first() is not None:
            return []

        created: list[WatchlistGroup] = []
        for preset in presets_for(market, asset_class):
            group = await self.create_group(
                user_id=user_id,
                name=preset.name,
                market=preset.market,
                asset_class=preset.asset_class,
                is_default=True,
            )
            for symbol in preset.symbols:
                await self.add_item(user_id=user_id, watchlist_id=group.id, symbol=symbol)
            created.append(group)
        if created:
            logger.info(
                "watchlist_presets_seeded",
                user_id=user_id,
                market=market.value,
                asset_class=asset_class,
                group_count=len(created),
            )
        return created

    async def list_groups(self, user_id: str) -> list[WatchlistGroup]:
        result = await self._session.execute(
            select(WatchlistRow)
            .where(WatchlistRow.user_id == user_id, WatchlistRow.deleted_at.is_(None))
            .order_by(WatchlistRow.market, WatchlistRow.asset_class, WatchlistRow.sort_order)
        )
        return [_group_to_model(row) for row in result.scalars().all()]

    async def add_item(
        self, *, user_id: str, watchlist_id: str, symbol: str, note: str | None = None
    ) -> WatchlistItem:
        """Idempotent star-add: a second add of the same symbol to the same group is a no-op
        that returns the existing row, never a duplicate/violation — the caller doesn't need
        an `Idempotency-Key` for this operation; the partial-unique `(watchlist_id, symbol)`
        constraint on `watchlist_items` is the guarantee."""
        group_result = await self._session.execute(
            select(WatchlistRow).where(
                WatchlistRow.id == watchlist_id, WatchlistRow.user_id == user_id
            )
        )
        group = group_result.scalar_one()

        existing_result = await self._session.execute(
            select(WatchlistItemRow).where(
                WatchlistItemRow.watchlist_id == watchlist_id,
                WatchlistItemRow.symbol == symbol,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return _item_to_model(existing)

        last_result = await self._session.execute(
            select(WatchlistItemRow.sort_order)
            .where(WatchlistItemRow.watchlist_id == watchlist_id)
            .order_by(WatchlistItemRow.sort_order.desc())
            .limit(1)
        )
        last_sort_order = last_result.scalar_one_or_none()
        row = WatchlistItemRow(
            id=str(uuid.uuid4()),
            watchlist_id=watchlist_id,
            user_id=user_id,
            symbol=symbol,
            market=group.market,
            asset_class=group.asset_class,
            note=note,
            sort_order=midpoint_key(last_sort_order, None),
            added_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "watchlist_item_added", user_id=user_id, watchlist_id=watchlist_id, symbol=symbol
        )
        return _item_to_model(row)

    async def remove_item(self, *, user_id: str, watchlist_id: str, symbol: str) -> None:
        """Idempotent star-remove: removing an item that isn't there is a no-op, never an
        error — a REST layer built on this can return 204 regardless of whether a row was
        actually deleted."""
        result = await self._session.execute(
            select(WatchlistItemRow).where(
                WatchlistItemRow.watchlist_id == watchlist_id,
                WatchlistItemRow.user_id == user_id,
                WatchlistItemRow.symbol == symbol,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def watches(self, *, user_id: str, symbol: str) -> bool:
        """The reverse query: does this user watch `symbol` anywhere — the star's lit/unlit
        state, answerable in one indexed lookup."""
        result = await self._session.execute(
            select(WatchlistItemRow.id).where(
                WatchlistItemRow.user_id == user_id, WatchlistItemRow.symbol == symbol
            )
        )
        return result.first() is not None

    async def _group_row(self, *, user_id: str, watchlist_id: str) -> WatchlistRow:
        result = await self._session.execute(
            select(WatchlistRow).where(
                WatchlistRow.id == watchlist_id,
                WatchlistRow.user_id == user_id,
                WatchlistRow.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise WatchlistNotFoundError(watchlist_id)
        return row

    async def get_group(self, *, user_id: str, watchlist_id: str) -> WatchlistGroup:
        return _group_to_model(await self._group_row(user_id=user_id, watchlist_id=watchlist_id))

    async def list_items(self, *, user_id: str, watchlist_id: str) -> list[WatchlistItem]:
        await self._group_row(user_id=user_id, watchlist_id=watchlist_id)
        result = await self._session.execute(
            select(WatchlistItemRow)
            .where(WatchlistItemRow.watchlist_id == watchlist_id)
            .order_by(WatchlistItemRow.sort_order)
        )
        return [_item_to_model(row) for row in result.scalars().all()]

    async def rename_group(
        self, *, user_id: str, watchlist_id: str, name: str, expected_version: int | None = None
    ) -> WatchlistGroup:
        row = await self._group_row(user_id=user_id, watchlist_id=watchlist_id)
        if expected_version is not None and row.version != expected_version:
            raise StaleWatchlistVersionError(watchlist_id)
        row.name = name
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("watchlist_group_renamed", user_id=user_id, watchlist_id=watchlist_id)
        return _group_to_model(row)

    async def _group_sort_order(self, *, user_id: str, watchlist_id: str | None) -> str | None:
        if watchlist_id is None:
            return None
        return (await self._group_row(user_id=user_id, watchlist_id=watchlist_id)).sort_order

    async def reorder_group(
        self,
        *,
        user_id: str,
        watchlist_id: str,
        after_id: str | None,
        before_id: str | None,
        expected_version: int | None = None,
    ) -> WatchlistGroup:
        """Move a group between two neighbors via a fresh lexorank key. A `None` neighbor means
        "no bound on that side" (move to an end)."""
        row = await self._group_row(user_id=user_id, watchlist_id=watchlist_id)
        if expected_version is not None and row.version != expected_version:
            raise StaleWatchlistVersionError(watchlist_id)
        lo = await self._group_sort_order(user_id=user_id, watchlist_id=after_id)
        hi = await self._group_sort_order(user_id=user_id, watchlist_id=before_id)
        row.sort_order = midpoint_key(lo, hi)
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("watchlist_group_reordered", user_id=user_id, watchlist_id=watchlist_id)
        return _group_to_model(row)

    async def delete_group(self, *, user_id: str, watchlist_id: str) -> None:
        """Soft-delete the group; items cascade with the row on purge. A missing group raises,
        which the caller maps to 404."""
        row = await self._group_row(user_id=user_id, watchlist_id=watchlist_id)
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("watchlist_group_deleted", user_id=user_id, watchlist_id=watchlist_id)

    async def reorder_groups(self, *, user_id: str, ordered_ids: tuple[str, ...]) -> None:
        """Full-array reorder fallback for a big reshuffle: reassign ascending lexorank keys in
        the given order. Every id must belong to the caller (per-user isolation)."""
        lower: str | None = None
        for watchlist_id in ordered_ids:
            row = await self._group_row(user_id=user_id, watchlist_id=watchlist_id)
            row.sort_order = midpoint_key(lower, None)
            row.version += 1
            row.updated_at = datetime.now(UTC)
            lower = row.sort_order
        await self._session.flush()
        logger.info("watchlist_groups_reordered", user_id=user_id, count=len(ordered_ids))

    async def _item_row(self, *, user_id: str, watchlist_id: str, item_id: str) -> WatchlistItemRow:
        result = await self._session.execute(
            select(WatchlistItemRow).where(
                WatchlistItemRow.id == item_id,
                WatchlistItemRow.watchlist_id == watchlist_id,
                WatchlistItemRow.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise WatchlistItemNotFoundError(item_id)
        return row

    async def set_item_note(
        self, *, user_id: str, watchlist_id: str, item_id: str, note: str | None
    ) -> WatchlistItem:
        row = await self._item_row(user_id=user_id, watchlist_id=watchlist_id, item_id=item_id)
        row.note = note
        await self._session.flush()
        return _item_to_model(row)

    async def move_item(
        self,
        *,
        user_id: str,
        item_id: str,
        target_watchlist_id: str,
        after_id: str | None = None,
    ) -> WatchlistItem:
        """Move an item to another of the caller's groups, taking the target group's
        market/asset_class and a fresh trailing lexorank key (or after `after_id` if given)."""
        target = await self._group_row(user_id=user_id, watchlist_id=target_watchlist_id)
        result = await self._session.execute(
            select(WatchlistItemRow).where(
                WatchlistItemRow.id == item_id, WatchlistItemRow.user_id == user_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise WatchlistItemNotFoundError(item_id)
        lo: str | None
        if after_id is not None:
            lo = (
                await self._item_row(
                    user_id=user_id, watchlist_id=target_watchlist_id, item_id=after_id
                )
            ).sort_order
        else:
            last = await self._session.execute(
                select(WatchlistItemRow.sort_order)
                .where(WatchlistItemRow.watchlist_id == target_watchlist_id)
                .order_by(WatchlistItemRow.sort_order.desc())
                .limit(1)
            )
            lo = last.scalar_one_or_none()
        row.watchlist_id = target_watchlist_id
        row.market = target.market
        row.asset_class = target.asset_class
        row.sort_order = midpoint_key(lo, None)
        await self._session.flush()
        logger.info("watchlist_item_moved", user_id=user_id, item_id=item_id)
        return _item_to_model(row)

    async def snapshot(self, user_id: str) -> list[WatchlistSnapshotGroup]:
        """The read-through projection `memory.recall` renders into the compact memory block —
        one entry per non-deleted group, symbols in the group's own sort order."""
        groups = await self.list_groups(user_id)
        snapshots: list[WatchlistSnapshotGroup] = []
        for group in groups:
            items_result = await self._session.execute(
                select(WatchlistItemRow)
                .where(WatchlistItemRow.watchlist_id == group.id)
                .order_by(WatchlistItemRow.sort_order)
            )
            symbols = tuple(row.symbol for row in items_result.scalars().all())
            snapshots.append(
                WatchlistSnapshotGroup(
                    name=group.name,
                    market=group.market,
                    asset_class=group.asset_class,
                    symbols=symbols,
                )
            )
        return snapshots


__all__ = [
    "StaleWatchlistVersionError",
    "WatchlistGroup",
    "WatchlistItem",
    "WatchlistItemNotFoundError",
    "WatchlistNotFoundError",
    "WatchlistSnapshotGroup",
    "WatchlistStore",
    "WatchlistStoreProtocol",
    "midpoint_key",
]
