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
idiom as `SourceAdapter`/`LLMClient`/`DeliveryChannel`), in `voice/providers.py`:

```
Transcriber.transcribe(audio: bytes, *, language="zh-TW", hints: tuple[str, ...]=()) -> Transcript
    # Transcript = text + per-segment confidence + low_confidence flag; no `stream` param
Speaker.speak(ssml_or_text: str, *, language="zh-TW") -> SpeechAudio
```
Providers are selected by `voice_stt_provider` / `voice_tts_provider` (`api/routers/voice.py`): `stub`
(default; deterministic, no network I/O), STT `faster_whisper` (self-host, optional `voice` extra),
TTS `edge_tts` (free cloud voices) or `openai` (billed per call). Any other value 503s
(`voice_provider_unconfigured`). `voice/service.py`'s `transcribe`/`speak` + the ticker-normalization
pass are unit-tested.

**Voice inquiry (STT) — a correctness path, not polish.** The audience speaks **Traditional
Chinese with code-switching** ("台積電 TSMC 2330") and a one-digit ticker error (2330→2380)
would silently send the wrong company into analysis. So:
- **zh-TW + code-switch aware:** default `language=zh-TW`, preserving English tickers.
- **Vocabulary biasing:** the `transcribe` route reads the caller's watchlist symbols via
  `memory.watchlists.WatchlistStore.snapshot` (the relational store, not a `memories` row) and feeds
  them plus a core 財經術語 lexicon (`voice.service.build_hints`) as `hints`. Only a provider that
  honors hints (the stub just marks them) actually biases recognition.
- **Number/ticker normalization:** `voice.service._normalize_spoken_ticker` maps a run of spoken zh
  digits ("二三三零") to arabic and substitutes only when the result is in the caller-supplied
  symbol universe — never invents a symbol.
- **Confirm-before-send:** `POST /voice/transcribe` **returns the transcript only — it does NOT create
  a turn**. The client shows it for edit; the user submits via the normal `POST /agent/query`.
  `Transcript.low_confidence` is a flag on the response for the client to act on; the server does not
  route a low-confidence transcript into the `ask_user` tool.
- **Ingestion contract:** base64-encoded audio up to `_MAX_AUDIO_BYTES` (~12MB; `413 audio_too_large`)
  — a payload-size ceiling, not a format/duration validator. Batch whole-file only, no streaming mode.
- **Audio disposition (PII/PDPA):** raw audio is discarded right after transcription — only the
  resulting text is persisted, and only if the user sends it as a message.

**TTS (read-aloud):** `POST /voice/speak {text, language}` — plain `text` (max 8000 chars; no
`turn_id`-driven shape) and returns JSON `{audio_base64, content_type, spoken_text}`, not a raw audio
stream.
- **Not built:** an SSML `say-as` digit/percentage reading pass; `voice.service.speak` passes the
  caveated text straight to the `Speaker`.
- **Governance:** the spoken text **must include the caveat** (non-trading/currentness).
  `voice.service.ensure_caveated_for_speech` runs the same `agent.guardrails.check_output` pass
  written answers get and speaks the redacted/caveated version — enforced in code and unit-tested.

**Entitlement:** **both STT and TTS are Free** (no tier gate; neither is in `FEATURE_MIN_TIER`).
Both require only `require_user` and share one fair-use bound (`_claim_voice`): a per-minute rate
(`VOICE_REQUESTS_PER_MINUTE` = 20, `429 rate_limited`) for every provider, plus a per-UTC-day cap
(`VOICE_DAILY_LIMIT` = 100 per kind, `429 voice_limit_reached`) that applies **only** when the
configured provider bills per call (TTS `openai`); a failed request refunds its claim. Local
whisper and Edge voices have no daily quota. Confirm-before-send and the spoken-caveat rule apply to
every user.

### Other per-message
- Copy, share single message (governed export), citations/source attribution surfaced from
  the journal. There is no `caveat` event kind (see the event table in
  [07](07-conversation-streaming.md) §2): the caveat is inline prose inside the persisted
  `TextBlock`, not a separate badge fed by its own SSE event.

## 2. Conversation history management

All per-`user_id`, on the `threads` model from [07](07-conversation-streaming.md):

| Action | Endpoint | Status |
|--------|----------|--------|
| List | `GET /threads?include_archived=` | **real, but narrower than once documented** — no `sort`/`filter`/`q`/pagination query params exist; the store always orders by `last_message_at` desc and the only filter is `include_archived` |
| Rename title | `PATCH /threads/{id} {title}` | real, `If-Match` version CAS |
| Auto-title | generated from the first user message if the thread has no title | real — `api.turn_driver._maybe_title_thread` (`conversation/title_generator.py`) titles an empty-title thread once, best-effort, after the first answer; a client-set title is never overwritten |
| Delete | `DELETE /threads/{id}` (soft-delete) | real |
| Sort | by `updated_at` / `created_at` / starred-first (query param) | **not built** — see List row above |
| Star / pin | `PATCH /threads/{id} {starred: true}` | real |
| Archive | `PATCH /threads/{id} {archived: true}` | real |
| Search | `GET /threads/search?q=...` | real — title + message-content substring match (`LIKE lower(...)`, portable across SQLite/Postgres), not a dialect-specific FTS index yet |
| Tag / folder | `PATCH /threads/{id} {tags:[...]}` | real |
| Export | `GET /threads/{id}/export?format=md|json` | real |
| Branch (try alternative, in-tree — default) | `POST /threads/{id}/switch-branch` | real pointer-flip; does **not** yet auto-generate a `BranchSummaryBlock` for the branch being left (see [07](07-conversation-streaming.md) §1) |
| Fork / duplicate (separate thread — rare) | `POST /threads/{id}/fork` | real empty-shell creation (copies no message rows); does **not** yet seed the new thread with a summary of the source path (same gap as switch-branch) |

`Thread` gains: `title, starred, archived, tags[], last_message_at`. Titles auto-generate on the
first turn and stay user-overridable via `PATCH`. Deleting is honored immediately (no shadow copies),
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
- **Per-chat model / 語氣 selector: built.** `Thread.model_override` is set by `PATCH /threads/{id}`
  or the `model` field of `POST /agent/query` and read on every turn; `Thread.personality_override`
  is set via `PATCH` (`"default"` clears it) and threaded into `PersonalityContext.thread_override`.
  Only a BYOK caller can choose a model (validated against `GET /models`; ignored for everyone
  else); `PATCH` with `model_override: null` or `""` clears it (see [12](12-multi-user-tiers.md) §5).

## Governance obligations
- Every action is per-`user_id`; no cross-user access.
- Regenerate/edit re-run through the full guardrail + entitlement pipeline (a retry can't
  bypass `sponsor` masking or non-trading checks).
- Exports/shares are governed artifacts (caveated, non-trading, no raw payloads).
- Voice transcripts/audio are treated as message content: not used to store credentials, and
  deletable with the thread.
