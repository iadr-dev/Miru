# Miru — Mobile UX Research Foundation

The **research/UX foundation** for the mobile client: who it's for, the situations they use it in,
the core task loops, and the mobile-specific principles the per-screen and interaction specs build
to. This is the *why* behind the mobile spec — it **deepens and justifies**
[README.md](README.md) (IA, personas, divergence), it never contradicts it. No code; blueprint stage.

> **Reading this as:** a **chat-first companion + glanceable monitor** for TW retail investors on a
> phone. The mobile-only **存股 holder** (Sufen) and finance-curious **beginner** (Xiaoyu) live here;
> the active trader (Kai) uses mobile as a **monitoring/alert-response mode**, with the workbench on
> web. Bilingual (zh-TW first), non-trading, dark-first. Governance and tokens are **shared with web**
> — this doc references, never redefines. Designed to the **2026 mobile UX bar** (glass nav island,
> gesture-first, chat-first, notification-driven re-entry) — but anti-slop: every pattern earns its
> place for this audience and never overrides the non-negotiables (§6.8).

**Companion docs (this set):** [README.md](README.md) (IA + content ranking), the mobile
[interaction-spec.md](interaction-spec.md) (motion/state/SSE contracts — being written in parallel),
and the per-screen docs [home.md](home.md), [watchlist.md](watchlist.md), [chat.md](chat.md),
[calendar.md](calendar.md), [settings.md](settings.md), [stock-detail.md](stock-detail.md),
[screener.md](screener.md). Shared foundations: the web app's tokens, visuals and motion non-negotiables,
[../brand.md](../brand.md), [backend/CLAUDE.md](../../backend/CLAUDE.md) §4.

---

## 1. Scope & design read

Mobile is **not the web workbench shrunk**. It is the surface for the two jobs a phone does best:

- **Glance** — orient in seconds without typing (存股 morning check, commute peek, alert tap-through).
- **Converse** — ask Miru in natural language / voice, get a streamed, caveated answer with follow-ups.

Everything deep (multi-panel Stock Detail, screener funnel tables, side-by-side agent lanes) stays
**desktop-primary** ([README §3](README.md)); mobile shows the
**compressed** form (section stack, card list, collapsed narration track). The mobile audience skews
toward people who **monitor and ask**, not people who screen and build.

---

## 2. Personas (deepened for mobile)

Grounded in the three named in [README §2](README.md), plus a notification-driven re-entry pattern
that mobile makes its own (⚑ §8). Each persona is framed by **mobile context**, **jobs-to-be-done
(JTBD)**, **what they open the app FOR**, **failure modes**, and **why a tab earns its slot**.

### 2.1 Sufen 素芬 — the 存股 dividend holder *(mobile-only, primary)*
| | |
|---|---|
| **Profile** | 45–60, long-hold ETF/blue-chip 存股; buys rarely, holds for yield; no desktop trading setup — the phone *is* the account. Low tolerance for typing, jargon, noise. |
| **Mobile context** | Morning kitchen-table glance; commute; evenings. One-handed. Wants "is anything happening to *my* holdings / *my* dividends," then closes the app. |
| **JTBD** | "Show me my holdings and what dividend/除權息 events are coming — without me typing anything." "Tell me if something big moved." |
| **Opens the app FOR** | 跑馬燈 + 大盤概況 + 我的自選 (glance), and **今日事件 / 行事曆** (the 存股 loop — [18](../backend/modules/18-calendar-events.md)). |
| **Failure modes** | Overwhelmed by a trader-dense dashboard; can't find dividend dates; scared off by 買賣/目標價 language (governance breach); mis-taps small targets. |
| **Why her surfaces** | **Home** (ambient, no-typing core) is her whole app — its 今日事件 block (→ full Calendar surface) is her #1 recurring view, and the 我的自選 glance block + top-bar ☆ cover holdings monitoring without a tab. Chat is occasional ("這次配息多少?"), often via voice. |

