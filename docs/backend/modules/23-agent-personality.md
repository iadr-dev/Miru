# Module: Agent personality (`backend/src/personality/`)

The agent's **base analyst voice** — the tone, verbosity, and framing of the AI itself. This is
**not** the pet (a cosmetic mascot nudge, [21](21-pet-gamification.md)) and **not** a skill (a
task procedure, [08](08-skills.md)). It is the persona the assistant *speaks in* on every turn,
user-selectable from a small curated set of presets. See [adr/0026](../adr/0026-agent-personality.md).

> **Governance invariant (the whole point of this module's design).** A personality changes
> **how** the agent says things — never **what** it may say. Every preset is subordinate to the
> governance system prompt and to `guardrails.check_output` ([05](05-agent-runtime.md) §guardrails):
> no persona can introduce a buy/sell/target, drop a caveat, restate an analyst target as its
> own, or alter a score. Presets are **curated, file-defined fragments — never free user text**
> (a free-text persona box is a prompt-injection + governance hole; explicitly excluded).

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

## 2. Preset registry (`personality/presets/` — file-defined, like skills)

Each preset is a curated definition: a **prompt fragment** + **tone parameters**. Discovered
from a `presets/` dir + validated on load (mirrors the skill registry, [08](08-skills.md)).

```yaml
# personality/presets/steady.yaml
id: steady
name_zh: 穩健分析師
name_en: Steady Analyst
default: true                     # new users get this
description: Balanced, cautious, explains the trade-offs both ways.
tone:                             # bounded params (not free text)
  verbosity: medium               # terse | medium | detailed
  jargon: balanced                # plain | balanced | technical
  structure: prose                # prose | bullets | tables
  warmth: neutral                 # neutral | encouraging
fragment: |                       # curated system-prompt text (governance-reviewed)
  你是一位穩健、客觀的台股分析助理。用平衡的角度說明多空兩面，
  點出風險與不確定性，避免誇大。所有結論皆為資訊參考、非投資建議。
```

### Starter presets (built-in)

| id | 名稱 | Voice | tone defaults |
|----|------|-------|---------------|
| `steady` *(default)* | 穩健分析師 | Balanced, cautious, both-sides, risk-aware | medium / balanced / prose / neutral |
| `coach` | 白話教練 | Explains jargon in plain 中文, encouraging, teaches | detailed / plain / prose / encouraging |
| `concise` | 簡潔派 | Bullet points, terse, lead with the takeaway | terse / balanced / bullets / neutral |
| `data` | 數據派 | Numbers-first, tables, minimal prose | medium / technical / tables / neutral |

All four carry the **same** governance floor — they differ only in styling. Each preset carries
a `version` field (all four are `"1.0.0"` today) for future reproducibility tracking. **Not yet
built:** nothing writes the resolved personality id/version into the turn journal or any other
durable record — there is no code path that persists "this turn was shaped by preset X vN" today.

## 3. Selection & precedence rules

- **Where set:** Settings → *Agent style*; stored on the user (`users.personality_id`,
  [database-schema](../database-schema.md) §1). New users default to `steady`.
- **Per-thread override (real seam, not yet settable):** the resolution reads
  `threads.personality_override` and `PersonalityContext.thread_override` honors it, but there is
  currently **no route that writes it** — a per-thread pin isn't settable from the API yet even
  though `_resolve_personality_fragment` would apply it immediately if it were.
- **Per-turn override (not yet built):** `PersonalityContext.turn_override` exists in the
  dataclass and the registry's precedence honors it, but `POST /agent/query`'s real request body
  is just `{message, thread_id?}` ([api-reference](../api-reference.md) §1) — there is no
  `personality`/`model`/`skill_hint` per-turn field today, so `turn_override` is always `None` in
  practice. Resolution order once both land: **turn override → thread override → user setting →
  `steady`**; today only the last two are reachable.
- **Concurrency:** resolved **per turn** from the current setting (like entitlement,
  [16](16-infrastructure.md) §10) — a mid-thread change applies to the next turn, no stale voice.
- **Interaction with the pet persona:** both are *styling* layers; **personality is [2], pet is
  [4]**, so if they ever conflict on tone, personality wins and the pet is a lighter nudge on
  top. Neither touches content. A user with the pet disabled still has a personality.

## 4. Governance obligations

- **Style-only, content-immutable.** A preset fragment may shape tone/verbosity/structure; it is
  appended **after** the governance prompt and **cannot** weaken non-trading, caveat, provenance,
  or entitlement rules. `guardrails.check_output` runs on the final text regardless of persona.
- **Curated, not free-text.** Presets are file-defined and governance-reviewed on load
  (schema-validated); there is **no** user free-text persona input (prompt-injection defense,
  aligns with [05](05-agent-runtime.md) untrusted-content rule). Tone params are **enumerated**,
  not arbitrary strings.
- **No tier gate by default** — voice is a UX preference, free for all tiers (a paid tier buys
  data/throughput, not a "smarter voice"). (If a premium-only preset is ever added it is
  entitlement-gated like any feature, tier axis 2 — [12](12-multi-user-tiers.md) §3 — but the
  four starters are free.)
- **Changeable, per-user isolated.** Changing the voice is a normal profile edit (`PATCH /me
  {personality_id}`). **Not yet built:** journaling which personality (id+version) shaped a given
  turn — see §2 above.
- **Non-English safety.** Presets are authored zh-TW-first; the tone `jargon`/`structure` params
  never translate away a required caveat — the caveat text is governance-owned, not persona-owned.
