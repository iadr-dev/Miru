"""Step C: the quote guard, trust and independence, staleness, membership, thresholds, proposal
files that `promote` accepts, atomic and idempotent writes, caps. Synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from analytics.supply_chain.theme_research.files import (
    read_rejected_keys,
    rejected_path,
)
from analytics.supply_chain.theme_research.job import execute_verify, format_verify
from analytics.supply_chain.theme_research.models import ThemeClaim, VerifySummary
from analytics.supply_chain.theme_research.proposals import proposal_paths, scan_proposed
from analytics.supply_chain.themes_io.promote import IndustryRoster, run_promote
from config.settings import Settings
from ingest.store import ParquetStore
from tests.analytics.supply_chain.theme_research.support import (
    CANDIDATE_ID,
    CANDIDATE_LABEL,
    COMPANIES,
    OUTSIDE,
    PRIMARY_HOST,
    SECONDARY_HOST,
    TERTIARY_A,
    TERTIARY_B,
    THEME_EN,
    THEME_KEY,
    THEME_ZH,
    TODAY,
    candidate_claim,
    claim,
    directory,
    make_root,
    seed_claims,
    settings,
)
from tests.analytics.supply_chain.verification.support import llm

ZETA_ZH, ZETA_EN = COMPANIES["9101"]
_FULLWIDTH_OFFSET = 0xFEE0


def wide_text(text: str) -> str:
    """Full-width letters and ideographic spaces, as scraped pages may carry them."""
    out = [chr(ord(c) + _FULLWIDTH_OFFSET) if c.isascii() and c.isalpha() else c for c in text]
    return "".join(out).replace(" ", "　")


async def verify(  # noqa: PLR0913 - a test helper with one knob per option
    root: Path,
    tmp_path: Path,
    *,
    cfg: Settings | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    second: object = None,
) -> VerifySummary:
    return await execute_verify(
        cfg or settings(),
        ParquetStore(tmp_path / "parquet"),  # type: ignore[arg-type]
        today=TODAY,
        limit=limit,
        dry_run=dry_run,
        second_client=second,  # type: ignore[arg-type]
        directory=directory(),
        root=root,
    )


def members_rows(root: Path) -> list[dict[str, Any]]:
    path = proposal_paths(root, TODAY).members
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []


def reasons_logged(root: Path) -> dict[str, str]:
    path = rejected_path(root)
    if not path.exists():
        return {}
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    return {f"{r['theme']}:{r['symbol']}": r["reason"] for r in lines}


async def rejected_reason(root: Path, tmp_path: Path, c: ThemeClaim) -> str:
    seed_claims(root, [c])
    summary = await verify(root, tmp_path)
    assert summary.verified == 0 and summary.rejected == 1
    return reasons_logged(root)[f"{c.theme}:{c.symbol}"]


async def test_quote_guard_needs_both_the_company_and_the_theme(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    only_company = claim("9101", excerpt=f"{ZETA_ZH}今年營收成長。")
    assert await rejected_reason(root, tmp_path, only_company) == "theme_not_in_quote"
    only_theme = claim("9102", excerpt=f"{THEME_ZH}需求持續擴大。")
    assert await rejected_reason(root, tmp_path, only_theme) == "company_not_in_quote"
    both = claim("9103")
    seed_claims(root, [both])
    assert (await verify(root, tmp_path)).verified == 1


async def test_quote_guard_ignores_case_and_width_and_accepts_aliases(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    text = f"{wide_text('ZETA OPTICS')} 受惠於 {wide_text('demo theme').upper()} 需求。"
    wide = claim("9101", excerpt=text)
    seed_claims(root, [wide])
    summary = await verify(root, tmp_path)
    assert summary.verified == 1
    [row] = members_rows(root)
    assert row["theme_key"] == THEME_KEY and row["symbol"] == "9101"


async def test_quote_guard_rejects_long_excerpts_http_urls_and_rejected_hosts(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    long_text = f"{ZETA_ZH}與{THEME_ZH}" + "的" * 200
    assert await rejected_reason(root, tmp_path, claim("9101", excerpt=long_text)) == (
        "excerpt_too_long"
    )
    http = claim("9102", url=f"http://{SECONDARY_HOST}/a")
    assert await rejected_reason(root, tmp_path, http) == "url_not_https"
    banned = claim("9103", url="https://www.ptt.cc/bbs/x")
    assert await rejected_reason(root, tmp_path, banned) == "host_rejected"


async def test_a_stale_source_is_rejected_but_an_undated_one_is_not(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    assert await rejected_reason(root, tmp_path, claim("9101", published_on="2020-01-01")) == (
        "source_too_old"
    )
    seed_claims(root, [claim("9102", published_on=None)])
    assert (await verify(root, tmp_path)).verified == 1


async def test_a_single_tertiary_source_stays_a_candidate_and_is_not_written(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101", url=TERTIARY_A)])
    summary = await verify(root, tmp_path)
    assert (summary.verified, summary.candidates, summary.member_rows) == (0, 1, 0)
    assert members_rows(root) == [] and reasons_logged(root) == {}


async def test_two_independent_tertiary_domains_verify_with_medium_confidence(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101", url=TERTIARY_A), claim("9101", url=TERTIARY_B)])
    summary = await verify(root, tmp_path)
    assert summary.verified == 1 and summary.member_rows == 1
    [row] = members_rows(root)
    assert row["confidence"] == "medium"


async def test_subdomains_of_one_site_count_once(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(
        root,
        [
            claim("9101", url="https://x.cheap.example.org/1"),
            claim("9101", url="https://y.cheap.example.org/2"),
        ],
    )
    summary = await verify(root, tmp_path)
    assert (summary.verified, summary.candidates) == (0, 1)


async def test_a_secondary_or_primary_source_verifies_with_high_confidence(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), claim("9102", url=f"https://{PRIMARY_HOST}/f")])
    summary = await verify(root, tmp_path)
    assert summary.verified == 2
    assert {r["confidence"] for r in members_rows(root)} == {"high"}


async def test_an_existing_member_is_skipped_and_logged(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    member = claim("9100")
    assert await rejected_reason(root, tmp_path, member) == "already_member"


async def test_a_symbol_outside_the_catalog_universe_is_rejected(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    ghost = ThemeClaim(
        theme=THEME_KEY,
        kind="existing",
        label=THEME_ZH,
        symbol=OUTSIDE[0],
        excerpt=f"{OUTSIDE[1]}是{THEME_ZH}供應商。",
        url=f"https://{SECONDARY_HOST}/g",
        publisher=SECONDARY_HOST,
        retrieved_at=TODAY.isoformat(),
        published_on="2026-09-01",
    )
    assert await rejected_reason(root, tmp_path, ghost) == "not_in_universe"


async def test_an_unknown_theme_key_is_rejected(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    assert await rejected_reason(root, tmp_path, claim("9101", theme="gone")) == "unknown_theme"


async def test_second_opinion_can_demote_a_single_weak_source(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101")])
    cfg = settings(supply_chain_second_opinion=True)
    no = await verify(root, tmp_path, cfg=cfg, second=llm('{"supports": false}'), dry_run=True)
    assert (no.verified, no.candidates, no.llm_calls) == (0, 1, 1)
    assert no.reasons == {"second_opinion_disagreed": 1}
    yes = await verify(root, tmp_path, cfg=cfg, second=llm('{"supports": true}'))
    assert yes.verified == 1 and yes.llm_calls == 1


async def test_a_primary_source_needs_no_second_opinion(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101", url=f"https://{PRIMARY_HOST}/f")])
    cfg = settings(supply_chain_second_opinion=True)
    summary = await verify(root, tmp_path, cfg=cfg, second=llm())
    assert summary.verified == 1 and summary.llm_calls == 0


async def test_new_theme_needs_six_verified_members(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    symbols = ["9101", "9102", "9103", "9104", "9105", "9106"]
    seed_claims(root, [candidate_claim(s) for s in symbols[:5]])
    five = await verify(root, tmp_path)
    assert (five.verified, five.new_themes, five.pending_themes) == (5, 0, 1)
    assert not proposal_paths(root, TODAY).new_themes.exists()
    seed_claims(root, [candidate_claim("9106")])
    six = await verify(root, tmp_path)
    assert six.new_themes == 1 and six.member_rows == 6
    [theme] = yaml.safe_load(proposal_paths(root, TODAY).new_themes.read_text(encoding="utf-8"))
    assert theme["key"] == "quantum-widget" and theme["name_zh"] == CANDIDATE_LABEL
    assert theme["name_en"] == CANDIDATE_LABEL and theme["thin"] is False
    assert theme["related"] == [] and theme["description"]
    assert {m["symbol"] for m in theme["members"]} == set(symbols)
    assert {m["confidence"] for m in theme["members"]} == {"high"}


async def test_position_follows_the_majority_of_claims(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(
        root,
        [
            claim("9101", position="upstream", url=f"https://{SECONDARY_HOST}/1"),
            claim("9101", position="upstream", url=f"https://{SECONDARY_HOST}/2"),
            claim("9101", position="downstream", url=f"https://{SECONDARY_HOST}/3"),
        ],
    )
    await verify(root, tmp_path)
    assert [r["position"] for r in members_rows(root)] == ["upstream"]


async def test_proposal_files_are_accepted_by_promote_dry_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    existing = [claim("9101"), claim("9102", url=f"https://{PRIMARY_HOST}/f")]
    symbols = ["9103", "9104", "9105", "9106", "9107", "9108"]
    seed_claims(root, [*existing, *[candidate_claim(s) for s in symbols]])
    summary = await verify(root, tmp_path)
    assert (summary.new_themes, summary.member_rows) == (1, 8)
    paths = proposal_paths(root, TODAY)
    report = run_promote(
        root,
        [paths.new_themes, paths.members],
        dry_run=True,
        industry=IndustryRoster(),
    )
    assert report.new_themes == ["quantum-widget"]
    assert report.added["quantum-widget"] == 6 and report.added[THEME_KEY] == 2
    assert dict(report.rejects) == {}
    assert not report.written
    live = yaml.safe_load(
        (root / "themes" / "concepts" / f"{THEME_KEY}.yaml").read_text(encoding="utf-8")
    )
    assert [m["symbol"] for m in live["members"]] == ["9100"]
    assert not (root / "themes" / "concepts" / "quantum-widget.yaml").exists()


async def test_rerun_is_idempotent_and_leaves_no_temp_files(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(
        root,
        [claim("9101"), claim("9102", url=TERTIARY_A), claim("9103", published_on="2019-01-01")],
    )
    first = await verify(root, tmp_path)
    paths = proposal_paths(root, TODAY)
    before = paths.members.read_text(encoding="utf-8")
    logged = rejected_path(root).read_text(encoding="utf-8")
    second = await verify(root, tmp_path)
    assert first.member_rows == 1 and second.member_rows == 0
    assert paths.members.read_text(encoding="utf-8") == before
    assert len(yaml.safe_load(before)) == 1
    assert rejected_path(root).read_text(encoding="utf-8") == logged
    assert list(paths.members.parent.glob(".*tmp*")) == []


async def test_a_later_run_merges_into_the_days_file_without_duplicates(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101")])
    await verify(root, tmp_path)
    seed_claims(root, [claim("9102")])
    summary = await verify(root, tmp_path)
    assert summary.member_rows == 1
    assert [r["symbol"] for r in members_rows(root)] == ["9101", "9102"]


async def test_a_pair_in_an_earlier_proposal_file_is_not_proposed_again(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    earlier = root / "themes" / "proposals" / "theme_members_2026-01-01.yaml"
    earlier.parent.mkdir(parents=True)
    earlier.write_text(
        yaml.safe_dump([{"theme_key": THEME_KEY, "symbol": "9101"}]), encoding="utf-8"
    )
    assert scan_proposed(root).pairs == {(THEME_KEY, "9101")}
    seed_claims(root, [claim("9101")])
    summary = await verify(root, tmp_path)
    assert summary.groups == 0 and summary.reasons == {"already_proposed": 1}
    assert members_rows(root) == []


async def test_dry_run_evaluates_but_writes_nothing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), claim("9102", published_on="2019-01-01")])
    summary = await verify(root, tmp_path, dry_run=True)
    assert summary.verified == 1 and summary.rejected == 1 and summary.dry_run
    assert "would_write=1" in summary.line()
    assert not proposal_paths(root, TODAY).members.exists()
    assert not rejected_path(root).exists()
    assert read_rejected_keys(rejected_path(root)) == set()


async def test_group_cap_stops_the_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), claim("9102"), claim("9103")])
    cfg = settings(supply_chain_research_max_claims=1)
    summary = await verify(root, tmp_path, cfg=cfg, limit=5)
    assert summary.groups == 1 and summary.stopped_by == "claims"
    limited = await verify(root, tmp_path, limit=1, dry_run=True)
    assert limited.groups == 1


async def test_second_opinion_budget_exhaustion_skips_rather_than_writes(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101")])
    cfg = settings(supply_chain_second_opinion=True, supply_chain_research_max_llm_calls=0)
    summary = await verify(root, tmp_path, cfg=cfg, second=llm())
    assert summary.skipped == 1 and summary.stopped_by == "llm_calls"
    assert members_rows(root) == []


async def test_console_output_has_ids_and_counts_only(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), candidate_claim("9102")])
    out = format_verify(await verify(root, tmp_path))
    assert f"{THEME_KEY}:9101 verified" in out and f"{CANDIDATE_ID}:9102 verified" in out
    for name in (ZETA_ZH, ZETA_EN, COMPANIES["9102"][0], THEME_ZH, THEME_EN, CANDIDATE_LABEL):
        assert name not in out
    assert "http" not in out