### 2.2 Xiaoyu 小語 — the finance-curious beginner *(mobile-only, primary)*
| | |
|---|---|
| **Profile** | 22–32, new to investing; learns by asking; intimidated by dense terminals and jargon. Mobile-native, chat-native (LINE/IG generation). |
| **Mobile context** | Bursty, curiosity-driven sessions anywhere; types short, often voice; abandons if the first answer is a wall of numbers. |
| **JTBD** | "Explain 台積電 to me like I'm new." "What does 除權息 mean?" "Is now scary or normal?" — wants **scaffolding**, not a blank prompt box. |
| **Opens the app FOR** | **Chat** (center tab) seeded by **快速提問 prompt chips** and `follow_up` chips; the **白話派/Coach** voice ([23](../backend/modules/23-agent-personality.md)). |
| **Failure modes** | Blank-composer paralysis; answer too dense (needs 白話 + progressive disclosure); loses the thread when the app is backgrounded mid-answer; mistakes explanation for a buy signal (caveat must land). |
| **Why her tabs** | **Chat** (centered, accented — her whole product) + **Home** (prompt-chip scaffold, ambient learning). Screener/Calendar rarely touched. |

### 2.3 Kai 凱 — the active trader, **monitoring mode** *(mobile = companion to web)*
| | |
|---|---|
| **Profile** | 30–45, trades actively; **web is the workbench** (multi-panel detail, screener, parallel lanes). On the phone he is *away from the desk*. |
| **Mobile context** | Between meetings, lunch, commute; reacts to a push; checks a live quote; fires a quick question; **defers deep analysis to the desk**. |
| **JTBD** | "React to this alert now." "Quick live quote on 2330." "Flag this to look at on the desktop later." **Not** "run a screener on my phone." |
| **Opens the app FOR** | **Notifications** (LINE push → tap-through, [14](../backend/modules/14-scheduler-alerts.md)) → **Stock Detail** → **問 Miru**; live-ish **Watchlist** ([16 §2a](../backend/modules/16-infrastructure.md) quote feed). |
| **Failure modes** | Alert with no fast path to the affected stock; stale quote presented as live; being forced through a desktop-shaped funnel on a phone; losing a draft when a call interrupts him. |
| **Why his surfaces** | The **top-bar ☆ watchlist** (his monitor, one tap from any tab — the drag-snap bottom-sheet is his most-used surface) + **Chat** (quick ask) + the **top-bar 🔔 bell** (alert inbox everywhere). Screener is a tab now but he mostly screens on web. |

### 2.4 ⚑ Wei 威 — the notification-driven re-entry user *(pattern, not a full 4th audience)*
A cross-cutting *mode* more than a distinct person (any of the three enters it): the user who is
**pulled back by a LINE push** and lands cold. Their entire session may be **push → tap → one screen
→ maybe one question → leave**. Mobile must treat the **notification as the front door**, not the
Home tab — deep-link straight to the relevant Stock Detail / event / thread with the caveat intact.
Called out because it reshapes flow (b) and principle §6.4/§6.5. *(Not added to the 5-tab IA — it's a
routing requirement, see ⚑ in §8.)*

---

## 3. Mobile context-of-use → UX consequence

The situations that shape every screen. Each maps to a concrete, testable consequence.

| Context | UX consequence |
|---|---|
| **One-handed / thumb reach** | Primary actions in the **bottom thumb zone**; Chat is the **raised center tab** ([README §2](README.md)); destructive/rare actions up top. Targets **≥44px**. |
| **Commute glance (seconds, not minutes)** | Home's top 3 blocks (跑馬燈 → 大盤概況 → 我的自選 → 今日事件) must answer "anything up?" **with zero taps/typing**. Time-to-glance is a success metric (§7). |
| **Notification-driven re-entry** | LINE push must **deep-link to the exact surface** (stock/event/thread), not dump on Home; caveat travels with the payload. Cold-land must be self-explanatory. |
| **Flaky / interrupted networks** (MRT, elevators, 4G→wifi) | Streaming turns use **SSE `Last-Event-ID` resume** ([07](../backend/modules/07-conversation-streaming.md)) — a dropped connection is a **non-event**, not a lost answer. Show "重新連線中…", not an error. |
| **App-backgrounding mid-turn** (call, app-switch) | **Per-thread draft persistence** + resumable stream ([13](../backend/modules/13-chat-interaction-features.md) §3) — returning restores the composer draft and the in-flight answer. |
| **Battery / data sensitivity** | Live-ish quote streaming (`/quote/stream`, Premium) **pauses when backgrounded / tab hidden**; reduced-motion & data-saver collapse marquee/pet animation; EOD fallback for Free. |
| **Ambient light / outdoors** | **Dark-first**, but red/green tuned for contrast on dark bg (pure green fails); glance legibility at arm's length ⇒ larger numerals, ▲▼ never color-alone. |
| **Interruptibility of attention** | Loading is **narration** ("Miru 正在看盤面…"), never a blank spinner, so a returning glance knows *what* is happening. |

