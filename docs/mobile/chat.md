# 對話 / Chat (tab 3 — center, accented — the soul)

The product's heart: a full-screen streaming conversation, voice-friendly, the primary interaction
for every persona. The **raised `--accent` center tab** (§6.1) is thumb-home. Parent index:
[README.md](README.md) §2/§3. UX rationale: [ux-foundation.md](ux-foundation.md). Streaming +
interaction contracts: [interaction-spec.md](interaction-spec.md). Web parity:
[../web/chat/README.md](../web/chat/README.md) + [../web/design-system.md](../web/design-system.md)
§7.3 (streaming) + §7.10 (Sisi).

> Non-negotiables: every analytical answer carries a **`caveat` badge** + non-trading footer
> (non-removable) · 紅漲綠跌 + ▲▼ + tabular-nums inside every inline card · 馬賽克 on `tool.locked` ·
> `--accent` chrome-only · dark-first · zh-TW first · **TTS read-aloud must speak the caveat** · no
> 明牌/飆股/目標價/買賣 copy.

## Components → backend

| Element | Component | Endpoint |
|---------|-----------|----------|
| Streaming turn | `activity` narration line + token stream | `POST /agent/query` (SSE) ([07](../backend/modules/07-conversation-streaming.md)) |
| Resume / reconnect | `Last-Event-ID` replay | `GET /threads/{id}/stream` |
| Inline rich content | `rich_card` (score gauge / `StockRow` / `ChartWithDataSheet`) | rendered from envelope |
| Caveat badge | `<Caveat>` pinned chip | envelope `caveat` |
| Follow-ups | `follow_up` chips | envelope |
| Locked step | inline 馬賽克 teaser + 升級 CTA | `tool.locked` ([12](../backend/modules/12-multi-user-tiers.md)) |
| Voice | mic → transcript, confirm-before-send | `/voice/transcribe` (**Free** STT, quota-bounded; TTS read-aloud Premium — [13](../backend/modules/13-chat-interaction-features.md)) |
| Steering | queue mid-run message | `/threads/{id}/steer` |
| Pet ambient | `Sisi` companion region | `pet.reaction` ([21](../backend/modules/21-pet-gamification.md)) |

## Layout (full-screen conversation)

```
┌───────────────────────────────────────┐
│ ‹ 台積電近期籌碼   ⋯ (model·語氣·分享) │ ← thread top bar (glass, content scrolls behind)
├───────────────────────────────────────┤
│                       台積電最近籌碼? ▐ │ ← user bubble (right, --surface-2)
│ 🐦 正在查 台積電 近20日籌碼…            │ ← activity narration (primary line, Sisi pulse)
│    顯示過程 ▾                           │ ← disclosure: raw thinking/tool internals collapsed
│ 外資連 8 買，投信同步…（tokens fade+rise) │ ← assistant answer (full-width, no bubble)
│ ┌ 多空 [偏多 72] ◜◝arc◜◝  動能▇▇籌碼▇ ┐ │ ← inline rich_card (score gauge)
│ └────────────────────────────────────┘ │
│ ┌ 🔒 分點主力進出 ░░▓▓░ 升級 Premium ┐ │ ← tool.locked → 馬賽克 teaser
│ └────────────────────────────────────┘ │
│ ⚠ 資料整理，非投資建議 · 截至 08/01    │ ← caveat badge (pinned, non-removable)
│ [近一月走勢?] [同業比較?] [設定提醒]    │ ← follow_up chips
├───────────────────────────────────────┤
│ 🐦 Sisi (idle breathing)  ← companion   │ ← ambient region, separated from analysis
├───────────────────────────────────────┤
│ [ 問問題…                    ] 🎤  ➤    │ ← composer: text + voice mic + send
└───────────────────────────────────────┘
        ⌂  ⌁  ◉Chat  ▽  ⚙   (Chat = raised accent center)
     Home Stock Chat Screener Settings
```

## Visual design

Tokens from [../web/design-system.md](../web/design-system.md); streaming look = §7.3, Sisi = §7.10.
**Dark-first, full-bleed single column, 2026 mobile bar.**

- **Frame.** Full `100dvh` on `--bg`. Thread **glass top bar** (back · title · `⋯` overflow →
  model/語氣 personality pin/分享/archive) — content scrolls behind the blur. The message list is the
  scroll region; composer is **docked bottom**, above the floating island via safe-area padding.
- **Message rows.** User = right-aligned `--surface-2` bubble (radius 16px, ≤85% width); assistant =
  **full-width text on `--bg`, no bubble** (reads as a document, better for long CJK answers at
  16px/1.7). Generous line-height and thumb-zone bottom padding.
- **Streaming.** `activity` narration is the **primary live line** ("正在查 台積電 近 20 日籌碼…") with
  the Sisi avatar subtly pulsing; answer tokens **fade + rise 4px** (`.stream-chunk`); reduced-motion
  → instant. **顯示過程** = a proper disclosure (chevron, `--text-muted`) collapsing raw
  `thinking.delta` / tool internals — reachable, not read aloud by default.
- **Parallel sub-agent fan-out → collapsed narration track.** The web's side-by-side lanes don't fit a
  phone: mobile renders fan-out as a **single stacked narration track** ("① 查籌碼 ✓ · ② 查技術 ⋯ · ③
  查新聞 ⋯") with per-lane status ticks, expandable under 顯示過程. ⚑ *tradeoff — see README.*
- **Inline `rich_card`s** (score gauge, `StockRow`, `ChartWithDataSheet`) render as `--surface` cards
  (`1px --border`, radius 16px, pad 20px) in the thread flow — same visual as canvas cards, full
  column width. Charts collapse to a single pane + 展開歷史 sheet (the mobile `ChartWithDataSheet`).
- **`caveat` badge** = pinned pill (`--warn`-tinted, ⚠ + text) on every analytical answer, **not
  removable**. **`follow_up` chips** = pill buttons (999px, `--surface-2`, hover/press `--accent`
  hairline, ≥44px) below the answer — the tap-forward loop that keeps the no-typing core intact.
- **`tool.locked` → inline 馬賽克** (backdrop-blur + diagonal texture + centered lock chip + one calm
  升級 CTA); the turn **proceeds around it**, never an interstitial.
- **Composer** (sticky bottom, glass `--surface`, adapts Kestrel `ChatComposer`): auto-grow input
  (radius 16px, `1px --border`, focus-visible `--accent` ring). The trailing button **morphs** — empty
  → 🎤 voice, has-input → ➤ send, streaming → ■ stop. A **`+` menu** (≥44px) holds attach (photo/
  screenshot) · **web_search** toggle · **deep_research** (Pro, 🔒) · **plan** — enabled ones show as
  inline chips; a **`/` slash** opens the skill picker (playbooks, optional alias to NL routing). Mic
  press → `.voice-bar` waveform while recording → transcript to a **confirm-before-send** editable
  field (never auto-fires — a 2330→2380 mis-hear must be catchable; low-confidence tokens highlighted;
  ambiguous symbol → `ask_user`). **Per-thread draft persists** across app backgrounding
  ([13](../backend/modules/13-chat-interaction-features.md) §3). A non-removable **disclaimer line**
  sits under the composer.
- **ContextBar + usage** (after the last answer, compact for mobile): context-window **% used** +
  **compaction** indicator ([07](../backend/modules/07-conversation-streaming.md) §compaction) +
  optional session cost; a **daily-AI-usage badge** ("今日 2/3 · 無限制 BYOK",
  [12](../backend/modules/12-multi-user-tiers.md)) above the composer — descriptive, non-trading.
- **Steering** — sending mid-run **queues** a steering message (no cancel), shown as a pending chip
  above the composer.
- **Sisi companion region** — a dedicated strip **between the answer and the composer**, visually
  separated so mascot warmth is never mistaken for advice; idle breathing, reacts to `pet.reaction`
  (thinking/done/watched-symbol-moved), celebrates `pet.leveled`. Reduced-motion → static.

### States
- **Loading = narration**, not a blank spinner ("Miru 正在看盤面…"); shimmer placeholder for a
  pending inline card.
- **Empty** (no thread) → prompt-starter chips (the beginner scaffold) + a warm Sisi greeting; new
  users arrive here post-onboarding (§5 README).
- **Error** — a dropped SSE connection is **invisible** (`Last-Event-ID` resume — critical on flaky
  mobile networks); a genuine failure → inline calm retry chip, the partial answer + caveat preserved.
- **Reduced-motion** → token stream instant, Sisi/gauge static, no waveform animation.

## A11y
`aria-live=polite` on the **answer only** (not every thinking token); 顯示過程 is a real disclosure;
collapsed thinking reachable but not announced. TTS read-aloud **must speak the caveat**
([13](../backend/modules/13-chat-interaction-features.md) §voice, governance §8). Voice
confirm-before-send mandatory. Focus-visible 2px `--accent`; ≥44px targets.

## Governance
Every answer non-trading + caveated (non-removable badge + footer); personality/pet layers style
**tone only** — never introduce trading language or drop a caveat. Untrusted fetched/web/PTT content
= data, not instructions (prompt-injection defense). No 明牌/飆股/目標價/買賣 copy
([../backend/governance.md](../backend/governance.md) §1, [../brand.md](../brand.md) §5).
