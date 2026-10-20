"""Step B: targets, extraction into raw claims, budgets, dry run. Stub search and model only."""

from __future__ import annotations

import json
from pathlib import Path

from analytics.supply_chain.theme_research.files import (
    ResearchState,
    claims_path,
    read_claims,
    read_state,
    state_path,
)
from analytics.supply_chain.theme_research.job import execute_research, format_research
from analytics.supply_chain.theme_research.models import ThemeTarget
from analytics.supply_chain.theme_research.research import (
    ResearchConfig,
    ThemeResearcher,
    run_research,
)
from analytics.supply_chain.theme_research.targets import queries_for, select_targets
from analytics.supply_chain.verification.budget import Budget, Limits
from ingest.store import ParquetStore
from tests.analytics.supply_chain.theme_research.support import (
    CANDIDATE_ID,
    COMPANIES,
    THEME_KEY,
    THEME_ZH,
    TODAY,
    directory,
    make_root,
    settings,
)
from tests.analytics.supply_chain.verification.support import StubSearch, hit, llm

URL = "https://news.example.com/theme"
ZETA = COMPANIES["9101"][0]
ETA = COMPANIES["9102"][0]
LINE_ZETA = f"{ZETA}是{THEME_ZH}的重要供應商。"
LINE_ETA = f"{ETA}已切入{THEME_ZH}供應鏈。"
PAGE = f"產業新聞 2026-09-01\n\n{LINE_ZETA}\n{LINE_ETA}\n與此無關的內容。"


def item(company: str, excerpt: str, **kw: object) -> dict[str, object]:
    return {"company": company, "symbol": None, "excerpt": excerpt, "position": "midstream"} | kw


def reply(*items: dict[str, object]) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def researcher(search: StubSearch, *replies: str, limits: Limits | None = None) -> ThemeResearcher:
    return ThemeResearcher(
        search=search,
        client=llm(*replies),
        directory=directory(),
        budget=Budget(limits or Limits()),
        today=TODAY,
        config=ResearchConfig(),
        trust_table=None,
    )


def theme_target() -> ThemeTarget:
    return ThemeTarget(
        target_id=THEME_KEY,
        kind="existing",
        label=THEME_ZH,
        names=(THEME_ZH, "Demo Theme"),
        members=frozenset({"9100"}),
    )


def stub() -> StubSearch:
    return StubSearch([hit(URL)], {URL: PAGE})


async def go(run: ThemeResearcher, root: Path, *, dry_run: bool = False, state=None):  # type: ignore[no-untyped-def]
    return await run_research(
        [theme_target()],
        run,
        root=root,
        state=state or ResearchState({}, {}),
        today=TODAY,
        dry_run=dry_run,
    )