---

## 4. Task flows (the core loops)

Six real mobile journeys as step sequences. **BE = backend touchpoint.**

### (a) Morning glance — 存股 (Sufen), zero typing
1. Open app → Home tab (default).
2. **跑馬燈** scrolls watchlist + 加權/櫃買 (紅漲綠跌 + ▲▼). *BE: `/quote/stream` Premium / EOD Free.*
3. Read **大盤概況** regime flag ("中性偏多"). *BE: `get_market_context` ([20](../backend/modules/20-analytics-scoring.md) §2b).*
4. Scan **我的自選** rows. *BE: `/memory` + `/quote`.*
5. Scan **今日事件** — 除權息/配息this week. *BE: `/calendar` filtered ([18](../backend/modules/18-calendar-events.md)).*
6. Close. **No typing, ≤10s, thumb-only.** ✅ core loop.

### (b) LINE alert → tap → Stock Detail → 問 Miru (Kai / Wei re-entry)
1. LINE push: "2330 台積電 觸及你設定的價格提醒". *BE: alert fire → LINE channel ([14](../backend/modules/14-scheduler-alerts.md) §4).*
2. Tap → deep-link **Stock Detail** (section stack), caveat present. *BE: `/analytics/{symbol}/*`, `/quote`.*
3. Scroll: quote → 多空 score gauge (band + numeric + factors, never number-alone) → 技術 chip.
4. Tap floating **「問 Miru」** → Chat seeded with the symbol. *BE: `/agent/query` ([07](../backend/modules/07-conversation-streaming.md)).*
5. Streamed narration ("正在查 台積電 近 20 日籌碼…") → answer + `caveat` + `follow_up` chips.

### (c) Beginner first question (Xiaoyu) — chip → streamed answer
1. Home → tap **快速提問** chip ("台積電最近怎麼了?") *or* Chat tab. *BE: seeds `/agent/query`.*
2. SSE narration line, then **streamed answer** (白話派 tone if set). *BE: [07](../backend/modules/07-conversation-streaming.md) narration; [23](../backend/modules/23-agent-personality.md).*
3. **`caveat`** badge lands (non-trading + currentness); pet reacts in its **separate companion region**.
4. `follow_up` chips ("什麼是除權息?", "風險在哪?") scaffold the next question — no blank-box paralysis.
5. `tool.locked` deep analysis → **馬賽克 teaser** + one calm upgrade CTA (frame sharp, detail blurred).

### (d) Add to watchlist + set an alert
1. Stock Detail / search result → **加入自選**. *BE: `POST /memory` (type=watchlist, [09](../backend/modules/09-memory.md)).*
2. Row overflow / detail → **設定提醒** (deep-link into Settings → Alerts — not a tab). *BE: `/alerts` ([14](../backend/modules/14-scheduler-alerts.md)).*
3. Pick price/event condition → confirm → LINE as delivery channel (default TW). *BE: `/schedules`, `/me/alert-preferences`.*
4. Confirmation returns user to where they were (Detail), no context loss.

