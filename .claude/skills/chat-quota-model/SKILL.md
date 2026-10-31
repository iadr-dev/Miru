---
name: chat-quota-model
description: TLA+ model of Miru's daily chat allowance (reserve a unit, run the pre-start steps, start the turn, refund, idempotent retry), checked with TLC. Use when a change touches usage/store.py reserve or refund, enforce_pre_turn_gate, the agent/query, regenerate or edit routes before start_turn_stream, the scheduler quota gate, or the turns idempotency key, and before claiming such a change keeps the cap or stops leaking units.
---

# Daily chat quota model

**Promise.** A user never gets more chats than their tier cap, and never loses an allowance unit for a turn that did not run.

**Harm when broken.** A leaked unit locks a Free user (cap 3, `accounts/entitlements.py` `TIER_LIMITS`) out of chat early. A cap overrun lets turns run that the tier does not pay for.

The model is `model/ChatQuota.tla`. Each `.cfg` in `model/` is one scenario, and `model/expected.tsv` holds the result each one must give. Some are expected to fail. Those failures are the findings below, and they stay expected failures until product code changes.

## Run it

```bash
bash .claude/skills/chat-quota-model/scripts/run.sh            # all scenarios, 30 to 45 s
bash .claude/skills/chat-quota-model/scripts/run.sh fixed asis_dup_race   # a subset
```

It needs Java 11 or later on PATH. The first run downloads TLC (`tla2tools.jar` v1.7.4) into `~/.cache/miru-chat-quota-model/tla2tools-v1.7.4/` and every run checks its SHA-256 before use. Override the cache root with `MIRU_TLA_CACHE`. Java runs from a temp dir, never from the repo. The script prints one line per scenario (expected, got, distinct states) and the temp dir holding each full TLC output as `<config>.out`. Exit 0 means every scenario matched `expected.tsv`.

Read every TLC result, pass or fail, with the pstack `/statechart` skill. It turns a violation into its shortest path in plain words and draws the state chart when a check passes.

## Does my change touch this?

```bash
bash .claude/skills/chat-quota-model/scripts/drift.sh          # from inside the checkout you changed
```

It lists the covered files (the coverage table below) that differ from the sync commit, committed or not. Exit 1 means look again: open each listed function, compare it with the action that cites it, and rerun `run.sh`.

## What the model contains

One user. The counter row is keyed by `(user_id, counter, window_key)` and no step reads another user's row, so users are independent. Requests are a small set (2 or 3 per scenario, listed in `model/MC.tla`), all concurrent, interleaved in every order. Each request follows one route.

| Model action | Code (line numbers at the sync commit) | Indivisible because |
|---|---|---|
| `Arrive` | `api/routers/agent.py:122-124` `_reject_duplicate`: a keyed send whose key has a committed turn gets 409 `duplicate_request` before the gate | one SELECT |
| `ReserveUpsert` | `usage/store.py:213` window key, `_increment` `store.py:134-163` `INSERT .. ON CONFLICT DO UPDATE n = n + 1 RETURNING n` | one statement; with `RowLock` the row stays locked until the commit |
| `ReserveDecide` | `store.py:218-231`: over the cap, `UPDATE n = n - 1` then commit and `ChatQuotaVerdict(allowed=False)`, turned into 429 `chat_limit_reached` at `api/turn_driver.py:122`; else commit and grant | one commit |
| `StepOk` / `StepFail` | the steps between the grant and the start, per route, listed in `Steps` in the model with their lines | each is one call that either returns or raises |
| `StepOk` on `append_turn` | `conversation/store.py:282-336`, insert flushed at `:313` against `uq_turns_user_idempotency_key` (`db/models/conversation.py:85`). A committed same-key row raises `IntegrityError` (`agent.py:175`); an uncommitted one makes the insert wait | one INSERT |
| `Refund` | `refund_daily_chat` `store.py:176-183` into `_refund` `store.py:196-208`: own session, `UPDATE n = n - 1 WHERE window_key = <today at refund time> AND n > 0`, commit | one statement plus commit |
| `Crash` | the worker process dies between two steps; its open transaction rolls back | |
| `Tick` | UTC midnight: `_day_key(datetime.now(UTC).date())` names the next row | |

