----------------------------- MODULE ChatQuota -----------------------------
(***************************************************************************)
(* The daily chat allowance of one user: reserve a unit, run the steps     *)
(* before the turn starts, start the turn, give the unit back on the paths *)
(* the code gives it back. One user is enough: the counter row is keyed by *)
(* (user_id, counter, window_key) and no step reads another user's row.    *)
(*                                                                         *)
(* Every action names the code it stands for. Line numbers are at the sync *)
(* commit recorded in ../SKILL.md.                                         *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets, Sequences

CONSTANTS
    Req,           \* request ids, a small set of numbers
    Route,         \* [Req -> {"query", "edit", "regenerate", "scheduled"}]
    Key,           \* [Req -> idempotency keys \cup {"none"}]; only "query" sends one
    Cap,           \* the tier's chat_limit_per_day (accounts/entitlements.py TIER_LIMITS)
    Days,          \* 1, or 2 to let the UTC day roll over once
    FailAt,        \* step names (see Steps) that may fail or exit early
    CrashIn,       \* subset of {"reserve", "post_claim"}: where the process may die
    RefundMode,    \* "as_is": refund only where the code refunds; "all": refund every pre-start exit
    RefundDay,     \* "now": refund the row of the day the refund runs (code); "claim": the claimed day
    RowLock,       \* TRUE: the _reserve upsert holds the counter row until its commit (code)
    UniqueKey,     \* TRUE: uq_turns_user_idempotency_key exists (code)
    DupRefund,     \* TRUE: agent.py refunds on the IntegrityError path (code)
    RefundStarted  \* FALSE: a turn that started keeps its unit (code)

NoOne == 0

(* The steps between a granted reservation and the turn start, per route. *)
(* The last step of each route is the one whose success starts the turn.  *)
Steps == [
    query      |-> <<"commit_gate_tx",       \* api/routers/agent.py:141 session.commit()
                     "process_attachments",  \* agent.py:142, 422 too_many_attachments / attachment_extraction_failed
                     "resolve_thread",       \* agent.py:146 -> _resolve_thread:239, 404 thread_not_found
                     "patch_thread",         \* agent.py:147-158 update_thread, personality_patch 422 unknown_personality
                     "append_turn",          \* agent.py:166 ConversationStore.append_turn (insert + flush)
                     "commit_turn">>,        \* agent.py:190 session.commit(); then start_turn_stream agent.py:198
    edit       |-> <<"append_turn",          \* api/routers/threads_actions.py:736 store.append_turn
                     "commit_turn">>,        \* threads_actions.py:750 session.commit(); start_turn_stream :753
    regenerate |-> <<"commit_gate_tx">>,     \* threads_actions.py:660 session.commit(); start_turn_stream :661
    scheduled  |-> <<"daily_limit_check",    \* scheduler/worker.py:313 _daily_limit_reached -> return False
                     "resolve_inputs",       \* worker.py:324-337 inputs is None -> watchlist_empty, return False
                     "claim_job",            \* worker.py:343 claim_job lost to another worker -> return False
                     "commit_claim">>        \* worker.py:347 session.commit(); run_skill worker.py:357
]

Terminal == {"rejected_dup", "rejected_full", "failed", "refunded", "crashed",
             "started", "started_refunded"}
ClaimedPhases == {"claimed", "refund_fail", "refund_dup"}

VARIABLES
    day,        \* current UTC day
    n,          \* [1..Days -> Nat]: committed usage_counters.n of ('chat', day)
    lock,       \* [1..Days -> Req \cup {NoOne}]: holder of the counter row lock (RowLock only)
    pend,       \* [Req -> 0..1]: the reserve transaction's uncommitted +1 (RowLock only)
    pc,         \* [Req -> phase]
    step,       \* [Req -> Nat]: index into Steps[Route[r]] while claimed
    wk,         \* [Req -> 0..Days]: window_key _reserve computed (store.py:213)
    used,       \* [Req -> Nat]: the n the upsert RETURNING gave this request
    claimDay,   \* [Req -> 0..Days]: day of the granted claim
    startedOn,  \* [Req -> 0..Days]: day charged for a started turn, 0 = not started
    turnRow,    \* [Req -> {"none", "pending", "committed"}]: this request's user turn row
    badReject   \* a request got chat_limit_reached while the day was not truly full

vars == <<day, n, lock, pend, pc, step, wk, used, claimDay, startedOn, turnRow, badReject>>

Ran(d)       == {o \in Req : startedOn[o] = d}
Held(d)      == {o \in Req : pc[o] \in ClaimedPhases /\ claimDay[o] = d}
InReserve(d) == {o \in Req : pc[o] = "reserve_decide" /\ wk[o] = d}
(* Units truly in use on day d: started turns, granted claims, and reserves that will be granted. *)
Occupied(d)  == Cardinality(Ran(d)) + Cardinality(Held(d))
                + Cardinality({o \in InReserve(d) : used[o] <= Cap})

CurStep(r)  == Steps[Route[r]][step[r]]
IsLast(r)   == step[r] = Len(Steps[Route[r]])
(* worker.py:313-322: the daily-limit skip returns without set_job_status, so next_run is not  *)
(* advanced and the next poll (scheduler_poll_interval_seconds) runs the same job again.       *)
Repoll(r)   == Route[r] = "scheduled" /\ CurStep(r) = "daily_limit_check"
KeyClash(r) == UniqueKey /\ Key[r] /= "none"
SameKey(r)  == {o \in Req \ {r} : Key[o] = Key[r]}

TypeOK ==
    /\ day \in 1..Days
    /\ n \in [1..Days -> Nat]
    /\ lock \in [1..Days -> Req \cup {NoOne}]
    /\ pend \in [Req -> 0..1]
    /\ turnRow \in [Req -> {"none", "pending", "committed"}]
    /\ badReject \in BOOLEAN

Init ==
    /\ day = 1
    /\ n = [d \in 1..Days |-> 0]
    /\ lock = [d \in 1..Days |-> NoOne]
    /\ pend = [r \in Req |-> 0]
    /\ pc = [r \in Req |-> "init"]
    /\ step = [r \in Req |-> 0]
    /\ wk = [r \in Req |-> 0]
    /\ used = [r \in Req |-> 0]
    /\ claimDay = [r \in Req |-> 0]
    /\ startedOn = [r \in Req |-> 0]
    /\ turnRow = [r \in Req |-> "none"]
    /\ badReject = FALSE

(* agent.py:122-124 _reject_duplicate: a keyed query whose key already has a committed turn  *)
(* gets 409 duplicate_request before the gate, so it claims nothing. Other routes go to the  *)
(* gate (turn_driver.py:121, quota.py:65).                                                   *)
Arrive(r) ==
    /\ pc[r] = "init"
    /\ pc' = [pc EXCEPT ![r] =
                IF Route[r] = "query" /\ Key[r] /= "none"
                   /\ \E o \in SameKey(r) : turnRow[o] = "committed"
                THEN "rejected_dup" ELSE "reserve"]
    /\ UNCHANGED <<day, n, lock, pend, step, wk, used, claimDay, startedOn, turnRow, badReject>>

(* store.py:213 window_key, then _increment store.py:134-163: one INSERT .. ON CONFLICT DO   *)
(* UPDATE n = n + 1 RETURNING n. With RowLock the +1 stays uncommitted and the row stays     *)
(* locked until the commit at store.py:228 or :230; a second upsert on the row waits.        *)
ReserveUpsert(r) ==
    /\ pc[r] = "reserve"
    /\ wk' = [wk EXCEPT ![r] = day]
    /\ used' = [used EXCEPT ![r] = n[day] + 1]
    /\ IF RowLock
         THEN /\ lock[day] = NoOne
              /\ lock' = [lock EXCEPT ![day] = r]
              /\ pend' = [pend EXCEPT ![r] = 1]
              /\ UNCHANGED n
         ELSE /\ n' = [n EXCEPT ![day] = n[day] + 1]
              /\ UNCHANGED <<lock, pend>>
    /\ pc' = [pc EXCEPT ![r] = "reserve_decide"]
    /\ UNCHANGED <<day, step, claimDay, startedOn, turnRow, badReject>>

(* store.py:218-231. Over the cap: UPDATE n = n - 1 in the same session, commit, refuse      *)
(* (turn_driver.py:122 429 chat_limit_reached). Else commit and grant.                       *)
ReserveDecide(r) ==
    /\ pc[r] = "reserve_decide"
    /\ LET d == wk[r] IN
       IF used[r] > Cap
         THEN /\ IF RowLock
                   THEN /\ lock' = [lock EXCEPT ![d] = NoOne]
                        /\ pend' = [pend EXCEPT ![r] = 0]
                        /\ UNCHANGED n
                   ELSE /\ n' = [n EXCEPT ![d] = n[d] - 1]
                        /\ UNCHANGED <<lock, pend>>
              /\ pc' = [pc EXCEPT ![r] = "rejected_full"]
              /\ badReject' = (badReject \/ Occupied(d) < Cap)
              /\ UNCHANGED <<step, claimDay>>
         ELSE /\ IF RowLock
                   THEN /\ n' = [n EXCEPT ![d] = n[d] + pend[r]]
                        /\ lock' = [lock EXCEPT ![d] = NoOne]
                        /\ pend' = [pend EXCEPT ![r] = 0]
                   ELSE UNCHANGED <<n, lock, pend>>
              /\ pc' = [pc EXCEPT ![r] = "claimed"]
              /\ step' = [step EXCEPT ![r] = 1]
              /\ claimDay' = [claimDay EXCEPT ![r] = d]
              /\ UNCHANGED badReject
    /\ UNCHANGED <<day, wk, used, startedOn, turnRow>>

Advance(r) ==
    IF IsLast(r)
      THEN /\ pc' = [pc EXCEPT ![r] = "started"]
           /\ startedOn' = [startedOn EXCEPT ![r] = claimDay[r]]
           /\ UNCHANGED step
      ELSE /\ step' = [step EXCEPT ![r] = step[r] + 1]
           /\ UNCHANGED <<pc, startedOn>>

(* One step before the start succeeds. append_turn is conversation/store.py:282-336: the     *)
(* turns insert is flushed at :313, where the unique index is checked. A same-key row that   *)
(* is committed raises IntegrityError (agent.py:175); one still uncommitted makes the insert *)
(* wait for that transaction, as Postgres does.                                              *)
StepOk(r) ==
    /\ pc[r] = "claimed"
    /\ LET s == CurStep(r) IN
       CASE s = "append_turn" /\ KeyClash(r) /\ \E o \in SameKey(r) : turnRow[o] = "committed" ->
                 \* agent.py:179 rollback, :182 refund_daily_chat, :185 409 duplicate_request
                 /\ pc' = [pc EXCEPT ![r] = IF DupRefund THEN "refund_dup" ELSE "rejected_dup"]
                 /\ UNCHANGED <<step, startedOn, turnRow>>
         [] s = "append_turn" ->
                 /\ ~(KeyClash(r) /\ \E o \in SameKey(r) : turnRow[o] = "pending")
                 /\ turnRow' = [turnRow EXCEPT ![r] = "pending"]
                 /\ Advance(r)
         [] s = "commit_turn" ->
                 /\ turnRow' = [turnRow EXCEPT ![r] = "committed"]
                 /\ Advance(r)
         [] OTHER ->
                 /\ Advance(r)
                 /\ UNCHANGED turnRow
    /\ UNCHANGED <<day, n, lock, pend, wk, used, claimDay, badReject>>

(* A step before the start raises (a handled ApiError, a DB error) or, in the scheduler,     *)
(* returns False. The request session rolls back a flushed turn row. As-is, nothing gives    *)
(* the unit back on these paths; RefundMode "all" adds the refund the fixed variant needs.   *)
StepFail(r) ==
    /\ pc[r] = "claimed"
    /\ CurStep(r) \in FailAt
    /\ turnRow' = [turnRow EXCEPT ![r] = "none"]
    /\ pc' = [pc EXCEPT ![r] =
                IF RefundMode = "all" THEN "refund_fail"
                ELSE IF Repoll(r) THEN "init" ELSE "failed"]
    /\ UNCHANGED <<day, n, lock, pend, step, wk, used, claimDay, startedOn, badReject>>