### (e) Voice ask on the move (Kai/Sufen) — **confirm-before-send**
1. Chat composer → tap **mic** (**Free** STT). *BE: `/voice/transcribe` ([13](../backend/modules/13-chat-interaction-features.md) §voice).*
2. Speak; transcript returns as **editable text**, low-confidence tokens highlighted.
3. **User confirms before send** — never auto-fires (a 2330→2380 mis-hear must be catchable). Ambiguous symbol → `ask_user`.
4. Send → normal streamed turn (flow c). TTS read-aloud, if used, **must speak the caveat** (governance §8).

### (f) Resume a dropped chat turn on reconnect
1. Mid-answer the network drops (MRT tunnel) / app backgrounds.
2. UI shows **"重新連線中…"** (narration, not error); draft + partial answer retained.
3. On reconnect, client replays with **`Last-Event-ID`** → stream **continues from the last event**. *BE: [07](../backend/modules/07-conversation-streaming.md) resume.*
4. Answer completes as if nothing happened. **A dropped network is a non-event.** ✅

---

## 5. IA rationale (why the 5 tabs, from persona intent)

The [README §2](README.md) IA is **Home / Stock / Chat(center) / Screener / Settings**. Justification, not aesthetics:

| Ruling | Why (from intent) |
|---|---|
| **5 tabs, tuned to daily intent** | Each maps to a recurring mobile job: orient (Home), find-a-symbol (Stock), ask (Chat), find-a-list (Screener), config+overflow (Settings). No tab is a workbench. |
| **Chat is centered + accent-raised** | It's the product's soul and Xiaoyu's whole use; center = **thumb home** on a phone; the emphasis slot signals "ask me anything." |
| **Stock is a tab (symbol landing)** | Every persona's most common intent is "pull up a symbol" — search / recent / 我的自選 quick-access / 熱門 → pushes Stock Detail ([stock.md](stock.md)). A first-class thumb-reachable tab beats burying search in the top bar. |
| **Screener is a tab** | Promoted to a primary tab (was under Settings); on mobile it's a **preset-pick → ranked card list** (not the web funnel table), so it fits a phone and gives discovery a permanent home. |
| **Watchlist is NOT a tab — it's the top-bar ☆ (every tab) + Home glance block** | Monitoring must be **one tap from anywhere**, but it doesn't need a dedicated slot: the **top-bar ☆ opens the full watchlist as a drag-snap bottom-sheet** (Kai's monitor entry from any tab), and the **我的自選 Home block** gives Sufen a **zero-tap glance** on launch. Mirrors web (☆ toggles the right rail on every page — ../web/README §2). So glance = 0 tap, monitor/manage = 1 tap. |
| **Calendar is NOT a tab — it's a surface** | 除權息/配息/法說 (Sufen's 存股 loop, [18](../backend/modules/18-calendar-events.md)) surfaces as the **今日事件 Home block** (→ full [calendar.md](calendar.md)) + Stock Detail 事件 section. Now consistent with web (in-context, no standalone nav) — the earlier web↔mobile divergence is resolved by the new tab set. |
| **Alerts live under Settings, NOT a tab** | Low-frequency *config*; the create/edit rule-builder is a settings form. Discoverability preserved via **deep-links** (Detail / watchlist swipe → 設定提醒). |
| **Notifications = top-bar 🔔 bell, every tab** | It's a **receive-and-tap-through** surface (flow b), the front door for re-entry (Wei) — must be reachable everywhere, never buried in Settings. |
| **Pet (Sisi) is NOT a tab** | Cosmetic, brand-subordinate ([../brand.md](../brand.md) §3); ambient in Chat's companion region + a Settings collection page. Never crowds analysis. |

---

## 6. Mobile-specific research principles (build to these)

Actionable principles the UI Designer / UX Architect implement. Each is a contract, not a preference.

### 6.1 Glanceability hierarchy
Top of every glance surface answers "anything happening to *mine*?" in **one visual pass**: 跑馬燈 →
大盤概況 regime → 我的自選 → 今日事件 (Home ranks 1–3 are the **ambient core**, [README §4](README.md)).
Big tabular numerals, ▲▼ + sign, calm regime flag. No deep analysis above the fold.