async def test_extraction_becomes_claims_and_an_unresolved_name_is_dropped(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    search = stub()
    run = researcher(
        search,
        reply(
            item(ZETA, LINE_ZETA),
            item("虛構公司", f"虛構公司也屬於{THEME_ZH}。"),
            item(ETA, LINE_ETA, position="bogus"),
        ),
    )
    summary = await go(run, root)
    claims = read_claims(claims_path(root))
    assert {c.symbol for c in claims} == {"9101", "9102"}
    first = next(c for c in claims if c.symbol == "9101")
    assert first.theme == THEME_KEY and first.kind == "existing" and first.url == URL
    assert first.publisher == "news.example.com" and first.retrieved_at == TODAY.isoformat()
    assert first.excerpt == LINE_ZETA and first.position == "midstream"
    assert next(c for c in claims if c.symbol == "9102").position == "related"
    assert summary.claims == 2 and summary.dropped == {"unresolved_company": 1}
    assert search.queries == [f"{THEME_ZH} 概念股", f"{THEME_ZH} 供應鏈 台灣"]


async def test_an_excerpt_not_in_the_page_and_a_known_member_are_dropped(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    member = COMPANIES["9100"][0]
    run = researcher(
        stub(),
        reply(
            item(ZETA, f"{ZETA}與{THEME_ZH}是頁面沒有的句子。"),
            item(member, f"{member}屬於{THEME_ZH}。"),
        ),
    )
    summary = await go(run, root)
    assert summary.claims == 0
    assert summary.dropped == {"excerpt_not_in_page": 1, "already_member": 1}


async def test_rerun_is_idempotent_and_dedupes_on_theme_symbol_url(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    state = ResearchState({}, {})
    for expected_new, expected_dup in ((2, 0), (0, 2)):
        run = researcher(stub(), reply(item(ZETA, LINE_ZETA), item(ETA, LINE_ETA)))
        summary = await go(run, root, state=state)
        assert (summary.claims, summary.duplicates) == (expected_new, expected_dup)
    assert len(read_claims(claims_path(root))) == 2
    assert read_state(state_path(root)).themes == {THEME_KEY: TODAY.isoformat()}


async def test_dry_run_searches_and_judges_but_writes_nothing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    search = stub()
    summary = await go(researcher(search, reply(item(ZETA, LINE_ZETA))), root, dry_run=True)
    assert summary.claims == 1 and summary.dry_run and search.queries
    assert not claims_path(root).exists() and not state_path(root).exists()
    assert "would_write=1" in summary.line()


async def test_search_down_is_skipped_and_not_marked_researched(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    summary = await go(researcher(StubSearch(None)), root)
    assert summary.outcomes == {THEME_KEY: "skipped:search_unavailable"}
    assert not state_path(root).exists()


async def test_a_stale_page_or_one_without_the_theme_never_reaches_the_model(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    old = "https://news.example.com/old"
    off = "https://news.example.com/off"
    search = StubSearch(
        [hit(old, "2019-01-01"), hit(off)], {old: PAGE, off: "完全無關的頁面 2026-09-01"}
    )
    summary = await go(researcher(search), root)
    assert summary.dropped == {"source_too_old": 1, "theme_not_on_page": 1}
    assert summary.llm_calls == 0 and summary.claims == 0


async def test_an_undated_page_is_judged_by_the_date_in_its_url(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    old = "https://news.example.com/article/category/1/post/201408140"
    new = "https://news.example.com/2026/09/01/theme"
    body = f"{LINE_ZETA}\n{LINE_ETA}"
    search = StubSearch([hit(old), hit(new)], {old: body, new: body})
    summary = await go(researcher(search, reply()), root)
    assert summary.dropped == {"source_too_old": 1}
    assert summary.llm_calls == 1


async def test_claim_cap_stops_the_run_after_the_first_target(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    second = theme_target().model_copy(update={"target_id": "other"})
    run = researcher(stub(), reply(), reply(), limits=Limits(max_claims=1))
    summary = await run_research(
        [theme_target(), second],
        run,
        root=root,
        state=ResearchState({}, {}),
        today=TODAY,
        dry_run=False,
    )
    assert summary.targets == 1 and summary.stopped_by == "claims"


async def test_search_cap_stops_the_run_gracefully(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run = researcher(stub(), reply(item(ZETA, LINE_ZETA)), limits=Limits(max_searches=1))
    summary = await go(run, root)
    assert summary.searches == 1 and summary.stopped_by == "searches"
    assert summary.claims == 1


async def test_llm_cap_skips_the_target_without_marking_it(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    summary = await go(researcher(stub(), limits=Limits(max_llm_calls=0)), root)
    assert summary.stopped_by == "llm_calls"
    assert summary.outcomes == {THEME_KEY: "skipped:budget"}
    assert not state_path(root).exists()


def test_targets_candidates_first_then_least_recently_researched_themes(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    concepts = root / "themes" / "concepts"
    body = (concepts / f"{THEME_KEY}.yaml").read_text(encoding="utf-8")
    (concepts / "zzz.yaml").write_text(
        body.replace(THEME_KEY, "zzz").replace(THEME_ZH, "另一題材").replace("Demo Theme", "Zzz"),
        encoding="utf-8",
    )
    state = ResearchState({THEME_KEY: "2026-10-01"}, {})
    picked = select_targets(root, state, source="all", limit=2)
    assert [t.target_id for t in picked] == [CANDIDATE_ID, "zzz"]
    assert [t.kind for t in picked] == ["candidate", "existing"]
    existing = select_targets(root, state, source="existing", limit=5)
    assert [t.target_id for t in existing] == ["zzz", THEME_KEY]
    done = ResearchState({}, {CANDIDATE_ID: "2026-10-10"})
    assert select_targets(root, done, source="candidates", limit=5) == []
    assert queries_for(picked[0]) == ["Quantum Widget 概念股", "Quantum Widget 供應鏈 台灣"]
    assert "量子小工具" in picked[0].names


async def test_execute_research_end_to_end_and_console_has_no_names(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    summary = await execute_research(
        settings(),
        ParquetStore(tmp_path / "parquet"),  # type: ignore[arg-type]
        today=TODAY,
        source="existing",
        limit=3,
        dry_run=False,
        search=stub(),
        client=llm(reply(item(ZETA, LINE_ZETA))),
        directory=directory(),
        root=root,
    )
    out = format_research(summary)
    assert f"{THEME_KEY} researched:1" in out
    for text in (ZETA, "Zeta Optics", LINE_ZETA, PAGE, "歐米克戎工業"):
        assert text not in out