RefundTarget(r) == IF RefundDay = "now" THEN day ELSE claimDay[r]

(* store.py:196-208 _refund via refund_daily_chat store.py:176-183: its own session, one      *)
(* UPDATE n = n - 1 WHERE window_key = today AND n > 0, then commit. agent.py:182 passes no  *)
(* `today`, so the row is the one for the day the refund runs (RefundDay "now").             *)
Refund(r) ==
    /\ \/ pc[r] \in {"refund_fail", "refund_dup"}
       \/ pc[r] = "started" /\ RefundStarted
    /\ LET d == RefundTarget(r) IN
       /\ RowLock => lock[d] = NoOne
       /\ n' = [n EXCEPT ![d] = IF n[d] > 0 THEN n[d] - 1 ELSE n[d]]
    /\ pc' = [pc EXCEPT ![r] =
                CASE pc[r] = "refund_dup" -> "rejected_dup"
                  [] pc[r] = "started"    -> "started_refunded"
                  [] Repoll(r)            -> "init"
                  [] OTHER                -> "refunded"]
    /\ UNCHANGED <<day, lock, pend, step, wk, used, claimDay, startedOn, turnRow, badReject>>

(* The worker process dies between two steps. Its open transaction rolls back: the reserve's *)
(* uncommitted +1 and lock (RowLock), or a flushed turn row. Committed rows stay.             *)
Crash(r) ==
    \/ /\ "reserve" \in CrashIn
       /\ pc[r] = "reserve_decide"
       /\ IF RowLock
            THEN /\ lock' = [lock EXCEPT ![wk[r]] = NoOne]
                 /\ pend' = [pend EXCEPT ![r] = 0]
            ELSE UNCHANGED <<lock, pend>>
       /\ pc' = [pc EXCEPT ![r] = "crashed"]
       /\ UNCHANGED <<day, n, step, wk, used, claimDay, startedOn, turnRow, badReject>>
    \/ /\ "post_claim" \in CrashIn
       /\ pc[r] \in ClaimedPhases
       /\ turnRow' = [turnRow EXCEPT ![r] = IF turnRow[r] = "pending" THEN "none" ELSE turnRow[r]]
       /\ pc' = [pc EXCEPT ![r] = "crashed"]
       /\ UNCHANGED <<day, n, lock, pend, step, wk, used, claimDay, startedOn, badReject>>

(* UTC midnight: _day_key(datetime.now(UTC).date()) starts naming the next row. *)
Tick ==
    /\ day < Days
    /\ day' = day + 1
    /\ UNCHANGED <<n, lock, pend, pc, step, wk, used, claimDay, startedOn, turnRow, badReject>>

Finished ==
    /\ \A r \in Req : pc[r] \in Terminal
    /\ UNCHANGED vars

Next ==
    \/ \E r \in Req : Arrive(r) \/ ReserveUpsert(r) \/ ReserveDecide(r) \/ StepOk(r)
                      \/ StepFail(r) \/ Refund(r) \/ Crash(r)
    \/ Tick
    \/ Finished

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
(* Cap safety: on every UTC day, the turns that started on that day's allowance never       *)
(* outnumber the cap.                                                                        *)
CapSafety == \A d \in 1..Days : Cardinality(Ran(d)) <= Cap

(* No leak: today's counter never exceeds the turns that started today plus the claims still *)
(* in flight (granted and not yet started or returned, or mid-reserve). A request that ends  *)
(* without starting a turn has given its unit back.                                          *)
NoLeak == n[day] <= Cardinality(Ran(day)) + Cardinality(Held(day)) + Cardinality(InReserve(day))

(* The user-visible side of a leak: chat_limit_reached only when the day is truly full. *)
RejectOnlyWhenFull == ~badReject

(* One idempotency key never produces two committed turns. *)
OneTurnPerKey ==
    \A r1, r2 \in Req :
        (r1 /= r2 /\ Key[r1] = Key[r2] /\ Key[r1] /= "none")
            => ~(turnRow[r1] = "committed" /\ turnRow[r2] = "committed")
=============================================================================