### 6.2 Typing-minimization
The core loops (a, b, d, morning check) work with **zero free-text typing**. Beginners get **prompt
chips + `follow_up` chips** instead of a blank box; movement/hands-busy users get **voice** (confirm-
before-send). Symbol search is one tap in the top bar. Typing is an *option*, never a *gate*.

### 6.3 Thumb-zone reachability
Primary nav + composer mic sit in the **bottom thumb arc**; Chat = raised center. Targets **≥44px**.
Rare/destructive actions (delete, sign-out) live top or behind overflow. One-handed use is the default
posture (Sufen/Xiaoyu), not an edge case.

### 6.4 Interruption-resilience
Assume the session **will** be interrupted. **SSE `Last-Event-ID` resume** + **per-thread draft
persistence** make backgrounding and network drops **non-events** (flows e/f;
[07](../backend/modules/07-conversation-streaming.md), [13](../backend/modules/13-chat-interaction-features.md) §3).
Reconnect state is **narration ("重新連線中…"), never a red error** when recovery is possible.

### 6.5 Notification etiquette
LINE is the primary TW channel ([14](../backend/modules/14-scheduler-alerts.md) §4). Pushes are
**relevant, throttled, and deep-linked** to the exact surface with the **caveat in the payload** — never
明牌/買賣 phrasing, never a nag. The bell inbox mirrors every push. Re-entry (Wei) lands *in context*.

### 6.6 Progressive disclosure of deep analysis
Mobile shows the **headline first**, detail on demand: Stock Detail = **section stack** (quote → gauge
→ 技術 → 摘要/SWOT teaser → 事件 → 籌碼/新聞), not multi-panel; parallel agent lanes **collapse to the
narration track** with a "顯示過程" disclosure; raw `thinking` stays behind a disclosure, never default.
Premium depth arrives as a **馬賽克 teaser** (frame sharp, detail blurred), one calm CTA — invitation, not wall.

### 6.7 Currentness & non-trading, carried everywhere
Every analytical surface (gauge, SWOT, Top-N, chat answer, **alert copy**, TTS) carries the
**non-trading + currentness caveat** — a required, non-removable component
([backend/CLAUDE.md](../../backend/CLAUDE.md) §4). Copy never uses 明牌/飆股/目標價/買賣/進場/停損.
Personality/pet layers style **tone only**, never add trading language or drop a caveat.

### 6.8 Design to the 2026 mobile bar (anti-slop, motivated)
This client targets the **current (2025–2026) mobile UX bar**, not a dated one — but every modern
pattern must **earn its place for this audience** and never override the non-negotiables (紅漲綠跌+▲▼,
non-trading, caveats, tabular-nums, dark-first, reduced-motion). Reference basis: the shipped **Kestrel
app** and the **web app** — *not* pattern-mining
(the Mobbin MCP is unavailable; see §9). Patterns adopted, with justification:

