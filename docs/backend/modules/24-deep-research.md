# 24 — Deep research (Pro)

A budgeted, cited, multi-step web investigation that runs **inside a chat turn** and streams its
progress. Implements the `deep_research` feature promised in [ADR-0014](../adr/0014-web-search.md)
and gated in [12](12-multi-user-tiers.md). Code: `backend/src/research/`, wired by
`api/deep_research_turn.py`.

## 1. Shape of a run

**Code runs the loop; the model is only asked to restate, to plan, to judge "is this enough?", and
to write.**

```
(restate the question, describe attached images)
        │
      plan ─▶ sub-question 1 ┐   each: search ─▶ read ─▶ review  × ≤ 3 rounds
              sub-question 2 ├─ run at the same time, sharing one budget
              sub-question N ┘   (web search + Miru market data)
                                   │
                              write ─▶ render
```

- **Conversation and images.** A follow-up ("那第四季呢？") is first rewritten into one standalone
  question from the last few turns of the thread (best effort: if that step fails the raw message is
  used). Attached images are described once by the model and the description is passed to the planner
  as **context only** - it is never a source, so nothing can be cited to it; a description that reads
  like instructions is dropped, and a failed description just means no extra context.
- **Sub-questions in parallel.** The plan has 1-5 independent sub-questions. They run together
  (`asyncio.gather`) against one shared source ledger and one shared budget. Totals are claimed
  *before* the await (as in the quota rules of backend/CLAUDE.md §5), so parallel lanes cannot overshoot
  a ceiling, and `SourceLedger.claim_unread` hands each page to exactly one lane. One lane failing
  does not end the others.
- **Miru market data next to the web.** A sub-question about a listed company may also request
  lookups from a fixed list (`research/datatools.py`: company profile, score, institutional flows,
  margin/short, monthly revenue, income statement, valuation, analyst consensus, dividends, technical
  indicators, foreign holding). The model names a tool from that list and a `(symbol, market)` pair -
  never a free-form tool or argument, so a request cannot ask for the whole market. The adapter
  (`research.adapters.ToolData`) runs it like any chat tool call: whole-market argument check, the
  tier entitlement (a locked result is simply left out), then `scan_structured_payload` on the output.
  A result becomes a citeable **data source** (`kind: "data"`, no URL, shown as a source without a link).

| Ceiling (`ResearchBudget`) | Default |
|---|---|
| rounds | 3 (per sub-question) |
| searches | 24 (whole run) |
| pages read | 30 (whole run; ≤ 4 per sub-question per round, 6 fetched concurrently) |
| market-data lookups | 12 (whole run) |
| wall-clock | 180 s (the final write always gets ≥ 25 s) |
| text per source shown to the model | 4,000 chars |

Hitting any ceiling (or the model never saying "sufficient") ends the research and **still writes
a report from what was read**; the report says why it stopped (`stopped_because`). Search / fetch /
data / review failures are tolerated and logged; a plan or write failure raises `ResearchFailedError`.

## 2. Governance (what makes it safe to ship)

- **Citations are structural.** The model returns *claims* (`text` + `source_ids`). `build_report`
  keeps only ids that exist in the source ledger **and were actually read**; a claim left with none
  is dropped (and counted in `dropped_claims`). Nothing uncited reaches the reader
  ([ADR-0014](../adr/0014-web-search.md): every web-derived claim is cited).
- **Fetched text is data, never instructions.** Pages (and market-data results, `kind="data"`) are wrapped in `<source id url title>` blocks,
  control characters are stripped, `<source` / `</source` inside a page are defused, pages that
  contain instruction-like phrases (EN + 中文) are excluded entirely, and both system prompts carry
  the untrusted-content notice.
- **No markdown injection.** Claim text, headings and titles are shown as plain text (`[ ] ( ) < > ! `` ` ``
  are escaped), so a hostile page cannot smuggle a link, tracking image or HTML into the answer.
- **No trading output.** Claim text, headings, title and open questions pass `check_output` claim by
  claim, and the finished markdown is scanned again and stamped with the standard caveat.
- **SSRF.** A URL must survive `schemas/urlsafety.canonical_url` (public http/https, no credentials,
  no internal/legacy-IPv4 hosts) to enter the ledger at all. The fetch (`sources/web_search.py::
  WebSearchAdapter.fetch_page`, the only network code) additionally resolves DNS itself and rejects
  the request if **any** answer is non-public, connects to the checked IP (TLS verifies the real
  hostname via SNI), follows redirects by hand re-validating **every hop** (max 4), caps the body at
  1 MB (plain `Accept-Encoding: identity`; any compressed response is refused, so no decompression
  bomb) and reads only `text/html`, `application/xhtml+xml`, `text/plain`.

## 3. Tier and limits

`POST /agent/query` (and `regenerate` / `edit`) accept `deep_research: true`. The pre-turn gate
(`enforce_pre_turn_gate(..., deep_research=True)`) runs: rate limit → `feature_allowed(user,
"deep_research")` (else `403 feature_not_entitled`) → the normal chat reservation. There is **no
daily report cap**: a run is bounded by its own budget (`ResearchBudget`: searches, pages, data
calls, wall-clock), and the per-minute rate limit and the daily-chat allowance still apply. A run
that fails ends with a plain "no report produced" answer; nothing is claimed or refunded.

**Safety rate.** Because there is no daily cap, report *starts* are also limited to 4 per 10 minutes per
user (a separate counter on the same rate limiter, `429 rate_limited`, retryable). It is an abuse guard,
not a quota: normal use never meets it, and nothing in the UI mentions it.

**Regenerate / edit.** The request's mode is stored on the user turn (`turns.mode = 'deep_research'`,
nullable). `regenerate` and `edit` re-run the way the original ran unless the body says
`deep_research: true|false` explicitly; a retried report goes through the same gate and spends
another run.

## 4. Streaming and persistence

Progress reuses existing frames: each stage (understanding, planning, `searching: <query>`,
`reading N pages`, `looking up market data`, reviewing, writing) is an `agent.spawned` lane closed by
`agent.done`. Sub-questions run side by side, so each has its own open lane, labelled `[n] ...`; the
whole-run stages (understanding, planning, writing) share one. The consulted web pages are one
`web_search.result` frame. The answer is an ordinary `OrchestratorResult` (markdown with `[n]`
citations, a sources list, an "as of" date and an early-stop note), so persistence, usage
recording (`UsageAccumulator` fed by every model call) and the final `message` frame are unchanged.

## 5. Not built yet

Per-user concurrency cap (one report at a time), report export, reading PDFs, and giving the planner
more data tools (the list is deliberately short and read-only).

## 6. Cost shown for a run

Deep research (and chat) usage is recorded per call in `llm_usage`. The settings usage page shows
the provider-reported charge where there is one (OpenRouter) and otherwise an estimate from
`llm/pricing.py`: recorded tokens x the provider's published list price, with cache reads at the hit
rate and cache writes at the write rate, Gemini thinking tokens billed as output, and dated prices
(Gemini 3.6 Flash steps up on 2027-01-01). Estimates are marked "≈" and never mixed into reported
charges. Update the price table when a model or price changes.
