---
name: verify
description: Launch and drive the real Miru backend (FastAPI, Taiwan stock agent chat and analytics) against an isolated scratch database with synthetic market data, then prove a change by calling its REST routes the way the web app does. Use after changing scoring, analytics, API routes, tools, ingest or governance code, or whenever asked to run the app or confirm a change works beyond unit tests.
---

# Verify Miru

Surface: the FastAPI backend at `/api/v1` (what the Next.js app and MCP clients call). The default
proof is **seed a tiny synthetic TW market, run the real scoring batch, start the real API, call
the real routes**. No Docker, no Redis, no network, no API keys. The web app and MCP server are
secondary surfaces (see Drive).

Hard rules. `backend/.env` holds live secrets: never read, print or load it. Everything here runs
from a scratch directory so pydantic-settings cannot find it. Data is synthetic (symbols 9001,
9002, 9003): never present it as real market data. Scores stay descriptive: a proof that shows
buy/sell/target language is a governance failure, not a pass.

## Launch

```bash
bash .claude/skills/verify/scripts/up.sh                  # score as shipped
bash .claude/skills/verify/scripts/up.sh --revenue-yoy    # also seed monthly revenue, set SCORE_REVENUE_YOY_ENABLED=true
```

To verify a git worktree's code instead of this checkout, export
`VERIFY_BACKEND_REPO=<worktree root>` (it must have its own synced `backend/.venv`); the scripts still
come from this skill.

`--revenue-yoy` seeds Nov and Dec 2023 revenue with **opposite** YoY orderings. On the 2024-01-02 run
only November (published Dec 10) is knowable, so with the publication gate 9001 keeps
`revenue_flow` 100.0 (9002 66.7, 9003 33.3) and the description reads 反映三大法人 20日淨買超與月營收 YoY.
A look-ahead leak of December would drop 9001 to 66.7. Without the flag the description is the
chip-only disclosure, unchanged. Proven against the revenue-YoY worktree on 2026-10-31.

Needs `uv` and a synced backend venv (`cd backend && uv sync`, about 15 s warm). It creates
`$TMPDIR/miru-verify.XXXXXX` (scratch), seeds 260 trading days of prices and 20 days of 三大法人 flow for 9001
(+30% 12-1 momentum), 9002 (+10%), 9003 (-10%), runs `job_scoring_batch` for 2024-01-02, starts
uvicorn on **127.0.0.1:8765** (override with `VERIFY_PORT`) and waits for `/readyz`. On success it
prints `SCRATCH=`, `PORT=`, `PID=`. It refuses to start if the port already answers: never
double-drive someone else's instance. `PYTHONPATH` is set to `backend/src` by the script because
the repo's own `calendar` package must precede the standard library's; a hand-run needs the same.

Ready means `/readyz` returns `{"status":"ready","checks":{"database":true}}` and the seed log says
`scoring_batch status=success rows_written=6`. Logs: `$SCRATCH/api.log`, `$SCRATCH/seed.log`.

## Doctor

```bash
bash .claude/skills/verify/scripts/doctor.sh "$SCRATCH"
```

Read-only. Checks the recorded PID is alive, `/readyz` is ready, the instance uses the scratch DB,
and a leaderboard read returns rows. Run it first whenever a call looks wrong.

## Drive

Base `B=http://127.0.0.1:8765/api/v1`. Reads are public, so no token is needed for analytics.

| Path | Call | Observable end state |
|------|------|----------------------|
| Stock score | `GET $B/analytics/9001/score?market=TW&profile=swing` | `found:true`, `composite` 100.0, `band` 偏多, five-factor `breakdown`, `partial_factors` lists value_guard and quality with an `unavailable_reason` (never a fake 50), `non_trading_caveat` present |
| Leaderboard | `GET $B/analytics/rankings?market=TW&profile=swing&direction=bullish` | order 9001 (100.0), 9002 (about 69.27), 9003 (about 26.82, 偏空); `as_of` 2024-01-02 |
| Both profiles | repeat with `profile=position` | different composite weights, same ordering |
| Authenticated | `TOKEN=$(cd "$SCRATCH" && <venv python> .claude/skills/verify/scripts/mint_token.py free)`, then `curl -H "Authorization: Bearer $TOKEN" $B/me` | 200 with token, 401 without. Run `mint_token.py` with the env from `$SCRATCH/env.txt` exported (same `DATABASE_URL`, `JWT_SECRET_KEY`) |

