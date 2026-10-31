---
name: chat-quota-model
description: TLA+ model of Miru's daily chat allowance (reserve a unit, run the pre-start steps, start the turn, refund, idempotent retry), checked with TLC. Use when a change touches usage/store.py reserve or refund, enforce_pre_turn_gate, the agent/query, regenerate or edit routes before start_turn_stream, the scheduler quota gate, or the turns idempotency key, and before claiming such a change keeps the cap or stops leaking units.
---

# Daily chat quota model

**Promise.** A user never gets more chats than their tier cap, and never loses an allowance unit for a turn that did not run.

**Harm when broken.** A leaked unit locks a Free user (cap 3, `accounts/entitlements.py` `TIER_LIMITS`) out of chat early. A cap overrun lets turns run that the tier does not pay for.

The model is `model/ChatQuota.tla`. Each `.cfg` in `model/` is one scenario, and `model/expected.tsv` holds the result each one must give. Some are expected to fail. An `asis_*` failure is an open finding below and stays an expected failure until product code changes; a `broken_*` failure is a variant broken on purpose, which shows the property can fail.

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
| `Arrive` | `api/routers/agent.py:121-123` `_reject_duplicate`: a keyed send whose key has a committed turn gets 409 `duplicate_request` before the gate | one SELECT |
| `ReserveUpsert` | `claim_daily_chat` `usage/store.py:175-186` fixes the day (`:182`); `_reserve` `store.py:276` window key, `_increment` `store.py:197-226` `INSERT .. ON CONFLICT DO UPDATE n = n + 1 RETURNING n` | one statement; with `RowLock` the row stays locked until the commit |
| `ReserveDecide` | `store.py:281-294`: over the cap, `UPDATE n = n - 1` then commit and `ChatQuotaVerdict(allowed=False)`; `claim_daily_chat` returns None, turned into 429 `chat_limit_reached` at `api/turn_driver.py:127`; else commit and grant a `ChatClaim` for that day | one commit |
| `StepOk` / `StepFail` | the steps between the grant and the start, per route, listed in `Steps` in the model with their lines. Every route runs them inside `ChatClaim.refund_unless_started` (`store.py:153-160`) and calls `turn_started()` (`:150`) once the last one succeeds; leaving the block before that refunds | each is one call that either returns or raises |
| `StepOk` on `append_turn` | `conversation/store.py:282-336`, insert flushed at `:313` against `uq_turns_user_idempotency_key` (`db/models/conversation.py:85`). A committed same-key row raises `IntegrityError` (`agent.py:176`); an uncommitted one makes the insert wait | one INSERT |
| `Refund` | `ChatClaim._refund` `store.py:162-173`, at most once per claim: rolls the caller's session back, then `refund_daily_chat` `store.py:239-246` into `_refund` `store.py:259-271`: own session, `UPDATE n = n - 1 WHERE window_key = <the claimed day> AND n > 0`, commit | one statement plus commit |
| `Crash` | the worker process dies between two steps; its open transaction rolls back | |
| `Tick` | UTC midnight: `_day_key(datetime.now(UTC).date())` names the next row | |

Routes and the step that starts the turn:

- `query`: `POST /agent/query`, `agent_query` `agent.py:107-214`. Gate at `:135`, claim block from `:138`. Steps `commit_gate_tx` (`:142`), `process_attachments` (`:143`, 422 `too_many_attachments`, `invalid_request` or `attachment_extraction_failed` in `api/attachments.py`), `resolve_thread` (`:147`, 404 `thread_not_found` at `:239`), `patch_thread` (`:148-159`, `update_thread`, 422 `unknown_personality` from `api/personality_choice.py:21`), `append_turn` (`:167`), `commit_turn` (`:188`). The turn starts at `start_turn_stream` `:196`; `turn_started()` at `:213`.
- `edit`: `edit_turn` `api/routers/threads_actions.py:694`. Gate `:738`, claim block from `:741`, `append_turn` `:742`, `commit_turn` `:756`, start `:759`, `turn_started()` `:773`.
- `regenerate`: `regenerate_turn` `threads_actions.py:599`. Gate `:653`, claim block from `:656`, `commit_gate_tx` `:662`, start `:663`, `turn_started()` `:678`.
- `scheduled`: `SchedulerWorker.run_job` `scheduler/worker.py`. `UsageQuotaGate.claim` (`scheduler/quota.py:61-67`) reserves at `worker.py:294-298`, claim block from `:316`. Steps `daily_limit_check` (`:317-326`, returns without touching the job), `resolve_inputs` (`:328-342`, `watchlist_empty`), `claim_job` (`:348`, another worker took the job), `commit_claim` (`:352`). `turn_started()` at `:353`; the turn starts at `run_skill` `:363`.

