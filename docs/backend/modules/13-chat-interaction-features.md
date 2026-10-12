# Module: Chat interaction features (`backend/src/api/routers/`, `backend/src/feedback/`, `backend/src/voice/`)

The product-surface features that make the agent chat feel complete: message actions
(feedback, retry, edit-resend, stop, voice), and conversation history management
(rename/delete/sort/star/pin/search/export). Built on the streaming + conversation model in
[07-conversation-streaming.md](07-conversation-streaming.md).

## 1. Message-level actions

### Feedback (👍 / 👎) — MINIMAL, REAL
- `POST /threads/{id}/turns/{tid}/feedback {rating: up|down, comment?}` writes a real row to
  `turn_feedback` (`SqlAlchemyFeedbackStore.submit`) — thumbs + an optional ≤500-char comment,
  scoped to `(turn_id, user_id)`. A re-submit **upserts** (updates rating/comment on the same
  row) rather than accumulating duplicates.
- **What is NOT built:** no per-skill attribution stored on the row, no rolling 7-day quality
  score, no `<60%`-with-`≥10`-samples alert, and none of `GET /feedback/quality`,
  `GET /feedback/alerts`, `GET /feedback/recent`, `PUT /feedback/alerts/{id}/acknowledge|resolve`
  exist. That quality-score/alert subsystem is a dedicated follow-up, not in scope here —
  `feedback/provenance.py`'s `SkillProvenance` shape exists for that follow-up to build against,
  but nothing today writes a provenance record onto a feedback row.

### Retry / regenerate (agent response) — REAL
- `POST /threads/{id}/turns/{tid}/regenerate` — re-runs the **same user input** that produced
  the target assistant turn through the real orchestrator turn-driver (the same
  `api.turn_driver.start_turn_stream` seam `/agent/query` uses — identical guardrail/entitlement/
  journal path, streamed as SSE exactly like `/agent/query`). The result is a **new sibling
  assistant turn** under the same parent user message; the original turn's row is untouched. The
  new turn's `regenerate_of` records which turn it re-ran, giving the UI what it needs for "‹ 2/3
  ›"-style version navigation (the navigation itself is a client concern — the backend does not
  track a "current version" pointer beyond the tree's own `active_leaf_message_id`). **Not
  built:** an optional `model` override param — regenerate always re-runs with the caller's
  normal model resolution, the same as any other turn.

### Edit user message & resend — REAL
- `POST /threads/{id}/turns/{tid}/edit {message}` — edits a **user** turn's text and resends,
  creating a **new branch**: a new user turn as a sibling of the original (same parent message),
  with a new assistant turn as its child, driven through the same real turn-driver seam as
  regenerate. The original user turn's row is never rewritten — canonical immutability holds; an
  edit only ever adds a new sibling subtree. The branch is immediately `switch_branch`-navigable.
  **Not built:** automatic BranchSummaryBlock generation for the branch being left behind — that
  needs a cheap-role `LLMClient` handle the interface layer does not hold today (the same gap
  `switch_branch` itself already documents); the branch itself is real, only the auto-summary is
  deferred.
- Distinct from regenerate: edit changes the input; regenerate re-rolls the output.

### Stop / cancel streaming
- Client aborts the SSE connection **and** calls
  `POST /threads/{id}/turns/{tid}/cancel` (from [07](07-conversation-streaming.md)) so the
  `TurnRegistry`'s `RunningTurn.request_cancel()` flips the cooperative cancel token and hard-
  cancels the driving task; the stream ends with a `turn.end{status: "cancelled"}` frame.
  **Not accurate as written:** "Partial output is persisted with `status: cancelled`" —
  `ConversationStore.append_turn` (the only write path `api/turn_driver.py` calls after a
  cancellation) has no `status` parameter at all; every persisted `TurnRow` is hardcoded to
  `TurnStatus.DONE`. A cancelled turn therefore is **not** persisted as a distinct row with
  `status: cancelled` today — the SSE stream reports the cancellation live, but nothing durable
  records it happened, and there is no assistant message row for a cancelled turn at all (the
  driver returns before calling `_persist_answer`). "Resumable/regenerable" holds only in the
  sense that the thread itself is unaffected and a fresh `/agent/query` or `regenerate` on a
  prior turn still works normally.