Routes and the step that starts the turn:

- `query`: `POST /agent/query`, `agent_query` `agent.py:108-214`. Gate at `:136`. Steps `commit_gate_tx` (`:141`), `process_attachments` (`:142`, 422 `too_many_attachments` or `attachment_extraction_failed` in `api/attachments.py`), `resolve_thread` (`:146`, 404 `thread_not_found` at `:239`), `patch_thread` (`:147-158`, `update_thread`, 422 `unknown_personality` from `api/personality_choice.py:21`), `append_turn` (`:166`), `commit_turn` (`:190`). The turn starts at `start_turn_stream` `:198`.
- `edit`: `edit_turn` `api/routers/threads_actions.py:690`. Gate `:734`, `append_turn` `:736`, `commit_turn` `:750`, start `:753`.
- `regenerate`: `regenerate_turn` `threads_actions.py:599`. Gate `:653`, `commit_gate_tx` `:660`, start `:661`.
- `scheduled`: `SchedulerWorker.run_job` `scheduler/worker.py`. `UsageQuotaGate.allows` (`scheduler/quota.py:61-68`) reserves at `worker.py:294`. Steps `daily_limit_check` (`:313`, returns without touching the job), `resolve_inputs` (`:324-337`, `watchlist_empty`), `claim_job` (`:343`, another worker took the job), `commit_claim` (`:347`). The turn starts at `run_skill` `:357`.

Switches, each mirroring one code fact. As-is values are the code today:

| Constant | As-is | Meaning |
|---|---|---|
| `RefundMode` | `"as_is"` | refund only where the code refunds (`agent.py:182`). `"all"` refunds every pre-start exit |
| `RefundDay` | `"now"` | `agent.py:182` passes no `today`, so `_refund` uses the day at refund time. `"claim"` refunds the claimed day |
| `RowLock` | `TRUE` | `_reserve` runs the upsert and the decrement in one transaction, so the row lock (Postgres) or the write lock (SQLite) is held until its commit |
| `UniqueKey` | `TRUE` | `uq_turns_user_idempotency_key` exists |
| `DupRefund` | `TRUE` | the `IntegrityError` path refunds |
| `RefundStarted` | `FALSE` | a started turn keeps its unit |
| `FailAt`, `CrashIn` | per scenario | which steps may fail, and whether the process may die inside `_reserve` (`"reserve"`) or after the grant (`"post_claim"`) |

## Properties

- `CapSafety`. On every UTC day, the turns that started on that day's allowance never outnumber the cap.
- `NoLeak`. Today's counter never exceeds the turns that started today plus the claims still in flight. So a request that ends without starting a turn has given its unit back.
- `RejectOnlyWhenFull`. `chat_limit_reached` is returned only when the day is truly full. This is what a leak looks like to the user.
- `OneTurnPerKey`. One idempotency key never produces two committed turns.

## Scenarios and what they showed at the sync commit

Each property was seen failing on a variant broken on purpose, so a pass means something:

| Scenario | Result | Shows |
|---|---|---|
| `broken_no_dup_refund` | `NoLeak` fails | removing the `agent.py:182` refund leaks the loser's unit in a same-key race |
| `broken_refund_started` | `CapSafety` fails | refunding a turn that started lets a second turn run on a cap of 1 |
| `broken_no_rowlock` | `RejectOnlyWhenFull` fails | if each `_reserve` statement committed alone, an over-cap caller's brief +1 rejects a caller while a unit is free |
| `broken_no_unique` | `OneTurnPerKey` fails | without the unique index both same-key sends start a turn |
| `fixed`, `fixed_other_routes` | pass | refunding every pre-start exit on the claimed day keeps all four properties, all routes, with a midnight rollover |
| `asis_dup_race` | pass | the code's same-key race handling is right when nothing else fails |
| `asis_reserve_crash` | pass | a crash inside `_reserve` leaks nothing |

