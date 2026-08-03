# Module: Chat interaction features (`backend/src/api/routers/`, `backend/src/feedback/`, `backend/src/voice/`)

The product-surface features that make the agent chat feel complete: message actions
(feedback, retry, edit-resend, stop, voice), and conversation history management
(rename/delete/sort/star/pin/search/export). Built on the streaming + conversation model in
[07-conversation-streaming.md](07-conversation-streaming.md).

## 1. Message-level actions

### Feedback (👍 / 👎)
- `POST /threads/{id}/turns/{tid}/feedback {rating: up|down, comment?}`.
- Backend resolves which **skill(s)** produced the turn (from the journal) and stores a
  `FeedbackEvent{user_id, turn_id, skill_name, rating, comment, ts}`.
- **Rolling 7-day quality score** per skill = `up / total`; old feedback expires naturally.
- When a skill's score `< 60%` with `>= 10` samples → a `FeedbackAlert` (pending) for admins.
  Endpoints: `GET /feedback/quality`, `GET /feedback/alerts`, `GET /feedback/recent`,
  `PUT /feedback/alerts/{id}/acknowledge|resolve`.

### Retry / regenerate (agent response)
- `POST /threads/{id}/turns/{tid}/regenerate {model?}` — re-runs the **same user input**,
  producing a new assistant turn. Prior response is kept as a sibling (versioned), so the UI
  can offer "‹ 2/3 ›" navigation. Optional `model` override (respects entitlements/BYOK).

### Edit user message & resend
- `POST /threads/{id}/turns/{tid}/edit {content}` — edits a **user** turn and re-runs from
  there, **branching** the message tree at that point (uses `switch_branch`, not a full
  `fork_thread` — the old branch stays in the same tree as a sibling, no context re-inflation;
  see [07-conversation-streaming.md](07-conversation-streaming.md)). Old branch is retained
  and, when left, summarized into a BranchSummaryEntry.
- Distinct from regenerate: edit changes the input; regenerate re-rolls the output.

### Stop / cancel streaming
- Client aborts the SSE connection **and** calls
  `POST /threads/{id}/turns/{tid}/cancel` (from [07](07-conversation-streaming.md)) so the
  server stops the loop between steps. Partial output is persisted with `status: cancelled`;
  the turn is resumable/regenerable.

### Voice
Two **separate** protocols (best-in-class differs per direction; both config-selected, same
idiom as `SourceAdapter`/`LLMClient`/`DeliveryChannel`), each with a **self-host option**
(whisper for STT — privacy/in-region/no per-minute cost) alongside cloud, mirroring the
`ollama` LLM pattern:

```
Transcriber.transcribe(audio, *, language="zh-TW", hints: list[str]=[], stream=False) -> Transcript
    # Transcript = text + per-segment confidence
Speaker.speak(ssml_or_text) -> audio
```

**Voice inquiry (STT) — a correctness path, not polish.** The audience speaks **Traditional
Chinese with code-switching** ("台積電 TSMC 2330") and a one-digit ticker error (2330→2380)
would silently send the wrong company into analysis. So:
- **zh-TW + code-switch aware:** default `language=zh-TW` (with auto-detect) preserving English
  tickers; provider chosen for Taiwan-Mandarin quality (whisper large-v3 self-host, or Google/
  Azure zh-TW with phrase hints) — recorded as a tradeoff, not "Whisper-class" only.
- **Vocabulary biasing (highest-leverage lever):** pass the user's watchlist symbols+names
  (`memories(type='watchlist')`) + a core 財經術語 lexicon as `hints` (whisper `initial_prompt`
  / Deepgram keyterms / Google `speechContexts` / Azure phrase list).
- **Number/ticker normalization:** a deterministic post-pass maps "二三三零/兩千三百三十"→`2330`
  against the symbol universe — never trust the raw ASR string for a symbol.
- **Confirm-before-send:** `POST /voice/transcribe` **returns the transcript only — it does NOT
  create a turn.** The client shows it for edit; the user submits via the normal
  `POST /agent/query`. Low-confidence / ambiguous symbols route to `ask_user`
  ([07](07-conversation-streaming.md)) ("which 台積電?") before analysis.