### Voice
Two **separate** protocols (best-in-class differs per direction; both config-selected, same
idiom as `SourceAdapter`/`LLMClient`/`DeliveryChannel`), each with a **self-host option**
(whisper for STT — privacy/in-region/no per-minute cost) alongside cloud, mirroring the
`ollama` LLM pattern:

```
Transcriber.transcribe(audio: bytes, *, language="zh-TW", hints: tuple[str, ...]=()) -> Transcript
    # Transcript = text + per-segment confidence (voice/providers.py; no `stream` param)
Speaker.speak(ssml_or_text: str, *, language="zh-TW") -> SpeechAudio
```
Real today: `voice/providers.py`'s `Transcriber`/`Speaker` Protocols plus deterministic
`StubTranscriber`/`StubSpeaker` implementations (no network I/O — the only providers that ship
in the default profile); `voice_stt_provider`/`voice_tts_provider` config keys select `"stub"`
by default, and any other value 503s (`voice_provider_unconfigured`) since no real cloud/
self-host adapter is wired yet. `voice/service.py`'s `transcribe`/`speak` + the ticker-
normalization pass are real and unit-tested, not just a protocol sketch.

**Voice inquiry (STT) — a correctness path, not polish.** The audience speaks **Traditional
Chinese with code-switching** ("台積電 TSMC 2330") and a one-digit ticker error (2330→2380)
would silently send the wrong company into analysis. So:
- **zh-TW + code-switch aware:** default `language=zh-TW` (with auto-detect) preserving English
  tickers; provider chosen for Taiwan-Mandarin quality (whisper large-v3 self-host, or Google/
  Azure zh-TW with phrase hints) — recorded as a tradeoff, not "Whisper-class" only.
- **Vocabulary biasing (highest-leverage lever), real:** `api/routers/voice.py`'s `transcribe`
  route reads the caller's watchlist symbols via `memory.watchlists.WatchlistStore.snapshot`
  (the relational store, **not** `memories(type='watchlist')` — that type is retired as a write
  target per [09-memory.md](09-memory.md)) and feeds them plus a core 財經術語 lexicon
  (`voice.service.build_hints`) as `hints`. There is no real whisper/Deepgram/Google/Azure
  adapter to actually consume those hints yet — only the stub provider (which uses `hints` as a
  marker, not real biasing) ships today.
- **Number/ticker normalization, real:** `voice.service._normalize_spoken_ticker` maps a run of
  spoken zh digits ("二三三零") to arabic and substitutes only when the result is in the
  caller-supplied symbol universe — never invents a symbol.
- **Confirm-before-send, real:** `POST /voice/transcribe` **returns the transcript only — it
  does NOT create a turn** (`voice/service.py`'s own docstring: "This module has no reference to
  the agent loop, conversation store, or orchestrator"). The client shows it for edit; the user
  submits via the normal `POST /agent/query`. **Not built:** routing a low-confidence/ambiguous
  transcript to `ask_user` — `ask_user` itself doesn't exist yet (see
  [07](07-conversation-streaming.md) §2); today `Transcript.low_confidence` is just a flag on the
  response for the client to act on however it chooses.
