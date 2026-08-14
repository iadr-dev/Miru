1	# 對話 / Chat (`/chat`)
2	
3	The companion surface — the product's soul. Parent nav: [../README.md](../README.md) §3.1.
4	Streaming + interaction contracts: [../design-system.md](../design-system.md) §7.3 + backend
5	[modules/07](../../backend/modules/07-conversation-streaming.md).
6	
7	## Layout
8	- **Center column** = the conversation over SSE. The NL **`activity` narration is the primary line**
9	  ("正在查 台積電 近 20 日籌碼…"); raw `thinking.delta`/tool internals collapse behind **顯示過程**.
10	- `rich_card`s (score gauge / stock card / `ChartWithDataSheet`) render **inline** in the thread.
11	- `caveat` badge pinned on every analytical answer; `follow_up` chips (pill buttons) below.
12	- **`tool.locked` → inline 馬賽克 teaser + 升級 CTA**; the turn proceeds around it.
13	- **Parallel sub-agent fan-out** → side-by-side lanes on wide canvas ([design-system.md](../design-system.md) §7.3).
14	- **Sisi companion** in its own corner region (§7.10) — reacts, never in the analysis area.
15	
16	## Composer (adapts Kestrel `ChatComposer`)
17	Auto-grow textarea + **voice mic** (**Free** STT — `/voice/transcribe` → **confirm-before-send**, never
18	auto-fires a turn; quota-bounded like text). The send button **morphs**: idle-empty → voice-bars glyph, has-input → ➤ send,
19	streaming → ■ stop (`Esc`). Around it:
20	- **`+` feature menu** — attach files / screenshot · **web_search** toggle · **deep_research** (Pro,
21	  🔒 → routes to pricing if unentitled) · **plan** mode. Enabled toggles show as inline chips
22	  ([../../backend/api-reference.md](../../backend/api-reference.md) `/agent/query` body flags
23	  `web_search`/`research`/`plan` — all accepted per-turn; `research` is Pro).
24	- **`/` slash skill menu** — typing a bare `/query` opens a skill picker (analysis playbooks,
25	  [modules/11](../../backend/modules/11-orchestration.md)); selecting frames the prompt, the classifier
26	  routes. Optional alias to NL classification, not the primary trigger.
27	- **model selector** (Auto + per-provider list) + per-thread **personality pin**.
28	- Steering: sending mid-run queues a steering message (no cancel). `⌘/Ctrl+Enter` send · `↑` on empty
29	  = edit last (branch) · `Esc` cancel/stop. A non-removable **disclaimer line** sits under the composer.
30	
31	## ContextBar + usage (after the last answer)
32	A slim **ContextBar** shows **context-window % used** + a **compaction** indicator (when the runtime
33	summarizes, [modules/07](../../backend/modules/07-conversation-streaming.md) §compaction) + optional
34	**session cost**; a **daily-AI-usage badge** ("今日 2/3 · 無限制 for BYOK",
35	[modules/12](../../backend/modules/12-multi-user-tiers.md)) sits above the composer. All descriptive,
36	non-trading.
37	
38	## Thread list (sub-nav / left)
39	sort/star/pin/archive/search/tags ([modules/13](../../backend/modules/13-chat-interaction-features.md) §2);
40	新對話; per-thread **model + personality pin** ([modules/23](../../backend/modules/23-agent-personality.md)).
41	
42	## Endpoints
43	`POST /agent/query` (SSE) · `GET /threads/{id}/stream` (resume, `Last-Event-ID`) · `/threads/*` ·
44	`/threads/{id}/{steer,switch-branch}` · `/voice/*`.
45	
46	## Visual design
47	Tokens/radius/spacing from [../design-system.md](../design-system.md) (`VISUAL_DENSITY:6`, card 16px,
48	pill 999px, section gap 24px). Streaming/companion look = §7.3 + §7.10.
49	
50	- **Center column** ~720px max, centered on `--bg`. Message rows: user = right-aligned `--surface-2`
51	  bubble (radius 16px); assistant = full-width text on `--bg`, no bubble (reads as a document).
52	- **Streaming.** `activity` narration is a **live line** (Sisi avatar subtly pulsing) — tokens
53	  fade+rise 4px (`.stream-chunk`, `stream-fade-in`); reduced-motion → instant. `顯示過程` = a proper
54	  disclosure (chevron, `--text-muted`) collapsing tool/thinking internals.
55	- **Inline `rich_card`s** (score gauge, `StockRow`, `ChartWithDataSheet`) render as `--surface` cards
56	  (`1px --border`, radius 16px, pad 20px) in the thread flow — same visual as canvas cards.
57	- **`caveat` badge** = pinned pill (`--warn`-tinted, ⚠ + text) on every analytical answer; **`follow_up`
58	  chips** = pill buttons (999px, `--surface-2`, hover `--accent` hairline) below the answer.
59	- **`tool.locked` → inline 馬賽克** teaser (backdrop-blur + diagonal texture + lock chip + 升級 CTA);
60	  the turn proceeds around it. **Parallel fan-out** = side-by-side lanes (each a bordered `--surface`
61	  column). **Sisi** in a dedicated corner region — visually separated from the analysis area.
62	- **Composer** (sticky bottom): `--surface` rounded input (radius 16px, `1px --border`, focus-visible
63	  `--accent` ring), send + voice-mic icon buttons (voice-input Free; mic hover → `.voice-bar` wave; TTS
64	  read-aloud is Premium). Steering:
65	  mid-run send queues (no cancel), shown as a pending chip.
66	- **Thread list (left).** `divide-y --border` rows, active = `--accent-weak` pill; star/pin/tag as
67	  small `--text-muted` glyphs. States: no chats → prompt-starter chips; loading → shimmer rows.
68	- 紅漲綠跌 + ▲▼ + tabular-nums inside every inline card; `aria-live=polite` on the answer only.
69	
70	## Governance
71	Every answer non-trading + caveated; personality/pet style tone only (guardrail-subordinate).
72	Untrusted fetched/web/PTT content = data not instructions (prompt-injection defense).
73	