Switches, each mirroring one code fact. As-is values are the code today:

| Constant | As-is | Meaning |
|---|---|---|
| `RefundMode` | `"pre_start"` | every exit before the turn starts refunds (`ChatClaim`). `"dup_only"` is the code before `ChatClaim`, which refunded only the `IntegrityError` path |
| `RefundDay` | `"claim"` | the claim refunds the day it was reserved on (`store.py:169`). `"now"`, the day at refund time, is the code before `ChatClaim` |
| `RowLock` | `TRUE` | `_reserve` runs the upsert and the decrement in one transaction, so the row lock (Postgres) or the write lock (SQLite) is held until its commit |
| `UniqueKey` | `TRUE` | `uq_turns_user_idempotency_key` exists |
| `DupRefund` | `TRUE` | the `IntegrityError` path refunds (its raise leaves the claim block, `agent.py:176-184`) |
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
| `broken_no_dup_refund` | `NoLeak` fails | if the `IntegrityError` path did not refund, the loser of a same-key race would keep its unit |
| `broken_dup_only_refund` | `NoLeak` fails | the code before `ChatClaim`: chat sends that fail after the gate keep the unit (F1) |
| `broken_scheduler_no_refund` | `RejectOnlyWhenFull` fails | the scheduler before `ChatClaim`: a job past its `daily_limit` drains the allowance (F3) |
| `broken_refund_today` | `CapSafety` fails | refunding the day the refund runs instead of the claimed day lets two turns run on a day-2 cap of 1 (F5) |
| `broken_refund_started` | `CapSafety` fails | refunding a turn that started lets a second turn run on a cap of 1 |
| `broken_no_rowlock` | `RejectOnlyWhenFull` fails | if each `_reserve` statement committed alone, an over-cap caller's brief +1 rejects a caller while a unit is free |
| `broken_no_unique` | `OneTurnPerKey` fails | without the unique index both same-key sends start a turn |
| `asis_query_exits`, `asis_edit_regen_exits`, `asis_scheduler_exits`, `asis_scheduler_drain` | pass | every pre-start exit of every route gives its unit back (F1, F2, F3 fixed) |
| `asis_rollover` | pass | a same-key race across UTC midnight refunds the day the loser claimed (F5 fixed), all four properties |
| `asis_query_rollover`, `asis_other_routes` | pass | every pre-start step of every route may fail, with a midnight rollover and a crash inside `_reserve`; all four properties hold |
| `asis_dup_race` | pass | the code's same-key race handling is right when nothing else fails |
| `asis_reserve_crash` | pass | a crash inside `_reserve` leaks nothing |

Fixed findings. Each was a counterexample TLC printed on the code before the fix; the `broken_*` variant above keeps that code's behaviour as a check that the property still catches it:

