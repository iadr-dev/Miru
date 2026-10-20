"""Synthetic fixtures for theme research: invented companies, symbols and example hosts only."""

from __future__ import annotations

import json
from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import yaml

from analytics.supply_chain.domain.concepts import Entity, EntityType
from analytics.supply_chain.theme_research.files import append_claims, claims_path
from analytics.supply_chain.theme_research.models import Position, TargetKind, ThemeClaim
from analytics.supply_chain.verification.directory import CompanyDirectory, build_directory
from config.settings import Settings
from ingest.context import IngestContext
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

TODAY = date(2026, 10, 18)

THEME_KEY = "demo-theme"
THEME_ZH = "示範題材"
THEME_EN = "Demo Theme"
CANDIDATE_ID = "tc-0123456789ab"
CANDIDATE_LABEL = "Quantum Widget"

# symbol -> (zh name, en name); 9100 is already a member of the demo theme, 9199 is a company the
# directory knows but the entity catalog does not (outside the universe).
COMPANIES: dict[str, tuple[str, str]] = {
    "9100": ("歐米克戎工業", "Omicron Works"),
    "9101": ("澤塔光學", "Zeta Optics Co., Ltd."),
    "9102": ("艾塔電池", "Eta Cells"),
    "9103": ("西塔晶圓", "Theta Wafers"),
    "9104": ("約塔電路", "Iota Boards"),
    "9105": ("卡帕鏡頭", "Kappa Lenses"),
    "9106": ("拉姆達光纖", "Lambda Fibers"),
    "9107": ("繆感測", "Mu Sensors"),
    "9108": ("紐繼電器", "Nu Relays"),
    "9109": ("克賽封裝", "Xi Packaging"),
    "9110": ("奧米克磁材", "Omik Magnets"),
    "9111": ("派連接器", "Pi Connectors"),
    "9112": ("洛散熱", "Rho Coolers"),
}
OUTSIDE = ("9199", "洛幽靈", "Rho Ghost")

SECONDARY_HOST = "news.example.com"
PRIMARY_HOST = "filings.example.gov"
TERTIARY_A = "https://a.example.org/p"
TERTIARY_B = "https://b.example.net/p"


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "supply_chain_research_enabled": True,
        "brave_search_api_key": "k",
        "finmind_api_key": "t",
    }
    return Settings(**(base | kw))  # type: ignore[arg-type]


def directory() -> CompanyDirectory:
    roster = {symbol: zh for symbol, (zh, _) in COMPANIES.items()}
    roster[OUTSIDE[0]] = OUTSIDE[1]
    entities = [
        Entity(id=zh, type=EntityType.TW_COMPANY, symbol=symbol, market="TW", aliases=(en,))
        for symbol, (zh, en) in COMPANIES.items()
    ]
    return build_directory(roster=roster, market_values={}, entities=entities)


def make_root(tmp_path: Path) -> Path:
    """A reference directory: catalog, one concept theme, one theme candidate, trust table."""
    root = tmp_path / "ref"
    (root / "entities").mkdir(parents=True)
    catalog: list[dict[str, object]] = [
        {"id": zh, "type": "tw_company", "symbol": symbol, "aliases": [en]}
        for symbol, (zh, en) in COMPANIES.items()
    ]
    catalog.append({"id": CANDIDATE_LABEL, "type": "technology", "aliases": ["量子小工具"]})
    (root / "entities" / "catalog.yaml").write_text(
        yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8"
    )
    concepts = root / "themes" / "concepts"
    concepts.mkdir(parents=True)
    theme = {
        "key": THEME_KEY,
        "name_zh": THEME_ZH,
        "name_en": THEME_EN,
        "description": "",
        "related": [],
        "members": [
            {"symbol": "9100", "name": "歐米克戎工業", "position": "upstream", "origin": "seeded"}
        ],
    }
    (concepts / f"{THEME_KEY}.yaml").write_text(
        yaml.safe_dump(theme, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    candidate = {
        "candidate_id": CANDIDATE_ID,
        "entity_id": CANDIDATE_LABEL,
        "label": CANDIDATE_LABEL,
        "type": "technology",
        "company_count": 7,
        "total_mentions": 9,
        "specificity": 3.0,
        "top_companies": ["9101"],
        "sections": ["business"],
        "first_seen": "2026-10-01",
        "trend": "new",
        "trend_delta": 0,
    }
    (root / "themes" / "theme_candidates.jsonl").write_text(
        json.dumps(candidate) + "\n", encoding="utf-8"
    )
    (root / "supply_chain").mkdir()
    (root / "supply_chain" / "publisher_trust.yaml").write_text(
        f"{SECONDARY_HOST}: secondary\n{PRIMARY_HOST}: primary\n", encoding="utf-8"
    )
    return root


def claim(  # noqa: PLR0913 - a test builder with one knob per field
    symbol: str = "9101",
    *,
    theme: str = THEME_KEY,
    kind: TargetKind = "existing",
    label: str = THEME_ZH,
    excerpt: str | None = None,
    url: str = f"https://{SECONDARY_HOST}/a",
    position: Position = "midstream",
    published_on: str | None = "2026-09-01",
) -> ThemeClaim:
    zh = COMPANIES[symbol][0]
    return ThemeClaim(
        theme=theme,
        kind=kind,
        label=label,
        symbol=symbol,
        excerpt=excerpt if excerpt is not None else f"{zh}是{label}的重要供應商。",
        url=url,
        publisher=url.split("/")[2],
        retrieved_at=TODAY.isoformat(),
        position=position,
        published_on=published_on,
    )


def candidate_claim(symbol: str, **kw: object) -> ThemeClaim:
    return claim(
        symbol,
        theme=CANDIDATE_ID,
        kind="candidate",
        label=CANDIDATE_LABEL,
        **kw,  # type: ignore[arg-type]
    )


def seed_claims(root: Path, claims: list[ThemeClaim]) -> None:
    append_claims(claims_path(root), claims)


def make_ctx(tmp_path: Path, leader: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path / "parquet"),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )
