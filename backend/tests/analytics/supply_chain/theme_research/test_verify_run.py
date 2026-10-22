"""Characterization of `execute_verify`'s group loop: which groups are skipped before they cost
anything, the counters and outcome map, the caps, and what a real versus a dry pass persists."""

from __future__ import annotations

from pathlib import Path

import yaml

from analytics.supply_chain.theme_research.files import (
    covered_path,
    read_rejected_keys,
    rejected_path,
)
from analytics.supply_chain.theme_research.proposals import proposal_paths
from tests.analytics.supply_chain.theme_research.support import (
    THEME_KEY,
    TODAY,
    candidate_claim,
    claim,
    make_root,
    seed_claims,
    settings,
)
from tests.analytics.supply_chain.theme_research.test_verify import verify
from tests.analytics.supply_chain.verification.support import llm


async def test_a_previously_rejected_claim_is_counted_and_never_judged_again(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101", published_on="2019-01-01"), claim("9102")])
    first = await verify(root, tmp_path)
    assert (first.verified, first.rejected) == (1, 1)
    assert len(read_rejected_keys(rejected_path(root))) == 1
    again = await verify(root, tmp_path)
    assert again.groups == 0
    assert again.reasons == {"already_rejected": 1, "already_proposed": 1}
    assert again.rejected == 0


async def test_outcome_map_counts_and_reasons_for_a_mixed_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(
        root,
        [
            claim("9101"),
            claim("9102", published_on="2019-01-01"),
            claim("9103", url="https://a.example.org/p"),
            claim("9100"),
        ],
    )
    summary = await verify(root, tmp_path)
    assert summary.groups == 4
    assert (summary.verified, summary.candidates, summary.rejected, summary.skipped) == (1, 1, 2, 0)
    assert summary.outcomes == {
        f"{THEME_KEY}:9101": "verified",
        f"{THEME_KEY}:9102": "rejected",
        f"{THEME_KEY}:9103": "candidate",
        f"{THEME_KEY}:9100": "rejected",
    }
    assert summary.reasons["already_member"] == 1
    assert summary.stopped_by is None
    assert not summary.dry_run
    assert summary.llm_calls == 0
    assert summary.spend_usd == 0.0


async def test_skipped_groups_do_not_use_the_claim_budget(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    earlier = root / "themes" / "proposals" / "theme_members_2026-01-01.yaml"
    earlier.parent.mkdir(parents=True)
    earlier.write_text(
        yaml.safe_dump([{"theme_key": THEME_KEY, "symbol": s} for s in ("9101", "9102")]),
        encoding="utf-8",
    )
    seed_claims(root, [claim("9101"), claim("9102"), claim("9103")])
    cfg = settings(supply_chain_research_max_claims=1)
    summary = await verify(root, tmp_path, cfg=cfg, limit=5)
    assert summary.groups == 1
    assert summary.reasons == {"already_proposed": 2}
    assert summary.stopped_by is None
    assert summary.verified == 1


async def test_the_default_group_cap_is_the_claims_setting_and_the_limit_overrides_it(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), claim("9102"), claim("9103")])
    cfg = settings(supply_chain_research_max_claims=2)
    assert (await verify(root, tmp_path, cfg=cfg, dry_run=True)).groups == 2
    assert (await verify(root, tmp_path, cfg=cfg, limit=3, dry_run=True)).groups == 2
    assert (await verify(root, tmp_path, cfg=cfg, limit=1, dry_run=True)).groups == 1
    assert (await verify(root, tmp_path, cfg=cfg, limit=0, dry_run=True)).groups == 0


async def test_a_dry_pass_persists_no_covered_rejected_or_proposal_files(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), claim("9102", published_on="2019-01-01")])
    summary = await verify(root, tmp_path, dry_run=True)
    assert summary.dry_run
    assert not covered_path(root).exists()
    assert not rejected_path(root).exists()
    assert not proposal_paths(root, TODAY).members.exists()


async def test_second_opinion_uses_the_injected_client_and_counts_its_calls(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    seed_claims(root, [claim("9101"), candidate_claim("9102")])
    cfg = settings(supply_chain_second_opinion=True)
    second = llm('{"supports": true}', '{"supports": true}')
    summary = await verify(root, tmp_path, cfg=cfg, second=second)
    assert summary.llm_calls == 2
    assert summary.verified == 2
    assert summary.spend_usd > 0
    assert len(second.calls) == 2
