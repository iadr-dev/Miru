# 設定 / Settings (tab 5 — the config hub)

The config + overflow hub (Home·Stock·Chat·Screener·**Settings**). Calm, low-density — **not** a data
surface. Hosts **Alerts/Schedules** (the mobile alerts home), Miru 的語氣 Personality, 夥伴 Pets, and
account config. (**Screener is no longer here** — it was promoted to its own tab, [screener.md](screener.md).)
Parent index: [README.md](README.md) §2. UX rationale: [ux-foundation.md](ux-foundation.md).
Behaviour/state: [interaction-spec.md](interaction-spec.md). Web parity:
the web app.

> Non-negotiables: config, not analysis — **馬賽克 never appears in Settings**; secrets never echoed;
> personality/pets are **style/cosmetic only**, never affect analysis or gate data. Dark-first · zh-TW
> first · WCAG AA both themes · ≥44px targets. Notifications inbox = **top-bar 🔔 bell** on every tab,
> not buried here.

## Sub-pages → backend (priority order)

| Sub-page | Content | Endpoint |
|----------|---------|----------|
| **提醒與排程 / Alerts & Schedules** | price/event **alert rules** + **scheduled jobs** (create/list/edit/delete) — the alerts home on mobile | `/alerts`, `/schedules` ([14](../backend/modules/14-scheduler-alerts.md)) |
| **主題 / Themes** 🔒 | LLM-discovered themes + 資金流向 (also reachable from [screener.md](screener.md) Row-2) | `/analytics/themes` (Premium) |
| **Miru 的語氣 / Agent style** | 穩健/白話/簡潔/數據派 preset picker ("Miru's voice", never "advisor") | `/personality/presets`, `PATCH /me {personality_id}` ([23](../backend/modules/23-agent-personality.md)) |
| **夥伴 / Pets** | collection · gacha · equip · streak (cosmetic, no tier gate) | `/pets/*` ([21](../backend/modules/21-pet-gamification.md)) |
| 方案與帳單 / Billing | plans, BYOK checkbox, portal, history | `/billing/*` ([15](../backend/modules/15-payments-paddle.md)) |
| API 金鑰 / BYOK | store/remove keys | `/me/keys` |
| 用量 / Usage & quota | chats/day, req/min, FinMind, cost | `/usage/quota`, `/usage/summary` |
| 通知偏好 / Alert prefs | channels (in-app/**LINE**/telegram/email), quiet hours, morning digest | `/me/alert-preferences` ([14](../backend/modules/14-scheduler-alerts.md)) |
| 記憶 / Memory | list + delete | `/memory` ([09](../backend/modules/09-memory.md)) |
| 帳號 / Profile | display_name·birthday·phone·nationality | `PATCH /me` ([12](../backend/modules/12-multi-user-tiers.md)) |
| 連結帳號 / Linked providers | Google/LINE link/unlink | `/me/connections` |
| 主題外觀 / Theme | dark-first toggle (light available) | client + profile |

## Layout (grouped hub list → pushed detail; forms as bottom-sheets/pushed panes)

```
┌───────────────────────────────────────┐
│ ‹ 設定                    🔔  ◍         │ ← glass top bar (bell = notifications inbox)
├───────────────────────────────────────┤
│ 監控                                    │ ← bento group
│  ⏰ 提醒與排程                       →  │
│  🎯 主題 Themes                    🔒→ │
├───────────────────────────────────────┤
│ 個人化                                  │
│  🗣 Miru 的語氣  (穩健)               →  │
│  🐦 夥伴 Pets   Sisi 🔥12           →  │
├───────────────────────────────────────┤
│ 帳號與用量                              │
│  💳 方案與帳單 · 🔑 BYOK · 📊 用量   →  │
│  🔔 通知偏好 · 🧠 記憶 · 👤 帳號     →  │
├───────────────────────────────────────┤
│ 外觀   ◐ 深色 (預設)  ○ 淺色           │
└───────────────────────────────────────┘
        ⌂  ⌁  ◉Chat  ▽  ⚙
     Home Stock Chat Screener Settings   (Settings = tab 5)
```