Findings on the code as-is. Each is a counterexample TLC printed, retold in the code's words:

- **F1, chat send errors after the gate keep the unit** (`asis_query_exits`, `NoLeak`). A Free user sends a message with an attachment that fails extraction. `enforce_pre_turn_gate` reserves and commits a unit (`turn_driver.py:121`, `store.py:230`). `process_attachments` (`agent.py:142`) raises 422. No code gives the unit back. The same holds for 404 `thread_not_found` (`agent.py:146`), 422 `unknown_personality` (`agent.py:151`), and any `append_turn` error other than `IntegrityError`. Effect: three such errors lock a Free user out for the rest of the UTC day with no answer received.
- **F2, edit and regenerate keep the unit on a pre-start error** (`asis_edit_regen_exits`, `NoLeak`). The gate grants (`threads_actions.py:734`), then `store.append_turn` (`:736`) or the commit (`:750`, or `:660` for regenerate) raises. No refund exists on either route.
- **F3, a scheduled job can drain the whole allowance without running** (`asis_scheduler_exits` for `NoLeak`, `asis_scheduler_drain` for the lockout). `UsageQuotaGate.allows` reserves a unit (`worker.py:294`). The job has reached its own `daily_limit`, so `run_job` returns at `:313-322` without refunding and without moving `next_run`. The job is due again on the next poll (`scheduler_poll_interval_seconds`, 60 s by default) and reserves again. TLC's trace: two polls take both units of a cap of 2, then the user's own chat send gets `chat_limit_reached` with zero turns run. The `watchlist_empty` (`:324-337`) and lost `claim_job` (`:343`) exits keep a unit too, once each. The repeat-every-poll part comes from reading `due_jobs` (`scheduler/store.py:607-625`) and the early return; the model encodes it as `Repoll`.
- **F4, a process crash after the grant keeps the unit** (`asis_crash`, `NoLeak`). The gate's commit is durable before any later step, so a worker killed between `store.py:230` and the turn commit leaks the unit. This includes a crash between the `IntegrityError` rollback (`agent.py:179`) and the refund (`:182`). `fixed_crash` shows a refund-on-error fix cannot close this; it needs the claim and the turn to commit together, or a sweeper.
- **F5, a refund across UTC midnight lands on the next day** (`asis_rollover`, `CapSafety`). Two copies of one keyed message: copy 1 is granted on day 1. Midnight passes. Copy 2 is granted on day 2 and its turn commits. Copy 1 hits `IntegrityError` and `refund_daily_chat` (`agent.py:182`, no `today`) decrements day 2's row (`store.py:203`). A third send is then granted on day 2, so two turns run on a day-2 cap of 1. Day 1's unit also stays spent; with every property on, TLC reports that shorter side effect as `RejectOnlyWhenFull`. Rare (needs a double-submit straddling 08:00 Taiwan time), one unit per occurrence.

Suspicions the model refuted, given `RowLock`: a crash between the upsert and the over-cap decrement in `_reserve` rolls the whole transaction back (`asis_reserve_crash` passes); concurrent over-cap callers cannot push `n` above the cap where others see it, because the second upsert waits for the first commit; the unguarded decrement at `store.py:219-227` only undoes the same transaction's own +1.

**Open policy question.** Should a turn that started and then failed give its unit back? The code says no on every route: `refund_daily_chat`'s docstring limits refunds to "a turn that was not started" (`store.py:179-180`), the scheduler records a failed `run_skill` without a refund (`worker.py:369-379`), and `docs/backend/modules/24-deep-research.md` §3 says a failed report run is neither claimed back nor refunded. For a normal chat answer that errors mid-stream, no doc states the policy. The model follows the code (`RefundStarted = FALSE`). If product decides otherwise, `CapSafety` must count only turns that succeeded, and `broken_refund_started` stops being a broken variant.