- **Ingestion contract:** the route accepts base64-encoded audio up to `_MAX_AUDIO_BYTES`
  (~12MB, "~a minute of compressed audio") — a hard payload-size ceiling, not a format/duration
  validator; there is no server-side check of the actual audio format (m4a/webm/ogg-opus) or
  duration, and no streaming/live mode (batch whole-file only, matching the doc's intent).
- **Audio disposition (PII/PDPA):** **raw audio is discarded immediately after transcription**
  by default — only the resulting `text` message is persisted (no audio in `messages.content`;
  no blob store). If playback is ever added, audio goes to an object store with TTL + `user_id`
  namespacing + account-purge shred — not the app DB.

**TTS (agent response / read-aloud):** `POST /voice/speak {text, language}` — takes a plain
`text` field only (**not** `{turn_id|text}`; there is no `turn_id`-driven "speak this prior
turn" shape) and returns a JSON body (`{audio_base64, content_type, spoken_text}`), not a raw
audio stream/binary response.
- **Not built:** the SSML `say-as` digit/percentage reading pass ("2330" as 股號 digits,
  "-0.83%" as "負百分之零點八三"). `voice.service.speak` passes the (caveat-guaranteed) text
  straight to the `Speaker` provider with no SSML transformation step — only the caveat-
  enforcement rule below is real.
- **Governance, real:** the spoken text **must include the caveat** (non-trading/currentness).
  `voice.service.ensure_caveated_for_speech` runs the same `agent.guardrails.check_output` pass
  written answers get and speaks the redacted/caveated version if it appended one — enforced in
  code, unit-tested, not just documented policy.

**Entitlement (voice split):** **STT / voice-input is Free** — `POST /voice/transcribe` requires
only `require_user` (any authenticated tier), matching the "beginner's on-the-go ask affordance"
intent. **Not built:** the quota-bounding described here — there is no audio-minute tracking
anywhere in the codebase (`usage_events` has no such column; `UsageEvent`/`UsageAccumulator`
track tokens/tool-calls/cost, not audio minutes) and `/voice/transcribe` calls no rate limiter
and no `enforce_pre_turn_gate`-equivalent daily-cap check — it is currently ungated beyond plain
authentication. **TTS / read-aloud staying Premium+ is real:** `POST /voice/speak` calls
`accounts.entitlements.feature_allowed(user, "tts_read_aloud")`, and `tts_read_aloud` maps to
`Tier.PREMIUM` in `accounts/entitlements.py` — an unentitled caller gets a 403, not a masked
teaser (a deliberate difference from the `sponsor` data-masking shape, since this gates a whole
binary-audio endpoint, not one data field). Cloud ASR/TTS keys as platform secrets is aspirational
too: `user_secrets.kind`'s CHECK constraint already includes `asr_key`/`tts_key` values
(`db/models/`), but no cloud/self-host provider exists to consume them yet — only the stub
providers ship. Confirm-before-send and the spoken-caveat rule are real and apply on **both**
tiers.

### Other per-message
- Copy, share single message (governed export), citations/source attribution surfaced from
  the journal. "Caveat badges rendered from `caveat` events" describes an unbuilt event kind
  (see [07](07-conversation-streaming.md) §2's implementation-status note) — today the caveat is
  inline prose inside the persisted `TextBlock`, not a separate rendered badge fed by its own
  SSE event.

## 2. Conversation history management

All per-`user_id`, on the `threads` model from [07](07-conversation-streaming.md):

| Action | Endpoint | Status |
|--------|----------|--------|
| List | `GET /threads?include_archived=` | **real, but narrower than once documented** — no `sort`/`filter`/`q`/pagination query params exist; the store always orders by `last_message_at` desc and the only filter is `include_archived` |
| Rename title | `PATCH /threads/{id} {title}` | real, `If-Match` version CAS |
| Auto-title | generated from first turn (cheap model) if user hasn't named it | **not built** — no code generates a title from a turn; a thread's `title` is whatever the client passed to `POST /threads` (default `""`) until a client `PATCH`es it |
| Delete | `DELETE /threads/{id}` (soft-delete) | real |
| Sort | by `updated_at` / `created_at` / starred-first (query param) | **not built** — see List row above |
| Star / pin | `PATCH /threads/{id} {starred: true}` | real |
| Archive | `PATCH /threads/{id} {archived: true}` | real |
| Search | `GET /threads/search?q=...` | real — title + message-content substring match (`LIKE lower(...)`, portable across SQLite/Postgres), not a dialect-specific FTS index yet |
| Tag / folder | `PATCH /threads/{id} {tags:[...]}` | real |
| Export | `GET /threads/{id}/export?format=md|json` | real |
| Branch (try alternative, in-tree — default) | `POST /threads/{id}/switch-branch` | real pointer-flip; does **not** yet auto-generate a `BranchSummaryBlock` for the branch being left (see [07](07-conversation-streaming.md) §1) |
| Fork / duplicate (separate thread — rare) | `POST /threads/{id}/fork` | real empty-shell creation (copies no message rows); does **not** yet seed the new thread with a summary of the source path (same gap as switch-branch) |

`Thread` gains: `title, starred, archived, tags[], last_message_at`. There is no auto-titling
today, so "Titles auto-generate but are user-overridable" describes an unbuilt feature — titles
are user-set only, defaulting to empty. Deleting is honored immediately (no shadow copies),
consistent with the memory-deletion rule in [09-memory.md](09-memory.md).

## 3. Draft & resilience

- **Draft persistence: not built as an API path.** `threads.draft_content` exists as a DB
  column (`db/models/`) and is read into the `Thread` model, but no router reads or writes
  it — `PatchThreadRequest` (`api/routers/threads_actions.py`) has no `draft_content` field, and
  no other endpoint sets it. Unsent-input persistence is therefore not currently reachable over
  the API despite the column existing.
- **Idempotency-Key is accepted and persisted, but not actually de-duped on the live
  `/agent/query` path.** `POST /agent/query` reads the `Idempotency-Key` header and stamps it
  onto the new user turn (`turns.idempotency_key`, which has a real
  `UNIQUE(user_id, idempotency_key)` DB constraint), but `api/routers/agent.py` calls
  `store.append_turn` directly with no idempotency check beforehand and no `try/except` around
  the insert — a retried request with the same key would either hit the DB uniqueness
  constraint and raise (uncaught, so a 500) or, if the constraint is per-user rather than
  per-request-flow, simply attempt a second insert. The in-process de-dupe map that returns the
  *same* `TurnHandle` for a repeated key (`conversation.turn_lifecycle.TurnLifecycle.start`,
  tested in `tests/conversation/test_turn_lifecycle.py`) is real and does exactly what this
  bullet describes — but, per [07](07-conversation-streaming.md) §3, `TurnLifecycle` is not the
  class the live HTTP path drives; `agent.py`/`turn_driver.py` never call it. So idempotent
  resend as a *user-visible* guarantee is not yet wired end-to-end.
- **Offline/queued resume:** on reconnect, `Last-Event-ID` resumes an in-flight stream (real,
  see [07](07-conversation-streaming.md) §2's Resumability section); "a queued message is sent
  once" describes client-side behavior this backend doesn't itself implement (no server-side
  outbox).
- **Per-chat model/persona selector: partially built.** `Thread.personality_override` is a real
  column, read and threaded into `PersonalityContext.thread_override` on every turn (`agent.py`/
  `threads_actions.py`), so a thread-pinned personality does take effect — but there is no
  endpoint that writes `personality_override` (`PatchThreadRequest` doesn't expose it either), so
  nothing sets it today except a manual DB write. `Thread.model_override` is a column that is
  read into the `Thread` model but is never consulted by any turn-start code path (`agent.py`,
  `turn_driver.py`) — a per-chat *model* pin does not exist yet, unlike personality.

## Governance obligations
- Every action is per-`user_id`; no cross-user access.
- Regenerate/edit re-run through the full guardrail + entitlement pipeline (a retry can't
  bypass `sponsor` masking or non-trading checks).
- Exports/shares are governed artifacts (caveated, non-trading, no raw payloads).
- Voice transcripts/audio are treated as message content: not used to store credentials, and
  deletable with the thread.