### Alert-rule builder (mobile form — pushed pane / bottom-sheet)

```
        ╭───── drag handle ─────╮
        │ 新增提醒                │
        │ 標的  [ 2330 台積電  🔍]│ ← symbol picker (reuses Stock search)
        │ 類型  [ 價格 | 事件 ]   │ ← segmented
        │ 條件  股價 [ ≥ ] [1,100]│ ← factor · op · value (紅漲綠跌 preview on threshold)
        │ 通知  [in-app][LINE][…] │ ← channels (LINE = primary TW channel)
        │ [ 取消 ]      [ 儲存 ]  │
        ╰────────────────────────╯
```

## Visual design

Tokens from the web app (card 16px, input 10px, pill 999px,
section gap 24px, pad 20px). Settings is **calm/low-density**. Behavioural contract (form dirty/save/
validation, alert-rule empty/create/validate/delete-confirm, keyboard/focus) =
[interaction-spec.md](interaction-spec.md). **Dark-first, one column, 2026 mobile bar.**

- **Frame.** `100dvh` on `--bg`; **glass top bar** (`‹` when pushed · 🔔 notifications inbox · account)
  content scrolls behind blur. Bottom floating island present (this is a tab).
- **Hub list** = **bento-grouped** rows (監控 / 個人化 / 帳號與用量 / 外觀), each group a `--surface`
  card with `divide-y --border` rows (≥44px, icon + zh label + optional value + chevron), 18px/600 group
  heads separated by negative space. Tapping a row **pushes a detail pane** (back-gesture returns) or
  opens a **bottom-sheet** for short forms. Active/pressed row → `--surface-2`.
- **提醒與排程** = the alerts home. Rule list (`divide-y --border`); `+ 新增提醒` opens the **alert-rule
  builder** as a drag-snap bottom-sheet (or pushed pane): symbol picker (reuses the [Stock](stock.md)
  search) · 價格/事件 type segmented · factor·op·value row (threshold preview uses 紅漲綠跌 + ▲▼ where
  signed) · channel toggles (in-app/**LINE**/telegram/email). Delete = swipe-to-reveal `✕` with confirm.
  Schedules (scheduled jobs) share the pattern.
- **Controls.** Inputs radius 10px `--surface` + `1px --border`, focus-visible `--accent` ring; toggles/
  segmented pickers use `--accent` active fill; primary buttons radius 12px `--accent`; ≥44px targets.
- **Miru 的語氣** = radio-card row (穩健/白話/簡潔/數據派), selected = `--accent` hairline + `--accent-weak`
  tint; framed **「Miru 的語氣」**, never "choose your advisor" (protects the non-trading promise).
- **夥伴 / Pets** = gacha/collection grid (cosmetic, no tier gate); **reduced-motion → static** (no
  shake/pop/gacha animation). Streak chip mirrors the Home 夥伴 block.
- **用量** = quota bars in `--accent` (tabular-nums). **方案與帳單** = plan cards + BYOK checkbox (BYOK
  → cheaper price). **通知偏好** = channel toggles + quiet hours + morning digest.
- **主題外觀** toggle (dark-first default); switching re-reads root CSS-var tokens. **馬賽克 never
  appears in Settings** (config, not gated analysis). WCAG AA both themes.

### States
- **Loading** = shimmer rows, never a spinner. **Empty** — 提醒 with no rules → calm 「尚無提醒，點 +
  新增」 (+ optional deep-link from a stock's 設定提醒). **Error** = inline calm retry; a failed save
  keeps the dirty form, never silently drops input. **Reduced-motion** → no pet animation, sheets snap.

## Governance
Per-user isolation; secrets (BYOK keys) never echoed. Personality/pets are **style/cosmetic only** —
never affect analysis or gate data, never introduce trading language. Alert copy is non-trading (a
threshold notice, not a cue). No 明牌/飆股/目標價/買賣 copy
([../backend/governance.md](../backend/governance.md) §1, [../brand.md](../brand.md) §5).
