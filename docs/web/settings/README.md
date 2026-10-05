1	# 設定 / Settings (`/settings/*`)
2	
3	Nested config hub (gear, bottom of rail — not a daily destination). Parent: [../README.md](../README.md) §2.3.
4	
5	## Sub-pages → backend
6	| Sub-page | Content | Endpoint |
7	|----------|---------|----------|
8	| 帳號 / Profile | display_name·birthday·phone·nationality | `PATCH /me` ([12](../../backend/modules/12-multi-user-tiers.md)) |
9	| 連結帳號 / Linked providers | Google/LINE link/unlink | `/me/connections` |
10	| **了然的語氣 / Agent style** | 穩健/白話/簡潔/數據派 preset picker ("Miru's voice", never "advisor") | `/personality/presets`, `PATCH /me {personality_id}` ([23](../../backend/modules/23-agent-personality.md)) |
11	| **夥伴 / Pets** | collection · gacha · equip · streak (cosmetic, no tier gate) | `/pets/*` ([21](../../backend/modules/21-pet-gamification.md)) |
12	| 方案與帳單 / Billing | plans, BYOK checkbox, portal, history | `/billing/*` ([15](../../backend/modules/15-payments-paddle.md)) |
13	| API 金鑰 / BYOK | store/remove keys | `/me/keys` |
14	| 用量 / Usage & quota | resolved **limits** (chats/day, req/min, FinMind class, features) — ⚑ **live consumption ("X/3 used today") + cost are NOT exposed**: `/usage/summary` is unbuilt, `/usage/quota` returns limits only ([../DEVELOPMENT.md §4a](../DEVELOPMENT.md)) | `/usage/quota` |
15	| **提醒與排程 / Alerts & Schedules** (all users; sponsor values masked) | template-based **alert rules** + **scheduled AI briefings** (create/list/edit/delete), notification history — the alerts home on **both web and mobile** (no top-level nav item); UX spec [alerts.md](alerts.md) | `/alerts`, `/schedules`, `/notifications` ([14](../../backend/modules/14-scheduler-alerts.md), the alerts v2 design) |
16	| 通知偏好 / Alert prefs | *(v1 state; v2 per the alerts v2 design)* channels (in-app/LINE/**telegram**/email/push), quiet hours, morning digest — ⚑ only **in-app** delivers today; LINE/telegram/email/push are no-network stubs ([../DEVELOPMENT.md §4d](../DEVELOPMENT.md)) | `/me/alert-preferences`, `/me/devices` ([14](../../backend/modules/14-scheduler-alerts.md)) |
17	| 記憶 / Memory | list + delete | `/memory` ([09](../../backend/modules/09-memory.md)) |
18	
19	## Also under 設定: **Screener** ([../screener/README.md](../screener/README.md) — on mobile; web has it as a primary nav item) · Themes. Alerts & Schedules is now a first-class Settings row above (both platforms).
20	
21	## Visual design
22	Tokens/radius/spacing from [../design-system.md](../design-system.md) (card 16px, input 10px, pill
23	999px, section gap 24–32px, pad 20px). Settings is calm/low-density — not a data surface.
24	**Behavioural contract** (form dirty/save/validation, Alert-rule builder empty/create/validate/delete-
25	confirm, keyboard/focus) = [interaction-spec §2.13](../interaction-spec.md#213-settings-and-the-alert-rule-builder).
26	
27	- **Layout.** Two-pane on ≥1024px: left sub-page list (nav pattern of design-system §6.2 — `divide-y
28	  --border`, active = `--accent-weak` pill + 2px `--accent` edge bar) + right content pane, single
29	  column ~640px max, form rows grouped by `divide-y --border` with 18–20px/600 section heads.
30	- **Controls.** Inputs radius 10px `--surface` + `1px --border`, focus-visible `--accent` ring;
31	  toggles/segmented pickers use `--accent` active fill; primary buttons radius 12px `--accent`.
32	- **了然的語氣** picker = radio-card row (穩健/白話/簡潔/數據派), selected = `--accent` hairline + tint.
33	  **夥伴/Pets** = gacha/collection grid (cosmetic; reduced-motion → static, no shake/pop). **用量**
34	  = quota bars in `--accent` (tabular-nums). **方案與帳單** = plan cards; BYOK checkbox.
35	- **Theme toggle** (dark-first default) lives here; switching re-reads root CSS-var tokens. **馬賽克**
36	  never appears in Settings (config, not gated analysis). WCAG AA both themes.
37	
38	## Governance
39	Per-user isolation; secrets never echoed; personality/pets are style/cosmetic only — never affect
40	analysis or gate data. Theme toggle (dark-first) lives here too.
41	