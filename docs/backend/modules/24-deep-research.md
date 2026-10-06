# 24 — Deep research (Pro)

A budgeted, cited, multi-step web investigation that runs **inside a chat turn** and streams its
progress. Implements the `deep_research` feature promised in [ADR-0014](../adr/0014-web-search.md)
and gated in [12](12-multi-user-tiers.md). Code: `backend/src/research/`, wired by
`api/deep_research_turn.py`.

## 1. Shape of a run

**Code runs the loop; the model is only asked to plan, to judge "is this enough?", and to write.**

```
plan ─▶ ┌ search ─▶ read ─▶ review ┐ × ≤ 3 rounds ─▶ write ─▶ render
        └──────────◀───────────────┘  (review asks for new queries, or says "sufficient")
```

| Ceiling (`ResearchBudget`) | Default |
|---|---|
| rounds | 3 |
| searches | 8 |
| pages read | 12 (≤ 5 per round, 3 fetched concurrently) |
| wall-clock | 120 s (the final write always gets ≥ 25 s) |
| text per source shown to the model | 6,000 chars |

Hitting any ceiling (or the model never saying "sufficient") ends the research and **still writes
a report from what was read**; the report says why it stopped (`stopped_because`). Search / fetch /
review failures are tolerated and logged; a plan or write failure raises `ResearchFailedError`.

## 2. Governance (what makes it safe to ship)

- **Citations are structural.** The model returns *claims* (`text` + `source_ids`). `build_report`
  keeps only ids that exist in the source ledger **and were actually read**; a claim left with none
  is dropped (and counted in `dropped_claims`). Nothing uncited reaches the reader
  ([ADR-0014](../adr/0014-web-search.md): every web-derived claim is cited).
- **Fetched text is data, never instructions.** Pages are wrapped in `<source id url title>` blocks,
  control characters are stripped, `<source` / `</source` inside a page are defused, pages that
  contain instruction-like phrases (EN + 中文) are excluded entirely, and both system prompts carry
  the untrusted-content notice.
- **No trading output.** Claim text, headings, title and open questions pass `check_output` claim by
  claim, and the finished markdown is scanned again and stamped with the standard caveat.
- **SSRF.** A URL must survive `schemas/urlsafety.canonical_url` (public http/https, no credentials,
  no internal/legacy-IPv4 hosts) to enter the ledger at all. The fetch (`sources/web_search.py::
  WebSearchAdapter.fetch_page`, the only network code) additionally resolves DNS itself and rejects
  the request if **any** answer is non-public, connects to the checked IP (TLS verifies the real
  hostname via SNI), follows redirects by hand re-validating **every hop** (max 4), caps the body at
  1 MB and reads only `text/html`, `application/xhtml+xml`, `text/plain`.

## 3. Quota and tier

`POST /agent/query` (and `regenerate` / `edit`) accept `deep_research: true`. The pre-turn gate
(`enforce_pre_turn_gate(..., deep_research=True)`) runs: rate limit → `feature_allowed(user,
"deep_research")` (else `403 feature_not_entitled`) → reserve one of **5 reports per UTC day**
(`usage_counters`, counter `deep_research`; else `429 deep_research_limit_reached`) → the normal
chat reservation. BYOK does **not** lift the report limit. The claim is **refunded** when the run
fails or yields no report (nothing citable was found); a user cancel is not refunded.

## 4. Streaming and persistence

Progress reuses existing frames: each stage (planning, `searching: <query>`, `reading N pages`,
reviewing, writing) is an `agent.spawned` lane closed by `agent.done`; the consulted pages are one
`web_search.result` frame. The answer is an ordinary `OrchestratorResult` (markdown with `[n]`
citations, a sources list, an "as of" date and an early-stop note), so persistence, usage
recording (`UsageAccumulator` fed by every model call) and the final `message` frame are unchanged.

## 5. Not built yet

Per-user concurrency cap (one report at a time), regenerate/edit resending the `deep_research`
flag from the client, report export, and reading PDFs.