| 2026 pattern | Why it earns its place here |
|---|---|
| **Floating / glass bottom-nav island** with morphing active pill + raised accent center ([README §2](README.md)) | Puts the primary jobs in the thumb arc; the raised center makes **Chat** (Xiaoyu's whole product) the obvious action. Glass = content-behind-blur keeps the glance surface visible while navigating. |
| **Content-behind-blur** top/bottom bars | Sufen's glance stays readable while chrome floats over it; blur has a **solid fallback** under `prefers-reduced-transparency`. |
| **Gesture-first navigation** — swipe, pull-to-refresh, **bottom-sheets over deep menus** | Fewer taps for one-handed use; alert-setup / overflow / symbol-detail actions surface as **bottom-sheets in the thumb zone** rather than pushed deep screens (helps Kai's fast alert response, Sufen's low-effort loop). |
| **AI-chat-first as a primary surface** (center tab) | The 2026 companion-app norm and the product's soul; matches how Xiaoyu (chat-native) already interacts. |
| **Notification-driven re-entry** (LINE push → deep-link) | The dominant modern mobile entry path; TW-native via LINE ([14](../backend/modules/14-scheduler-alerts.md)). See §6.5 / flow (b) / Wei. |
| **Dynamic-type / large-font resilience** | 存股 users (Sufen, 45–60) run large system fonts; layouts must reflow, never clip 台積電 2330 or truncate the caveat. |
| **Calm glanceable density** | Anti-slop: dense-but-legible, one visual pass (§6.1) — never a cluttered trader terminal on a phone. |
| **Motivated motion only** | Morph/blur/streaming life communicate state; **no magnitude-scaled or celebratory motion on price/score** (the web motion non-negotiables); everything above tier 3 honors reduced-motion. Trend for trend's sake is rejected. |

---

## 7. Success signals & anti-goals

### Good looks like
- **Time-to-glance ≤ ~10s:** open → "anything up with mine?" answered with **no typing, thumb-only** (flow a).
- **No-typing core loop works:** 存股 + monitoring users complete daily use via glance + chips + voice.
- **A dropped network is a non-event:** resume/draft-persistence make flaky mobile invisible (flows e/f).
- **Re-entry lands in context:** LINE push → correct surface in one tap, caveat intact (flow b).
- **Beginner never faces a blank box:** chips scaffold every start; answers land 白話 + caveat + follow-ups.
- **Alert/answer copy stays governance-clean:** zero 明牌/目標價/買賣 across pushes and analysis.

### Anti-goals (do NOT)
- **Cram the web workbench onto a phone** — no multi-panel Detail, no funnel factor tables, no side-by-side lanes.
- **Make the mascot crowd analysis** — Sisi stays cornered/ambient, brand-subordinate; never in the answer/caveat area.
- **Signal 漲跌 by color alone** — always ▲▼ + sign + `aria-label`; red=漲 / green=跌 (inverted), tuned for dark bg.
- **Auto-fire voice** — transcript is always confirm-before-send; low-confidence highlighted.
- **Blank spinners / bare errors** — loading is narration; recoverable drops are "重新連線中…", not failure.
- **Promote Screener/Calendar-heavy flows to phone-first** — mobile monitors and asks; deep screening is web's job.

---

## 8. Tensions surfaced — all resolved

- ✓ **Calendar IA — now consistent, no divergence.** The tab set changed to Home·Stock·Chat·Screener·
  Settings, so Calendar is a **surface on both clients** (in-context, no standalone tab) — the earlier
  web↔mobile divergence is gone (§5).
- ✓ **"Wei" is a mode, not a tab.** Notification re-entry reshapes routing (deep-link front door), not
  the IA; deep-link landing + cold-land state are specified in [interaction-spec.md](interaction-spec.md)
  §7.3.
- ✓ **Live-quote battery/data policy — owned.** The pause-when-backgrounded + data-saver cadence
  contract lives in [interaction-spec.md](interaction-spec.md) §6.1 + backend §2a (`cadence:
  normal|saver`); it no longer falls between docs.
- ✓ **Voice split — STT Free, TTS Premium** ([13](../backend/modules/13-chat-interaction-features.md)):
  **voice-input is Free** (quota-bounded like text) so the beginner's on-the-go ask-loop (Xiaoyu, flow
  e) works without a paywall; **TTS read-aloud stays Premium**. Confirm-before-send + spoken-caveat
  apply on both tiers.
- ✓ **Voice split — STT Free, TTS Premium** ([13](../backend/modules/13-chat-interaction-features.md)):
  **voice-input is Free** (quota-bounded like text) so the beginner's on-the-go ask-loop (Xiaoyu, flow
  e) works without a paywall; **TTS read-aloud stays Premium**. Confirm-before-send + spoken-caveat
  apply on both tiers. *(Resolved.)*

---

## 9. Reference basis

This foundation is grounded in the **shipped Kestrel app**, the existing **mobile
[README.md](README.md)**, and the **web app**,
plus the backend module docs cited inline. Live UX pattern-mining (Mobbin MCP) was **unavailable /
paywalled**, so the 2026-bar principles (§6.8) are derived from the shipped design system and
first-principles fit to this audience — not from scraped reference galleries.
```
