"""`python -m ingest.backfill_cli` — the operator's one-shot dev backfill trigger.

A separate module from `ingest/__main__.py` on purpose: that file's `__main__` guard belongs to
the long-running leader-scheduled daemon (`python -m ingest`), a different process shape and
different operational concern than this one-shot, interactive, exit-code-bearing tool. Keeping
them apart means neither concern has to reason about the other's argument parsing or exit
semantics.

This is the *explicit manual trigger* the platform's ingest carve-out already describes as
permitted: rather than requiring the operator to export `INGEST_ON_STARTUP=true` into the whole
process environment just to run one bounded gap-fill, this CLI accepts its own `--yes` override
and passes it straight into `run_preflight_if_enabled`'s existing `ingest_on_startup` parameter —
the same gate the dev-only startup catch-up uses, just supplied per-invocation instead of via env.
The one thing this CLI will never do is let that override paper over a production config: it
hard-stops unconditionally when `Settings.aitw_env == "production"`, regardless of `--yes`, before
touching any spec, so a mis-set override can never turn into an accidental prod backfill.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import structlog

from config.settings import Settings, get_settings
from ingest.context import IngestContext
from ingest.daemon import build_ingest_context_from_settings
from ingest.jobs import ALL_WHOLE_MARKET_JOBS, WholeMarketJobSpec
from ingest.journal import JobRun, RunStatus
from ingest.preflight import run_preflight_if_enabled

logger = structlog.get_logger(__name__)

_PRODUCTION_ENV = "production"
_MAX_BACKFILL_YEARS = 10  # generous upper bound — never an unbounded operator-supplied window


class ProductionHardStopError(RuntimeError):
    """Raised when the CLI is invoked against a production config — never caught, always fatal."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingest.backfill_cli",
        description=(
            "One-shot dev backfill: gap-fill every whole-market job over the bounded "
            "dev window, through the same run_preflight_if_enabled() code path the "
            "INGEST_ON_STARTUP catch-up uses."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Explicit manual-trigger override: run even if INGEST_ON_STARTUP is not set in "
            "the environment. Still hard-refuses if AITW_ENV=production."
        ),
    )
    parser.add_argument(
        "--years",
        type=int,
        default=None,
        help="Override dev_backfill_years (small positive int; default from Settings).",
    )
    return parser.parse_args(argv)


def _validate_years(years: int | None, settings: Settings) -> int:
    resolved = settings.dev_backfill_years if years is None else years
    if resolved <= 0 or resolved > _MAX_BACKFILL_YEARS:
        raise ValueError(
            f"dev_backfill_years must be in [1, {_MAX_BACKFILL_YEARS}], got {resolved}"
        )
    return resolved


def _check_not_production(settings: Settings) -> None:
    if settings.aitw_env == _PRODUCTION_ENV:
        raise ProductionHardStopError(
            "refusing to run: AITW_ENV=production — this CLI never runs against a production "
            "config, regardless of --yes or INGEST_ON_STARTUP."
        )


async def run_backfill_cli(
    settings: Settings,
    ctx: IngestContext,
    *,
    override: bool,
    years: int,
    specs: Sequence[WholeMarketJobSpec] = ALL_WHOLE_MARKET_JOBS,
) -> tuple[JobRun, ...]:
    """Run `run_preflight_if_enabled` once per spec, printing a per-dataset summary as each
    `JobRun` batch lands. One failing spec is logged and does not stop the remaining specs —
    mirroring `backfill_range`'s own per-date resumability philosophy."""
    _check_not_production(settings)
    ingest_on_startup = override or settings.ingest_on_startup
    if not ingest_on_startup:
        logger.warning(
            "ingest_backfill_cli_refused",
            reason="INGEST_ON_STARTUP is false and --yes was not passed",
        )
        return ()

    all_runs: list[JobRun] = []
    for spec in specs:
        try:
            runs = await run_preflight_if_enabled(
                spec,
                ctx,
                ingest_on_startup=True,
                dev_backfill_years=years,
            )
        except Exception:
            logger.exception("ingest_backfill_cli_spec_errored", dataset=spec.dataset)
            continue
        for run in runs:
            logger.info(
                "ingest_backfill_cli_run",
                dataset=run.dataset,
                run_date=run.run_date.isoformat(),
                rows_written=run.rows_written,
                status=run.status.value,
            )
        all_runs.extend(runs)
    return tuple(all_runs)


async def _amain(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    try:
        _check_not_production(settings)
        years = _validate_years(args.years, settings)
    except (ProductionHardStopError, ValueError) as exc:
        logger.error("ingest_backfill_cli_aborted", detail=str(exc))
        return 1

    ctx = build_ingest_context_from_settings(settings)
    runs = await run_backfill_cli(settings, ctx, override=args.yes, years=years)
    if not runs:
        logger.info("ingest_backfill_cli_no_runs")
        return 0
    failed = tuple(r for r in runs if r.status is RunStatus.FAILED)
    logger.info(
        "ingest_backfill_cli_summary",
        total_runs=len(runs),
        failed_runs=len(failed),
    )
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["ProductionHardStopError", "main", "parse_args", "run_backfill_cli"]