- **F1, chat send errors after the gate kept the unit.** Fixed in `967e553` (`fix(api): give the chat back when /agent/query fails before its turn starts`). A Free user sent a message with an attachment that failed extraction; `process_attachments` raised 422 after the gate had committed a unit, and no code gave it back. The same held for 404 `thread_not_found`, 422 `unknown_personality`, and any `append_turn` error other than `IntegrityError`; three such errors locked a Free user out for the UTC day. `agent_query` now runs every step after the gate inside the `ChatClaim` block (`agent.py:138-213`). Tests: `backend/tests/api/test_chat_refunds.py`.
- **F2, edit and regenerate kept the unit on a pre-start error.** Fixed in `61f79d7`. `edit_turn` (`threads_actions.py:741-773`) and `regenerate_turn` (`:656-678`) run their steps after the gate inside the `ChatClaim` block. Tests: `test_chat_refunds.py`.
- **F3, a scheduled job could drain the whole allowance without running.** Fixed in `c3ca766`. `UsageQuotaGate.allows` reserved a unit; a job at its own `daily_limit` returned without refunding and without moving `next_run`, so it was due again on the next poll (`scheduler_poll_interval_seconds`, 60 s by default) and reserved again. `watchlist_empty` and a lost `claim_job` kept a unit once each. `QuotaGate.claim` now returns the `ChatClaim`, and `run_job` keeps those exits inside its block (`worker.py:316-353`). The job still re-polls each minute while past its `daily_limit`; each poll now reserves and gives back, net zero (`Repoll` in the model). Tests: `backend/tests/scheduler/test_worker.py` (`*spends*`).
- **F5, a refund across UTC midnight landed on the next day.** Fixed in `967e553` as a side effect of the same claim: the `IntegrityError` loser's refund used to run `refund_daily_chat` with no `today`, so after midnight it decremented the next day's row and a third send ran on a full day. The claim now carries the day it was reserved on (`store.py:182`, `:169`).

Open findings on the code as-is:

- **F4, a process crash after the grant keeps the unit** (`asis_crash`, `asis_exits_crash`, `NoLeak`). The gate's commit is durable before any later step, so a worker killed between `store.py:293` and the turn commit leaks the unit, including a crash inside the claim block before its refund runs (`store.py:162-173`). A refund on exit cannot close this; it needs the claim and the turn to commit together, or a sweeper.

Suspicions the model refuted, given `RowLock`: a crash between the upsert and the over-cap decrement in `_reserve` rolls the whole transaction back (`asis_reserve_crash` passes); concurrent over-cap callers cannot push `n` above the cap where others see it, because the second upsert waits for the first commit; the unguarded decrement at `store.py:282-290` only undoes the same transaction's own +1.

**Open policy question.** Should a turn that started and then failed give its unit back? The code says no on every route: a `ChatClaim` is settled by `turn_started()` (`store.py:150-151`) and `refund_daily_chat`'s docstring limits refunds to "a turn that was not started" (`store.py:242-243`), the scheduler records a failed `run_skill` without a refund (`worker.py:375-385`), and `docs/backend/modules/24-deep-research.md` §3 says a failed report run is neither claimed back nor refunded. For a normal chat answer that errors mid-stream, no doc states the policy. The model follows the code (`RefundStarted = FALSE`). If product decides otherwise, `CapSafety` must count only turns that succeeded, and `broken_refund_started` stops being a broken variant.

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

Sync commit: `90e86e4ba3913d936e7af140cd20ae6298bb4dc9`

Move the sync commit forward only after confirming that the model still describes every file and function below at the new commit. `drift.sh` reads this line and this table.

| File | Functions |
|---|---|
| `backend/src/usage/store.py` | `ChatClaim`, `claim_daily_chat`, `SqlAlchemyUsageStore._increment`, `reserve_daily_chat`, `refund_daily_chat`, `_refund`, `_reserve` |
| `backend/src/api/turn_driver.py` | `enforce_pre_turn_gate`, `start_turn_stream` |
| `backend/src/api/routers/agent.py` | `agent_query`, `_reject_duplicate`, `_resolve_thread` |
| `backend/src/api/routers/threads_actions.py` | `regenerate_turn`, `edit_turn` |
| `backend/src/scheduler/quota.py` | `QuotaGate.claim`, `UsageQuotaGate.claim` |
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
