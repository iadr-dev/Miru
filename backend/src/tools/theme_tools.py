"""Theme/tier lookup tools over the relational theme store.

These are the agent's read surface onto the ingested industry-chain + concept-research theme
layer: what's in a theme, which themes a symbol belongs to, and a theme's upstream/midstream/
downstream tier breakdown. Unlike the market tools (which name a capability and fetch through the
router), these read already-stored relational data, so each takes the app's injected session
factory and opens one short read session per call — the caller never fetches a source live.

Classification/relationship metadata carries no scoring and no buy/sell framing, so it is `public`
at the tool level — the same tier at which `get_company_profile` returns its sector/industry
classification and `get_institutional_flow` returns its whole-market-ingested rows. The underlying
industry-chain source is licensed, but once ingested and stored relationally the derived theme
data follows the established convention that a derived-store read is classed by what it exposes,
not by its origin adapter's tier.

Concept-theme membership is AI-researched and carries a per-member `confidence`; industry-chain
membership is editorially sourced at `confidence` 1.0. Every member row surfaces `source` and
`confidence` so a caller can tell the two apart and never present a researched guess as an
editorially-guaranteed fact.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.themes.models import (
    SymbolTheme,
    ThemeKind,
    ThemeMember,
    ThemeTier,
    ThemeTierName,
)
from analytics.themes.store import SqlAlchemyThemeStore, ThemeStore
from schemas.enums import DataClass
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput

_DEFAULT_MEMBER_LIMIT = 200


class GetThemeMembersInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str = Field(min_length=1, description="Theme slug/key, e.g. '半導體'.")
    limit: int = Field(
        default=_DEFAULT_MEMBER_LIMIT,
        ge=1,
        le=1000,
        description="Maximum member rows to return (members are ordered by symbol).",
    )


class GetThemeMembersOutput(BaseModel):
    """A theme's identity plus its member symbols, each carrying the per-membership
    `sub_industry`/`confidence`/`source` so an AI-researched concept member is never presented as
    an editorially-guaranteed industry-chain one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str
    found: bool
    name_zh: str = ""
    name_en: str = ""
    kind: ThemeKind | None = None
    member_count: int = 0
    members: tuple[ThemeMember, ...] = Field(default_factory=tuple)
    message: str = ""


class GetThemeMembersTool(BaseTool):
    """`get_theme_members` — the symbols in a theme, with each membership's sub-industry,
    confidence, and provenance. An unknown key returns `found=False` with a clear message, never
    an error."""

    name = "get_theme_members"
    description = (
        "Get the member symbols of a theme (industry-chain or concept), each with its "
        "sub-industry, confidence score, and source (industry-chain vs AI-researched)."
    )
    input_model: type[BaseModel] = GetThemeMembersInput
    output_model: type[BaseModel] = GetThemeMembersOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "theme_members"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetThemeMembersInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            theme = await store.get_theme_by_key(key=typed.theme_key)
            if theme is None:
                return GetThemeMembersOutput(
                    theme_key=typed.theme_key,
                    found=False,
                    message=f"No theme found for key {typed.theme_key!r}.",
                )
            members = await store.get_theme_member_details(theme_key=typed.theme_key)
        return GetThemeMembersOutput(
            theme_key=theme.key,
            found=True,
            name_zh=theme.name_zh,
            name_en=theme.name_en,
            kind=theme.kind,
            member_count=len(members),
            members=tuple(members[: typed.limit]),
        )


class GetSymbolThemesOutput(BaseModel):
    """The reverse lookup — every theme a symbol belongs to, with that membership's own
    sub-industry and confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    theme_count: int = 0
    themes: tuple[SymbolTheme, ...] = Field(default_factory=tuple)


class GetSymbolThemesTool(BaseTool):
    """`get_symbol_themes` — the themes one symbol belongs to (the reverse of
    `get_theme_members`). Answers "what themes is 2330 in". An unknown symbol returns an empty
    theme list, never an error."""

    name = "get_symbol_themes"
    description = (
        "Get every theme a stock/ETF symbol belongs to (industry-chain and concept), with each "
        "membership's sub-industry and confidence — the reverse of get_theme_members."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetSymbolThemesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "symbol_themes"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(SymbolInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            themes = await store.get_symbol_themes(symbol=typed.id, market=typed.market.value)
        return GetSymbolThemesOutput(
            symbol=typed.id,
            market=typed.market.value,
            theme_count=len(themes),
            themes=tuple(themes),
        )


class GetThemeTiersInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str = Field(min_length=1, description="Theme slug/key, e.g. '半導體'.")


class GetThemeTiersOutput(BaseModel):
    """A theme's sub-industry tier breakdown, grouped into upstream/midstream/downstream. Each
    tier assignment carries its `source` (curated editorial map vs keyword heuristic) so a caller
    can tell an editorially-verified position from an automatically-classified one and never
    present a heuristic guess as equally authoritative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str
    found: bool
    upstream: tuple[ThemeTier, ...] = Field(default_factory=tuple)
    midstream: tuple[ThemeTier, ...] = Field(default_factory=tuple)
    downstream: tuple[ThemeTier, ...] = Field(default_factory=tuple)
    message: str = ""


class GetThemeTiersTool(BaseTool):
    """`get_theme_tiers` — a theme's sub-industries grouped by upstream/midstream/downstream
    position, each assignment disclosing whether an editor (curated) or a keyword heuristic
    assigned it. An unknown key returns `found=False`, never an error."""

    name = "get_theme_tiers"
    description = (
        "Get a theme's sub-industries grouped into upstream/midstream/downstream tiers, each "
        "assignment marked curated (editorial) or heuristic (automatically classified)."
    )
    input_model: type[BaseModel] = GetThemeTiersInput
    output_model: type[BaseModel] = GetThemeTiersOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "theme_tiers"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetThemeTiersInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            theme = await store.get_theme_by_key(key=typed.theme_key)
            if theme is None:
                return GetThemeTiersOutput(
                    theme_key=typed.theme_key,
                    found=False,
                    message=f"No theme found for key {typed.theme_key!r}.",
                )
            tiers = await store.get_theme_tiers(theme_key=typed.theme_key)
        by_tier: dict[ThemeTierName, list[ThemeTier]] = {name: [] for name in ThemeTierName}
        for tier in tiers:
            by_tier[tier.tier].append(tier)
        return GetThemeTiersOutput(
            theme_key=theme.key,
            found=True,
            upstream=tuple(by_tier[ThemeTierName.UPSTREAM]),
            midstream=tuple(by_tier[ThemeTierName.MIDSTREAM]),
            downstream=tuple(by_tier[ThemeTierName.DOWNSTREAM]),
        )


def build_registration_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table. All three are deferred long-tail
    reads. Unlike the router-backed tool groups, these read the relational theme store, so this
    table takes the app's session factory rather than the data-access router."""
    tools: list[tuple[Tool, bool]] = [
        (GetThemeMembersTool(session_factory), False),
        (GetSymbolThemesTool(session_factory), False),
        (GetThemeTiersTool(session_factory), False),
    ]
    return tools


__all__ = [
    "GetSymbolThemesOutput",
    "GetSymbolThemesTool",
    "GetThemeMembersInput",
    "GetThemeMembersOutput",
    "GetThemeMembersTool",
    "GetThemeTiersInput",
    "GetThemeTiersOutput",
    "GetThemeTiersTool",
    "build_registration_table",
]
