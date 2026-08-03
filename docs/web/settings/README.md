# 設定 / Settings (`/settings/*`)

Nested config hub (gear, bottom of rail — not a daily destination). Parent: [../README.md](../README.md) §2.3.

## Sub-pages → backend
| Sub-page | Content | Endpoint |
|----------|---------|----------|
| 帳號 / Profile | display_name·birthday·phone·nationality | `PATCH /me` ([12](../../backend/modules/12-multi-user-tiers.md)) |
| 連結帳號 / Linked providers | Google/LINE link/unlink | `/me/connections` |
| **了然的語氣 / Agent style** | 穩健/白話/簡潔/數據派 preset picker ("Miru's voice", never "advisor") | `/personality/presets`, `PATCH /me {personality_id}` ([23](../../backend/modules/23-agent-personality.md)) |
| **夥伴 / Pets** | collection · gacha · equip · streak (cosmetic, no tier gate) | `/pets/*` ([21](../../backend/modules/21-pet-gamification.md)) |
| 方案與帳單 / Billing | plans, BYOK checkbox, portal, history | `/billing/*` ([15](../../backend/modules/15-payments-paddle.md)) |
| API 金鑰 / BYOK | store/remove keys | `/me/keys` |
| 用量 / Usage & quota | chats/day, req/min, FinMind, cost | `/usage/quota`, `/usage/summary` |
| **提醒與排程 / Alerts & Schedules** (Pro) | price/event **alert rules** + **scheduled jobs** (create/list/edit/delete) — the alerts home on **both web and mobile** (no top-level nav item) | `/alerts`, `/schedules` ([14](../../backend/modules/14-scheduler-alerts.md)) |
| 通知偏好 / Alert prefs | channels (in-app/LINE/telegram/email), quiet hours, morning digest | `/me/alert-preferences` ([14](../../backend/modules/14-scheduler-alerts.md)) |
| 記憶 / Memory | list + delete | `/memory` ([09](../../backend/modules/09-memory.md)) |

## Also under 設定: **Screener** ([../screener/README.md](../screener/README.md) — on mobile; web has it as a primary nav item) · Themes. Alerts & Schedules is now a first-class Settings row above (both platforms).

## Visual design
Tokens/radius/spacing from [../design-system.md](../design-system.md) (card 16px, input 10px, pill
999px, section gap 24–32px, pad 20px). Settings is calm/low-density — not a data surface.
**Behavioural contract** (form dirty/save/validation, Alert-rule builder empty/create/validate/delete-
confirm, keyboard/focus) = [interaction-spec §2.13](../interaction-spec.md#213-settings-and-the-alert-rule-builder).

- **Layout.** Two-pane on ≥1024px: left sub-page list (nav pattern of design-system §6.2 — `divide-y
  --border`, active = `--accent-weak` pill + 2px `--accent` edge bar) + right content pane, single
  column ~640px max, form rows grouped by `divide-y --border` with 18–20px/600 section heads.
- **Controls.** Inputs radius 10px `--surface` + `1px --border`, focus-visible `--accent` ring;
  toggles/segmented pickers use `--accent` active fill; primary buttons radius 12px `--accent`.
- **了然的語氣** picker = radio-card row (穩健/白話/簡潔/數據派), selected = `--accent` hairline + tint.
  **夥伴/Pets** = gacha/collection grid (cosmetic; reduced-motion → static, no shake/pop). **用量**
  = quota bars in `--accent` (tabular-nums). **方案與帳單** = plan cards; BYOK checkbox.
- **Theme toggle** (dark-first default) lives here; switching re-reads root CSS-var tokens. **馬賽克**
  never appears in Settings (config, not gated analysis). WCAG AA both themes.

## Governance
Per-user isolation; secrets never echoed; personality/pets are style/cosmetic only — never affect
analysis or gate data. Theme toggle (dark-first) lives here too.
