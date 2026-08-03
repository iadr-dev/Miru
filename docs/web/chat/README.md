# 對話 / Chat (`/chat`)

The companion surface — the product's soul. Parent nav: [../README.md](../README.md) §3.1.
Streaming + interaction contracts: [../design-system.md](../design-system.md) §7.3 + backend
[modules/07](../../backend/modules/07-conversation-streaming.md).

## Layout
- **Center column** = the conversation over SSE. The NL **`activity` narration is the primary line**
  ("正在查 台積電 近 20 日籌碼…"); raw `thinking.delta`/tool internals collapse behind **顯示過程**.
- `rich_card`s (score gauge / stock card / `ChartWithDataSheet`) render **inline** in the thread.
- `caveat` badge pinned on every analytical answer; `follow_up` chips (pill buttons) below.
- **`tool.locked` → inline 馬賽克 teaser + 升級 CTA**; the turn proceeds around it.
- **Parallel sub-agent fan-out** → side-by-side lanes on wide canvas ([design-system.md](../design-system.md) §7.3).
- **Sisi companion** in its own corner region (§7.10) — reacts, never in the analysis area.

## Composer (adapts Kestrel `ChatComposer`)
Auto-grow textarea + **voice mic** (**Free** STT — `/voice/transcribe` → **confirm-before-send**, never
auto-fires a turn; quota-bounded like text). The send button **morphs**: idle-empty → voice-bars glyph, has-input → ➤ send,
streaming → ■ stop (`Esc`). Around it:
- **`+` feature menu** — attach files / screenshot · **web_search** toggle · **deep_research** (Pro,
  🔒 → routes to pricing if unentitled) · **plan** mode. Enabled toggles show as inline chips
  ([../../backend/api-reference.md](../../backend/api-reference.md) `/agent/query` body flags
  `web_search`/`research`/`plan` — all accepted per-turn; `research` is Pro).
- **`/` slash skill menu** — typing a bare `/query` opens a skill picker (analysis playbooks,
  [modules/11](../../backend/modules/11-orchestration.md)); selecting frames the prompt, the classifier
  routes. Optional alias to NL classification, not the primary trigger.
- **model selector** (Auto + per-provider list) + per-thread **personality pin**.
- Steering: sending mid-run queues a steering message (no cancel). `⌘/Ctrl+Enter` send · `↑` on empty
  = edit last (branch) · `Esc` cancel/stop. A non-removable **disclaimer line** sits under the composer.

## ContextBar + usage (after the last answer)
A slim **ContextBar** shows **context-window % used** + a **compaction** indicator (when the runtime
summarizes, [modules/07](../../backend/modules/07-conversation-streaming.md) §compaction) + optional
**session cost**; a **daily-AI-usage badge** ("今日 2/3 · 無限制 for BYOK",
[modules/12](../../backend/modules/12-multi-user-tiers.md)) sits above the composer. All descriptive,
non-trading.

## Thread list (sub-nav / left)
sort/star/pin/archive/search/tags ([modules/13](../../backend/modules/13-chat-interaction-features.md) §2);
新對話; per-thread **model + personality pin** ([modules/23](../../backend/modules/23-agent-personality.md)).

## Endpoints
`POST /agent/query` (SSE) · `GET /threads/{id}/stream` (resume, `Last-Event-ID`) · `/threads/*` ·
`/threads/{id}/{steer,switch-branch}` · `/voice/*`.

## Visual design
Tokens/radius/spacing from [../design-system.md](../design-system.md) (`VISUAL_DENSITY:6`, card 16px,
pill 999px, section gap 24px). Streaming/companion look = §7.3 + §7.10.

- **Center column** ~720px max, centered on `--bg`. Message rows: user = right-aligned `--surface-2`
  bubble (radius 16px); assistant = full-width text on `--bg`, no bubble (reads as a document).
- **Streaming.** `activity` narration is a **live line** (Sisi avatar subtly pulsing) — tokens
  fade+rise 4px (`.stream-chunk`, `stream-fade-in`); reduced-motion → instant. `顯示過程` = a proper
  disclosure (chevron, `--text-muted`) collapsing tool/thinking internals.
- **Inline `rich_card`s** (score gauge, `StockRow`, `ChartWithDataSheet`) render as `--surface` cards
  (`1px --border`, radius 16px, pad 20px) in the thread flow — same visual as canvas cards.
- **`caveat` badge** = pinned pill (`--warn`-tinted, ⚠ + text) on every analytical answer; **`follow_up`
  chips** = pill buttons (999px, `--surface-2`, hover `--accent` hairline) below the answer.
- **`tool.locked` → inline 馬賽克** teaser (backdrop-blur + diagonal texture + lock chip + 升級 CTA);
  the turn proceeds around it. **Parallel fan-out** = side-by-side lanes (each a bordered `--surface`
  column). **Sisi** in a dedicated corner region — visually separated from the analysis area.
- **Composer** (sticky bottom): `--surface` rounded input (radius 16px, `1px --border`, focus-visible
  `--accent` ring), send + voice-mic icon buttons (voice-input Free; mic hover → `.voice-bar` wave; TTS
  read-aloud is Premium). Steering:
  mid-run send queues (no cancel), shown as a pending chip.
- **Thread list (left).** `divide-y --border` rows, active = `--accent-weak` pill; star/pin/tag as
  small `--text-muted` glyphs. States: no chats → prompt-starter chips; loading → shimmer rows.
- 紅漲綠跌 + ▲▼ + tabular-nums inside every inline card; `aria-live=polite` on the answer only.

## Governance
Every answer non-trading + caveated; personality/pet style tone only (guardrail-subordinate).
Untrusted fetched/web/PTT content = data not instructions (prompt-injection defense).