## Real bug or modeling error?

When a result differs from `expected.tsv`, read the trace with `/statechart`, then replay it step by step against the cited lines. It is a modeling error when a step in the trace cannot happen in the code: a step the code makes indivisible split in two, a failure injected where the call cannot raise, or a lock the database does not take. It is a real bug when every step maps to code that can do it. Never weaken a property, or drop a failure point the code has, to turn a result green.

## Updating the model for an intended change

1. Run `drift.sh` and open every listed function.
2. Change the action that cites the changed code, keep its comment pointing at the new lines, and update the tables above.
3. If the change fixes a finding, flip that scenario's line in `expected.tsv` to `pass` and move the finding to the passing table. If it adds a pre-start step, add it to `Steps` and to `FailAt` in the scenarios for that route.
4. Run `run.sh` until every line matches, and read the results with `/statechart`.
5. Only after confirming the model describes the code at the new commit, set the sync commit below to that commit's `git rev-parse HEAD`.

## Scope

Covered: the four routes that reserve a daily chat, up to 3 concurrent requests, caps of 1 and 2, at most one UTC midnight, failures at every pre-start step listed above, a crash at any point, same-key retries.

Not covered: the per-minute rate limiter (Redis `INCR`, `turn_driver.py:101`) and the voice counters (`reserve_daily_voice`, `api/routers/voice.py`), which share `_reserve` but not this counter. Postgres isolation levels other than READ COMMITTED; under SERIALIZABLE a conflict aborts the reserve, which the model's crash covers. SQLite busy timeouts beyond "the second writer waits or fails before its commit". Anything after the turn starts: the LLM, the stream, a client that disconnects before `run_and_stream` first runs, cancellation. Unlimited callers (Pro, BYOK), who never touch the counter. More than 3 requests, more than one rollover, or more than one user. A pass covers this model under these bounds, not the deployed system.

## Coverage record

Sync commit: `b3c8afe3087418dd5d6faec47e85a3d227c51916`

Move the sync commit forward only after confirming that the model still describes every file and function below at the new commit. `drift.sh` reads this line and this table.

| File | Functions |
|---|---|
| `backend/src/usage/store.py` | `SqlAlchemyUsageStore._increment`, `reserve_daily_chat`, `refund_daily_chat`, `_refund`, `_reserve` |
| `backend/src/api/turn_driver.py` | `enforce_pre_turn_gate`, `start_turn_stream` |
| `backend/src/api/routers/agent.py` | `agent_query`, `_reject_duplicate`, `_resolve_thread` |
| `backend/src/api/routers/threads_actions.py` | `regenerate_turn`, `edit_turn` |
| `backend/src/scheduler/quota.py` | `UsageQuotaGate.allows` |
| `backend/src/scheduler/worker.py` | `SchedulerWorker.run_job` |
| `backend/src/scheduler/store.py` | `due_jobs`, `claim_job` |
| `backend/src/conversation/store.py` | `SqlAlchemyConversationStore.append_turn`, `turn_for_idempotency_key` |
| `backend/src/db/models/conversation.py` | `TurnRow.__table_args__` (`uq_turns_user_idempotency_key`) |
| `backend/src/db/models/billing.py` | `UsageCounterRow` |
| `backend/src/api/attachments.py` | `process_attachments` |
| `backend/src/api/personality_choice.py` | `personality_patch` |
| `backend/src/accounts/entitlements.py` | `TIER_LIMITS`, `chat_limit_for` |

## Tool pin

TLC from `tla2tools.jar` v1.7.4 ("The Xenophanes release", TLC2 Version 2.19 of 08 August 2024), downloaded from `https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar`. SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`. The release page publishes SHA-1 `bee4a54f3ee3d4afc347c3240ec2d9e93b075104`, which this file matched when pinned. `run.sh` holds the same version, URL and SHA-256.