- **Ingestion contract:** accept `m4a/AAC` (iOS), `webm/ogg-opus` (Android/web); bound to
  ~60s, capped payload size; batch (whole-file) MVP — streaming/live-mode deferred (premature,
  and it removes the safety-critical confirm step).
- **Audio disposition (PII/PDPA):** **raw audio is discarded immediately after transcription**
  by default — only the resulting `text` message is persisted (no audio in `messages.content`;
  no blob store). If playback is ever added, audio goes to an object store with TTL + `user_id`
  namespacing + account-purge shred — not the app DB.

**TTS (agent response / read-aloud):** `POST /voice/speak {turn_id|text}` → audio stream.
- zh-TW reading via an **SSML `say-as` pass**: "2330" as 股號 digits, "-0.83%" as
  "負百分之零點八三", driven by the same symbol/number lexicon.
- **Governance:** the spoken text **must include the caveat** (non-trading/currentness), not
  just the analysis — a spoken answer that drops the caveat is a governance regression ([governance.md](../governance.md) §1/§8).

**Entitlement (voice split):** **STT / voice-input is Free** — it is the beginner's on-the-go
"ask" affordance (Xiaoyu), so gating it would break the core ask-loop; it is quota-bounded like text
turns (audio-minutes recorded in `usage_events`, counts against the tier cost budget, subject to a
Free daily cap + fair-use rate limit). **TTS / read-aloud stays Premium+** (higher per-use cost, not
core to asking). Cloud ASR/TTS keys are platform secrets (KMS, [16](16-infrastructure.md) §8;
`user_secrets.kind` gains `asr_key`/`tts_key` only if voice becomes BYOK). Confirm-before-send and the
spoken-caveat rule apply on **both** tiers.

### Other per-message
- Copy, share single message (governed export), citations/source attribution surfaced from
  the journal, caveat badges rendered from `caveat` events.

## 2. Conversation history management

All per-`user_id`, on the `threads` model from [07](07-conversation-streaming.md):

| Action | Endpoint |
|--------|----------|
| List (paginated) | `GET /threads?sort=updated|created|starred&filter=...&q=...` |
| Rename title | `PATCH /threads/{id} {title}` |
| Auto-title | generated from first turn (cheap model) if user hasn't named it |
| Delete | `DELETE /threads/{id}` (soft-delete → purge) |
| Sort | by `updated_at` / `created_at` / starred-first (query param) |
| Star / pin | `PATCH /threads/{id} {starred: true}` |
| Archive | `PATCH /threads/{id} {archived: true}` |
| Search | `GET /threads/search?q=...` — title + message content (FTS) |
| Tag / folder | `PATCH /threads/{id} {tags:[...]}` |
| Export | `GET /threads/{id}/export?format=md|json` |
| Branch (try alternative, in-tree — default) | `POST /threads/{id}/switch-branch` |
| Fork / duplicate (separate thread — rare) | `POST /threads/{id}/fork` |

`Thread` gains: `title, starred, archived, tags[], last_message_at`. Titles auto-generate but
are user-overridable; deleting is honored immediately (no shadow copies), consistent with the
memory-deletion rule in [09-memory.md](09-memory.md).

## 3. Draft & resilience

- **Draft persistence:** unsent input saved per-thread (survives reload).
- **Idempotent resend:** `Idempotency-Key` (from [07](07-conversation-streaming.md)) prevents
  duplicate turns on retry.
- **Offline/queued resume:** on reconnect, `Last-Event-ID` resumes an in-flight stream;
  a queued message is sent once.
- **Per-chat model/persona selector:** thread metadata can pin a model (entitlement-checked).

## Governance obligations
- Every action is per-`user_id`; no cross-user access.
- Regenerate/edit re-run through the full guardrail + entitlement pipeline (a retry can't
  bypass `sponsor` masking or non-trading checks).
- Exports/shares are governed artifacts (caveated, non-trading, no raw payloads).
- Voice transcripts/audio are treated as message content: not used to store credentials, and
  deletable with the thread.