There is no dev login route (sign-in is Google/LINE/Telegram OAuth), so a script authenticates only
by minting a JWT as above. The same token goes in the `access_token` cookie on host `localhost`
for the browser.

**Chat turn** (`POST $B/agent/query`, body `{"message": "...", "locale": "zh-TW"}`, SSE with
`turn.start`, `tool.start`, `tool.result`, `rich_card`, `message.delta`, `turn.end`) needs a real
LLM provider key in the environment of the server. The scratch run has none by design. To prove a
chat change, export one provider key into `up.sh`'s environment yourself, from your own shell, and
expect the turn to call `get_stock_score` for 9001. Not exercised by this skill's own proof.

**Web** (`cd web && pnpm install && pnpm dev`, :3000; `NEXT_PUBLIC_API_BASE=http://localhost:8765/api/v1`):
set the minted JWT as cookie `access_token` for `localhost`, open `/zh-TW/chat`. Stable handles, since
there are no `data-testid`s: composer `textarea#miru-chat-composer`, send `button[type=submit]`
with `aria-label` 送出 (en: Send), final answer `div[aria-live="polite"]`, cards
`figure[aria-label=...]`, stock page `/zh-TW/stock/9001` score/profile toggle `role=tablist`.
Not exercised by this skill's own proof (web dependencies are not installed by default).

**MCP** (`PYTHONPATH=backend/src` from a scratch cwd, `MCP_REQUIRE_AUTH=false`, `python -m mcp`,
:8100, `GET /healthz`, JSON-RPC at `/mcp`): `tools/call` of `execute_tool` with
`{"id":"get_stock_score","args":{"id":"9001","market":"TW"}}`. Not exercised by this skill's own proof.

## Evidence

Write proofs to `${TMPDIR:-/tmp}/miru-verify-evidence/<yyyymmdd-hhmmss>-<feature>/`, **outside**
the scratch dir so teardown cannot delete them. Save the raw response bodies (`score-9001.json`,
`rankings.json`, `me.json`) plus the launch line `scoring_batch status=...`. Standards:

- Exercise the real route, not a unit call or a test-only endpoint.
- Capture the request and the resulting state; for a scoring change, show the factor `description`
  and `partial_factors`, not just `composite`.
- Check the order and the honesty rules: unavailable factors say why, no buy/sell/hold/target words
  (`pytest tests/analytics/test_scoring_batch_e2e.py` is the offline unit-level twin).
- Compare against the baseline above when the change should alter output (for example the
  `revenue_flow` description changes only with `--revenue-yoy`).

## Cleanup

```bash
bash .claude/skills/verify/scripts/down.sh "$SCRATCH"
```

Kills the recorded Windows PID **and its child** (`taskkill //T`), then deletes the scratch dir. It
refuses any directory not named `miru-verify.*`. Never kill by process name. After a failed run,
run it too; then confirm `curl -s -m 2 http://127.0.0.1:8765/livez` gets no answer. Evidence under
`miru-verify-evidence/` is kept.

## Isolation

One instance per port. Different `VERIFY_PORT` plus a separate scratch dir lets two run side by
side. The Docker stack (`scripts/dev-backend.sh`, api :8000, mcp :8100, Postgres, Redis) is a
different environment: use it only when a change needs Postgres, Redis, the scheduler or the ingest
daemon, and never point this skill's scripts at it.

## Feature map

`features/README.md` lists the features and how to drive each. Keep it current with
`/maintain-verification-skill`.
