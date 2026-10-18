"""`ConceptStore` over one `AsyncSession`. Never commits; the caller owns the transaction."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterator, Sequence
from typing import cast

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.supply_chain.domain.concepts import (
    POSITION_LABEL_ZH,
    ConceptMember,
    ConceptOrigin,
    ConceptPosition,
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
)
from analytics.supply_chain.domain.models import MarketCode
from analytics.supply_chain.store.concept_protocol import (
    ConceptCollisionError,
    ConceptCounts,
    ConceptInfo,
    ConceptReplaceResult,
    EntityReplaceResult,
)
from analytics.themes.models import ThemeKind, ThemeMember, ThemeSource
from analytics.themes.store import SqlAlchemyThemeStore
from db.models import (
    ScConceptMemberRow,
    ScConceptThemeRow,
    ScEntityMentionRow,
    ScEntityRow,
    ThemeMembershipRow,
    ThemeRow,
)

_CHUNK = 500
# Mirrored memberships are curated by file, but not hand-verified: a mid confidence.
MIRROR_CONFIDENCE = 0.6
MIRROR_SOURCE = ThemeSource.CONCEPT_RESEARCH

type MentionKey = tuple[str, str, str, str]


def _chunks[T](items: Sequence[T]) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


def _entity_of(row: ScEntityRow) -> Entity:
    return Entity(
        id=row.id,
        type=EntityType(row.type),
        symbol=row.symbol,
        market=cast("MarketCode | None", row.market),
        aliases=tuple(row.aliases),
        mentions=row.mentions,
        reports=row.reports,
    )


class SqlAlchemyConceptStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------------------------------------------------- concept themes

    async def replace_concept_themes(self, themes: Sequence[ConceptTheme]) -> ConceptReplaceResult:
        new_keys = {t.key for t in themes}
        await self._reject_industry_collisions(new_keys)
        existing = {
            row.key: row for row in (await self._session.scalars(select(ScConceptThemeRow))).all()
        }
        created = 0
        for theme in themes:
            row = existing.get(theme.key)
            if row is None:
                row = ScConceptThemeRow(id=str(uuid.uuid4()), key=theme.key, name_zh=theme.name_zh)
                self._session.add(row)
                created += 1
            row.name_zh = theme.name_zh
            row.name_en = theme.name_en
            row.description = theme.description
            row.related = list(theme.related)
        await self._session.flush()
        for theme in themes:
            await self._replace_members(theme)
            await self._mirror(theme)
        removed = sorted(set(existing) - new_keys)
        await self._delete_themes(removed)
        return ConceptReplaceResult(
            themes=len(themes),
            members=sum(len(t.members) for t in themes),
            created=created,
            deleted=len(removed),
        )

    async def _reject_industry_collisions(self, keys: Collection[str]) -> None:
        clash = list(
            (
                await self._session.scalars(
                    select(ThemeRow.key).where(
                        ThemeRow.kind == ThemeKind.INDUSTRY.value, ThemeRow.key.in_(list(keys))
                    )
                )
            ).all()
        )
        if clash:
            raise ConceptCollisionError(
                f"concept theme key(s) already used by an industry theme: {sorted(clash)}"
            )

    async def _replace_members(self, theme: ConceptTheme) -> None:
        await self._session.execute(
            delete(ScConceptMemberRow).where(ScConceptMemberRow.theme_key == theme.key)
        )
        self._session.add_all(
            ScConceptMemberRow(
                id=str(uuid.uuid4()),
                theme_key=theme.key,
                symbol=m.symbol,
                market=m.market,
                name=m.name,
                position=m.position.value,
                origin=m.origin.value,
            )
            for m in theme.members
        )
        await self._session.flush()

    async def _mirror(self, theme: ConceptTheme) -> None:
        """Mirror into the generic theme tables so `get_themes`, `get_theme_members` and the
        screener chips list the concept theme. The theme's memberships are replaced whole."""
        store = SqlAlchemyThemeStore(self._session)
        row = await store.upsert_theme(
            key=theme.key,
            name_zh=theme.name_zh,
            name_en=theme.name_en,
            kind=ThemeKind.CONCEPT,
            source=MIRROR_SOURCE,
        )
        await self._session.execute(
            delete(ThemeMembershipRow).where(ThemeMembershipRow.theme_id == row.id)
        )
        await store.replace_theme_members(
            theme_key=theme.key,
            source=MIRROR_SOURCE,
            members=[
                ThemeMember(
                    symbol=m.symbol,
                    market=m.market,
                    sub_industry=POSITION_LABEL_ZH[m.position],
                    confidence=MIRROR_CONFIDENCE,
                    source=MIRROR_SOURCE,
                )
                for m in theme.members
            ],
        )

    async def _delete_themes(self, keys: Sequence[str]) -> None:
        for chunk in _chunks(keys):
            ids = list(
                (
                    await self._session.scalars(
                        select(ThemeRow.id).where(
                            ThemeRow.key.in_(list(chunk)),
                            ThemeRow.kind == ThemeKind.CONCEPT.value,
                        )
                    )
                ).all()
            )
            if ids:
                await self._session.execute(
                    delete(ThemeMembershipRow).where(ThemeMembershipRow.theme_id.in_(ids))
                )
                await self._session.execute(delete(ThemeRow).where(ThemeRow.id.in_(ids)))
            await self._session.execute(
                delete(ScConceptMemberRow).where(ScConceptMemberRow.theme_key.in_(list(chunk)))
            )
            await self._session.execute(
                delete(ScConceptThemeRow).where(ScConceptThemeRow.key.in_(list(chunk)))
            )

    async def concept_info(self, keys: Collection[str] | None = None) -> dict[str, ConceptInfo]:
        rows = (await self._session.scalars(select(ScConceptThemeRow))).all()
        by_name: dict[str, str] = {}
        for row in rows:
            by_name[row.key] = row.key
        for row in rows:
            by_name.setdefault(row.name_zh, row.key)
        wanted = set(keys) if keys is not None else None
        out: dict[str, ConceptInfo] = {}
        for row in rows:
            if wanted is not None and row.key not in wanted:
                continue
            related = dict.fromkeys(
                by_name[name]
                for name in row.related
                if name in by_name and by_name[name] != row.key
            )
            out[row.key] = ConceptInfo(description=row.description, related_keys=tuple(related))
        return out

    async def concept_positions(self, theme_key: str) -> dict[tuple[str, str], ConceptPosition]:
        rows = await self._session.execute(
            select(
                ScConceptMemberRow.symbol, ScConceptMemberRow.market, ScConceptMemberRow.position
            )
            .where(ScConceptMemberRow.theme_key == theme_key)
            .order_by(ScConceptMemberRow.symbol)
        )
        return {(s, m): ConceptPosition(p) for s, m, p in rows.all()}

    async def concept_members(self, theme_key: str) -> list[ConceptMember]:
        rows = (
            await self._session.scalars(
                select(ScConceptMemberRow)
                .where(ScConceptMemberRow.theme_key == theme_key)
                .order_by(ScConceptMemberRow.symbol)
            )
        ).all()
        return [
            ConceptMember(
                symbol=r.symbol,
                market=cast("MarketCode", r.market),
                name=r.name,
                position=ConceptPosition(r.position),
                origin=ConceptOrigin(r.origin),
            )
            for r in rows
        ]

    # ----------------------------------------------------------------------- entities

    async def replace_entities(
        self, entities: Sequence[Entity], mentions: Sequence[EntityMention]
    ) -> EntityReplaceResult:
        new_ids = {e.id for e in entities}
        existing = {row.id: row for row in (await self._session.scalars(select(ScEntityRow))).all()}
        for entity in entities:
            row = existing.get(entity.id)
            if row is None:
                row = ScEntityRow(id=entity.id, type=entity.type.value)
                self._session.add(row)
            row.type = entity.type.value
            row.symbol = entity.symbol
            row.market = entity.market
            row.aliases = list(entity.aliases)
            row.mentions = entity.mentions
            row.reports = entity.reports
        await self._session.flush()
        gone = sorted(set(existing) - new_ids)
        for chunk in _chunks(gone):
            await self._session.execute(
                delete(ScEntityMentionRow).where(ScEntityMentionRow.entity_id.in_(list(chunk)))
            )
            await self._session.execute(delete(ScEntityRow).where(ScEntityRow.id.in_(list(chunk))))
        created, deleted = await self._replace_mentions(mentions)
        return EntityReplaceResult(
            entities=len(entities),
            mentions=len(mentions),
            created_mentions=created,
            deleted_entities=len(gone),
            deleted_mentions=deleted,
        )

    async def _replace_mentions(self, mentions: Sequence[EntityMention]) -> tuple[int, int]:
        current: dict[MentionKey, tuple[str, int]] = {}
        result = await self._session.execute(
            select(
                ScEntityMentionRow.id,
                ScEntityMentionRow.symbol,
                ScEntityMentionRow.entity_id,
                ScEntityMentionRow.section,
                ScEntityMentionRow.role_key,
                ScEntityMentionRow.count,
            )
        )
        for row_id, symbol, entity_id, section, role_key, count in result.all():
            current[(symbol, entity_id, section, role_key)] = (row_id, count)
        wanted = {m.key: m for m in mentions}
        inserts = [
            {
                "id": str(uuid.uuid4()),
                "symbol": m.symbol,
                "entity_id": m.entity_id,
                "section": m.section.value,
                "role": m.role.value if m.role else None,
                "role_key": m.role.value if m.role else "",
                "count": m.count,
            }
            for key, m in wanted.items()
            if key not in current
        ]
        for chunk in _chunks(inserts):
            await self._session.execute(insert(ScEntityMentionRow), list(chunk))
        for key, mention in wanted.items():
            known = current.get(key)
            if known is not None and known[1] != mention.count:
                await self._session.execute(
                    update(ScEntityMentionRow)
                    .where(ScEntityMentionRow.id == known[0])
                    .values(count=mention.count)
                )
        doomed = [row_id for key, (row_id, _n) in current.items() if key not in wanted]
        for ids in _chunks(doomed):
            await self._session.execute(
                delete(ScEntityMentionRow).where(ScEntityMentionRow.id.in_(list(ids)))
            )
        return len(inserts), len(doomed)

    async def entity_totals(self, symbol: str) -> dict[str, int]:
        result = await self._session.execute(
            select(ScEntityMentionRow.entity_id, func.sum(ScEntityMentionRow.count))
            .where(ScEntityMentionRow.symbol == symbol)
            .group_by(ScEntityMentionRow.entity_id)
        )
        return {entity_id: int(total or 0) for entity_id, total in result.all()}

    async def mentions_of_entities(self, entity_ids: Collection[str]) -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        for chunk in _chunks(sorted(entity_ids)):
            result = await self._session.execute(
                select(
                    ScEntityMentionRow.symbol,
                    ScEntityMentionRow.entity_id,
                    func.sum(ScEntityMentionRow.count),
                )
                .where(ScEntityMentionRow.entity_id.in_(list(chunk)))
                .group_by(ScEntityMentionRow.symbol, ScEntityMentionRow.entity_id)
            )
            out.extend((s, e, int(n or 0)) for s, e, n in result.all())
        return out

    async def distinct_entity_counts(self, symbols: Collection[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for chunk in _chunks(sorted(symbols)):
            result = await self._session.execute(
                select(
                    ScEntityMentionRow.symbol,
                    func.count(func.distinct(ScEntityMentionRow.entity_id)),
                )
                .where(ScEntityMentionRow.symbol.in_(list(chunk)))
                .group_by(ScEntityMentionRow.symbol)
            )
            out.update({s: int(n) for s, n in result.all()})
        return out

    async def get_entities(self, ids: Collection[str]) -> dict[str, Entity]:
        out: dict[str, Entity] = {}
        for chunk in _chunks(sorted(ids)):
            rows = (
                await self._session.scalars(
                    select(ScEntityRow).where(ScEntityRow.id.in_(list(chunk)))
                )
            ).all()
            out.update({r.id: _entity_of(r) for r in rows})
        return out

    async def entities_by_symbol(self, symbols: Collection[str]) -> dict[tuple[str, str], Entity]:
        out: dict[tuple[str, str], Entity] = {}
        for chunk in _chunks(sorted(symbols)):
            rows = (
                await self._session.scalars(
                    select(ScEntityRow)
                    .where(ScEntityRow.symbol.in_(list(chunk)))
                    .order_by(ScEntityRow.mentions.desc(), ScEntityRow.id)
                )
            ).all()
            for row in rows:
                if row.symbol is not None and row.market is not None:
                    out.setdefault((row.symbol, row.market), _entity_of(row))
        return out

    async def counts(self) -> ConceptCounts:
        async def total(
            model: type[ScConceptThemeRow | ScConceptMemberRow | ScEntityRow | ScEntityMentionRow],
        ) -> int:
            return int(await self._session.scalar(select(func.count()).select_from(model)) or 0)

        by_type = await self._session.execute(
            select(ScEntityRow.type, func.count()).group_by(ScEntityRow.type)
        )
        return ConceptCounts(
            concept_themes=await total(ScConceptThemeRow),
            concept_members=await total(ScConceptMemberRow),
            entities=await total(ScEntityRow),
            entity_mentions=await total(ScEntityMentionRow),
            entities_by_type={str(t): int(n) for t, n in by_type.all()},
        )


__all__ = ["MIRROR_CONFIDENCE", "MIRROR_SOURCE", "SqlAlchemyConceptStore"]
