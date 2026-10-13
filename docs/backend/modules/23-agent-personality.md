# Module: Agent personality (`backend/src/personality/`)

The agent's **base analyst voice** — the tone, verbosity, and framing of the AI itself. This is
**not** the pet (a cosmetic mascot nudge, [21](21-pet-gamification.md)) and **not** a skill (a
task procedure, [08](08-skills.md)). It is the persona the assistant *speaks in* on every turn,
user-selectable from a small curated set of presets.

> **Governance invariant (the whole point of this module's design).** A personality changes
> **how** the agent says things — never **what** it may say. Every preset is subordinate to the
> governance system prompt and to `guardrails.check_output` ([05](05-agent-runtime.md) §guardrails):
> no persona can introduce a buy/sell/target, drop a caveat, restate an analyst target as its
> own, or alter a score. Presets are **curated, file-defined fragments — never free user text**
> (a free-text persona box is a prompt-injection hole and could bypass the governance prompt; explicitly excluded).

---

## 1. The system-prompt layering (where personality sits)

The assistant's system prompt is assembled in a **fixed precedence** — lower layers can only
*style*, never override, higher ones:

```
[1] Governance system prompt  — non-trading, caveats, provenance      ← immovable base
[2] Agent personality          — voice/tone/verbosity/jargon (THIS module)
[3] Skill body                 — task procedure, if a skill is active ([08])
[4] Pet persona hint           — cosmetic warmth nudge ([21])
                     ↓ all subordinate to ↓
     guardrails.check_output (pre_output)  — final non-trading/caveat backstop ([05])
```

Personality is injected via the `PromptLayers` the caller constructs `AgentLoop` with
(`agent/loop.py`'s `AgentLoop.run_turn` builds the turn's one system message from
`assemble_system_prompt(self._prompt_layers)` at the top of `run_turn`, before the Step loop
starts) — a fixed prompt segment resolved once per turn from the user's setting, **not**
re-derived per Step. `_transform_context` (the `transform_context` seam, [05](05-agent-runtime.md)
§loop) is a separate concern — it only compacts the message list between Steps and never touches
the system message.

**Resolution is real, end-to-end, on every turn.** `orchestrator/orchestrator.py` holds a
`PersonalityRegistry` (built via `orchestrator/composition.py`, shared app-wide through
`AppState.personality_registry` so it is loaded once, not per turn) and a `PersonalityContext`
dataclass (`turn_override`/`thread_override`/`user_preference` — the same three-level precedence
as §3 below). `_resolve_personality_fragment` resolves the context against the registry once per
turn, and every real turn path populates `PromptLayers.personality` from the result: `run`
(`none`/`single`/`subagent`/`team`), `run_skill` (scheduled runs), `_run_none`, `_run_single`,
and each fan-out lane via `_skill_prompt_layers`. The callers that build `PersonalityContext`
already hold what they need — `api/routers/agent.py` builds it from the authenticated `User` and
the loaded `Thread` (`thread_override=thread.personality_override`,
`user_preference=user.personality_id`); `scheduler/worker.py` does the analogous thing for a
scheduled job, which has no thread so only `user_preference` is set. `turn_override` has no
producer yet (no API surface lets a single turn pin a one-off preset) — the field stays on
`PersonalityContext` so the full precedence is honored the moment one lands, with no further
orchestrator change required. Regardless of which personality fragment is resolved,
`guardrails.check_output` still scans the final output on every path — personality is
appended **after** the governance layer and never bypasses this backstop (see the governance
invariant at the top of this doc, and [05](05-agent-runtime.md) §guardrails).

## 1a. Audit notes (live-tested)

- **Contract.** `GET /personality/presets` returns only what the picker needs (`id`, `name_zh`,
  `name_en`, `description`, `default`, `version`) - the curated prompt `fragment` stays
  on the server. `PATCH /me {personality_id}` refuses an unknown id with 422; a stale stored id
  falls through to the default at resolve time.
- **Fragments are written in English and carry the language clause themselves** ("answer in the
  language of the user's most recent message"). Chinese fragments pulled an English question into a
  Chinese answer (seen live with the coach preset, overriding the governance prompt's same-language rule, `agent/prompts/governance.py` rule 8). A test pins the
  clause on every preset. Remaining limitation: `gpt-4o-mini` still drifts to Chinese for English
  questions on this Taiwan-themed prompt regardless of preset (Gemini does not), so it is a model
  trait, not a tone-setting bug.
- **Effect size.** Presets are soft: with `gpt-4o-mini`, `data` produces a table for comparisons
  but not for conceptual questions, `concise` is bullets for English and looser for Chinese, `coach`
  adds a takeaway + example. A preset never overrides what the user asks for ("in one sentence").
- **Visible and recorded.** Each turn publishes `personality.used` `{personality}` (the resolved
  preset id) right after `turn.start`, and the id is stored in `turns.usage.personality`, so the web
  shows 「語氣：…」 under the answer live and after reload. The preset version is not journaled.
- **Taiwan wording.** `agent/prompts/governance.py` rule 14 pins market terms (本益比, 殖利率, 融資/融券, 外資/投信/自營商,
  漲跌幅, 成交量, 市值), independent of the preset.

## 2. Preset registry (`personality/presets/` — file-defined, like skills)

Each preset is a curated definition: a **prompt fragment** + **tone parameters**. Discovered
from a `presets/` dir + validated on load (mirrors the skill registry, [08](08-skills.md)).

```yaml
# personality/presets/steady.yaml
id: steady
name_zh: 穩健分析師
name_en: Steady Analyst
default: true                     # new users get this
version: "1.0.0"
description: Balanced, cautious, explains the trade-offs both ways.
tone:                             # bounded params (not free text)
  verbosity: medium               # terse | medium | detailed
  jargon: balanced                # plain | balanced | technical
  structure: prose                # prose | bullets | tables
  warmth: neutral                 # neutral | encouraging
fragment: |                       # curated system-prompt text (written in English, carries its own
  Voice: a steady, objective ...  # language clause; see backend/src/personality/presets/steady.yaml)
```

### Starter presets (built-in)

| id | 名稱 | Voice | tone defaults |
|----|------|-------|---------------|
| `steady` *(default)* | 穩健分析師 | Balanced, cautious, both-sides, risk-aware | medium / balanced / prose / neutral |
| `coach` | 白話教練 | Explains jargon in plain 中文, encouraging, teaches | detailed / plain / prose / encouraging |
| `concise` | 簡潔派 | Bullet points, terse, lead with the takeaway | terse / balanced / bullets / neutral |
| `data` | 數據派 | Numbers-first, tables, minimal prose | medium / technical / tables / neutral |

All four carry the **same** governance floor — they differ only in styling. Each preset carries
a `version` field (all four are `"1.0.0"` today). The resolved preset **id** is recorded in
`turns.usage.personality`; the **version** is not persisted anywhere.

## 3. Selection & precedence rules

- **Where set:** Settings → *Agent style*; stored on the user (`users.personality_id`,
  the `personality_id` column on the user model in `backend/src/db/models/accounts.py`). New users default to `steady`.
- **Per-chat override (settable):** `PATCH /threads/{id} {personality_override}` pins a preset for
  that chat; the id `"default"` clears the pin so the chat follows the account again; an unknown id
  is `422 unknown_personality`. `POST /agent/query` accepts the same `personality` field (applied
  and persisted as the thread override before the turn, so a pick made before the first message
  still styles it). The composer has a 語氣 menu next to the model menu.
- **Per-turn override (not built):** `PersonalityContext.turn_override` is honored by the registry but
  has no producer. Resolution order: **turn override → thread override → user setting → `steady`**;
  in practice the last three are reachable.
- **Concurrency:** resolved **per turn** from the current setting (like entitlement,
  [16](16-infrastructure.md) §10) — a mid-thread change applies to the next turn, no stale voice.
- **Interaction with the pet persona:** both are *styling* layers; **personality is [2], pet is
  [4]**, so if they ever conflict on tone, personality wins and the pet is a lighter nudge on
  top. Neither touches content. A user with the pet disabled still has a personality.

## 4. Obligations

- **Style-only, content-immutable.** A preset fragment may shape tone/verbosity/structure; it is
  appended **after** the governance prompt and **cannot** weaken non-trading, caveat, provenance,
  or entitlement rules. `guardrails.check_output` runs on the final text regardless of persona.
- **Curated, not free-text.** Presets are file-defined and reviewed in code
  (schema-validated on load); there is **no** user free-text persona input (prompt-injection defense,
  aligns with [05](05-agent-runtime.md) untrusted-content rule). Tone params are **enumerated**,
  not arbitrary strings.
- **No tier gate by default** — voice is a UX preference, free for all tiers (a paid tier buys
  data/throughput, not a "smarter voice"). (If a premium-only preset is ever added it is
  entitlement-gated like any feature, tier axis 2 — [12](12-multi-user-tiers.md) §3 — but the
  four starters are free.)
- **Changeable, per-user isolated.** Changing the voice is a normal profile edit (`PATCH /me
  {personality_id}`). The preset id that shaped a turn is recorded in `turns.usage.personality`
  (§1a); the version is not.
- **Non-English safety.** Presets are authored zh-TW-first; the tone `jargon`/`structure` params
  never translate away a required caveat — the caveat text is owned by the governance prompt, not the persona.
