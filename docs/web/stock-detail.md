1	# 了然 · Miru — Stock Detail page (`/stock/{symbol}`) — index
2	
3	Overview + per-tab index. Grounded in the backend ([../backend/api-reference.md](../backend/api-reference.md)
4	§4/§4a, modules 03/18/20), design language ([design-system.md](design-system.md)), adapted from the
5	Kestrel reference. **Each tab has its own doc in [`stock-detail/`](stock-detail/).**
6	
7	> **Global non-negotiables on every card** (design-system §4): 紅漲綠跌 `--up`(漲)/`--down`(跌)
8	> **never color-alone → always ▲▼+sign** · **tabular numerals** · **non-trading `<Caveat>`** on every
9	> analytical card · **馬賽克 teaser** on `sponsor` data (分點/官股/主動ETF holdings/`/quote/stream`) ·
10	> dark-first. Every rich view = the **`ChartWithDataSheet` primitive** ([design-system §7.11](design-system.md)).
11	
12	## 1. Page shell + AssetHeader (sticky, all asset kinds)
13	
14	```
15	┌──────────────────────────────────────────────────────────────────────────────────┐
16	│  台積電 2330  [半導體]                              ☆ 加入自選   💬 問 Miru        │  ← AssetHeader (no marquee)
17	│  1,050.00  ▲15.00 +1.45%   開1,038 高1,052 低1,035 量38,204張   band:[偏多 72]     │
18	│  ● 即時 ~5s延遲 (Premium)  /  ● EOD 08/01 (Free)                                   │
19	├──────────────────────────────────────────────────────────────────────────────────┤
20	│ 基本資料│產業分析│財務分析│籌碼分析│技術分析│智能摘要│相關新聞│研究圖表│ 即時🔒 │  ← tab bar (overflow-x)
21	└──────────────────────────────────────────────────────────────────────────────────┘
22	```
23	
24	**AssetHeader → backend:** name+code+sector `get_company_profile` · price+▲▼% `GET /quote(/stream)`
25	(`LiveTickValue`+`PriceDelta`, flash on tick) · 開/高/低/量 `GET /quote` · band pill `[偏多 72]`
26	`GET /analytics/{symbol}/score` (click → 智能摘要 tab, scrolls to gauge) · ☆ star →
27	`PUT /watchlists/{id}/items/{symbol}` ([ADR-0027](../backend/adr/0027-first-class-watchlist.md)) ·
28	💬 seeds `POST /agent/query` (docks Chat right rail ≥1280px) · currentness dot `GET /health`.
29	
30	## 2. Tab sets (per-asset-kind; `detect_kind` mod 20 §2c)
31	
32	**TW STOCK — 8 tabs + 即時:** `基本資料 · 產業分析 · 財務分析 · 籌碼分析 · 技術分析 · 智能摘要(含多空評分) ·
33	相關新聞 · 研究圖表` **+ 即時 (Premium)**. **TW ETF — 6:** `概覽 · 智能摘要 · 持股/操作 · 折溢價 ·
34	技術分析 · 相關新聞`. **US (phased, mod 19):** renders tabs, content "尚未支援".
35	
36	Tab bar: horizontal-scroll pill row, active `--accent`, `<ErrorBoundary>` per tab. Grouped tabs
37	(籌碼/財務/新聞) use nested category sub-tabs ([design-system §6.3](design-system.md)).
38	
39	## 3. Per-tab docs (each = full component/chart/datasheet/endpoint spec)
40	
41	| Tab | Doc | Backing (headline) |
42	|-----|-----|--------------------|
43	| 基本資料 | [stock-detail/基本資料.md](stock-detail/基本資料.md) | profile, insiders, 公告/事件, 被N支ETF持有, ESG |
44	| 產業分析 | [stock-detail/產業分析.md](stock-detail/產業分析.md) | 同業比較, 供應鏈, 多空訊號, 產業鏈資金流🔒 |
45	| 財務分析 | [stock-detail/財務分析.md](stock-detail/財務分析.md) | 股利/營收/獲利/財務結構/現金流/自結/法說 |
46	| 籌碼分析 | [stock-detail/籌碼分析.md](stock-detail/籌碼分析.md) | 法人/融資券/借券/分點🔒/股權分散 |
47	| 技術分析 | [stock-detail/技術分析.md](stock-detail/技術分析.md) | K線 + MA/KD/MACD/RSI/布林 + 支撐壓力/N波 |
48	| 智能摘要 | [stock-detail/智能摘要.md](stock-detail/智能摘要.md) | 多空 gauge + factor bars + AI 摘要/SWOT |
49	| 相關新聞 | [stock-detail/相關新聞.md](stock-detail/相關新聞.md) | 即時/財經/PTT/名人 |
50	| 研究圖表 | [stock-detail/研究圖表.md](stock-detail/研究圖表.md) | 估值河流, 分析師深度, 個股事件calendar, 除權息 |
51	| 即時 (Premium) | [stock-detail/即時.md](stock-detail/即時.md) | best-5 ladder + tick/分K + 當沖率 🔒 |
52	
53	**TW ETF variant tabs:** 概覽 (`get_company_profile`+`get_etf_aum`+`get_etf_beneficiaries`) · 智能摘要
54	· 持股/操作 (passive PCF + 主動ETF 操作日報 `get_active_etf_report` 🔒, see [財務分析](stock-detail/財務分析.md)
55	sibling `ActiveEtfDaily`) · 折溢價 (`EtfPremiumHistory` — diverging-bar 折溢價% + 股價/淨值 dual-line).
56	
57	## 4. ASCII — 產業分析 tab
58	
59	```
60	┌─ 產業分析 ────────────────────────────────────────────────────────────────┐
61	│ ┌ 智能摘要·市場定位 ──────────────────────────────┐ [Premium teaser]        │
62	│ │ 台積電為全球晶圓代工龍頭，先進製程市占>60%…       │ ← headline SHARP        │
63	│ │ ┌ SWOT ────────────────────────────────────┐   │                         │
64	│ │ │ S 優勢 ░░░░░░  │ W 劣勢 ░░░░░  ← 馬賽克     │   │ free: SWOT blurred      │
65	│ │ │ 🔒 升級 Premium 看完整 SWOT               │   │ under backdrop-blur     │
66	│ │ │ O 機會 ░░░░░░  │ T 威脅 ░░░░░              │   │                         │
67	│ │ └───────────────────────────────────────────┘   │                         │
68	│ └──────────────────────────────────────────────────┘                        │
69	│ ┌ 同業比較 ───────────────────────────────────────────────────────────┐    │
70	│ │ 名稱代號    市值    本益比  ROE   月營收YoY  多空    [半導體]         │    │
71	│ │ 台積電2330 14.2兆  18.3x  28.1% ▲+34.2%  [偏多72]● ← this row        │    │
72	│ └──────────────────────────────────────────────────────────────────────┘    │
73	│ ┌ 供應鏈關係圖 ──────────────────────────┐ ┌ 多空訊號 ─────────────────┐   │
74	│ │ 上游[環球晶]►[台積電2330]►下游[聯發科]  │ │ 偏多主導 ▇▇▇▇▇▇▇▇▁▁ 75%   │   │
75	│ │ [半導體][設備][封測] sector-tinted      │ │ ⚠ 均線描述，非買賣訊號     │   │
76	│ └─────────────────────────────────────────┘ └────────────────────────────┘   │
77	└────────────────────────────────────────────────────────────────────────────┘
78	```
79	
80	## 5. ASCII — 籌碼分析 tab (分點 sponsor mosaic)
81	
82	```
83	┌─ 籌碼分析 ────────────────────────────────────────────────────────────────┐
84	│ [法人] [融資券] [借券] [分點🔒] [股權分散]   ← sub-tabs; 🔒 shows BEFORE click │
85	│ ── 法人 ── 外資▇▇▇▇▇▇▇▲+12,450張連8買 │投信▇▇▇▲+2,180連3買│自營▁▼-340張      │
86	│  合計 ▲+14,290張 (紅買超/綠賣超, 20d bar, hover→per-day) ⚠非建議·季底作帳低信心 │
87	│ ── 分點 (sponsor) ── ░░░░░ ▓▓張 ░░ ← 馬賽克 (券商sharp,值blur)                │
88	│    🔒 分點/官股籌碼為 Premium · 升級解鎖主力分點進出                          │
89	│ ── 股權分散 ── 千張大戶比率▇▇▇68.2%▲·股東人數▼=籌碼集中                       │
90	└────────────────────────────────────────────────────────────────────────────┘
91	```
92	
93	## 6. Interactive effects & governance
94	Live-tick flash · gauge needle ease · K線 crosshair (←/→) · peer/supply-chain hover · sub-tab
95	shared-layout glide · mosaic reveal-on-upgrade. All honor `prefers-reduced-motion`; **gauge never
96	re-animates on a quote tick**. Every analytical card carries `<Caveat kind="non-trading">`; analyst
97	data attributed; no 明牌/飆股/目標價/買賣 copy ([../brand.md](../brand.md) §5). Full interaction
98	contracts: [design-system.md](design-system.md) §5/§7.11.
99	
100	## 7. Visual design (the workbench)
101	
102	Tokens/radius/spacing from [design-system.md](design-system.md) (`VISUAL_DENSITY:6`, card 16px /
103	input 10px / pill 999px / button 12px, 4px base, section gap 24–32px, card padding 20px). Dark-first.
104	
105	- **AssetHeader** (sticky, top): `--surface` bg, `1px --border` bottom hairline, pad 20px. `名稱 代號`
106	  (no mid-token break, 20px/600) + `SectorChip` · big tabular price 28–32px `--font-num` · **▲▼ +
107	  chg + %** in `--up`(漲)/`--down`(跌) · 開/高/低/量 as inline `--text-muted` micro row · band pill
108	  `[偏多 72]` (`--up`-tinted, click → 智能摘要 gauge) · currentness dot (`● 即時` `--accent` / `● EOD`
109	  `--text-muted`). ☆ / 💬 as ghost icon buttons (radius 12px). Live price flashes `--up-weak`/`--down-weak`.
110	- **Tab bar.** Segmented pill row on `--surface`, overflow-x scroll, active = `--accent` underline +
111	  shared-layout glide (`layoutId`); `即時🔒` shows lock glyph pre-click; `<ErrorBoundary>` per tab.
112	- **Canvas grid (≥1280px).** 12-col; cards span 6 (side-by-side pairs) or 12 (full charts). Card =
113	  `--surface` + `1px --border` + radius 16px + pad 20px + tinted shadow (never neon/pure-black), hover
114	  → `--surface-2`. Multi-row data uses `divide-y --border`. 768–1279px → 2→1 col.
115	- **Peer / supply-chain visuals.** `PeerCompaniesSection` = sortable table, `divide-y --border`,
116	  tabular-nums, 月營收YoY/多空 cells in `--up`/`--down` **+ ▲▼**, `SectorChip` per row, hover reveals
117	  spark, row → detail. `SupplyChainGraph` nodes sector-tinted (sector tokens), focus node `--accent`
118	  ring, edges drawn via `.flow-line` (dash-draw on mount, `flow-draw`), reduced-motion → static.
119	- **`SectorChip`.** Muted sector-token bg (~15% α) + solid-hue text + hairline of same hue, pill 999px,
120	  12px. Category only, never a price. Tokens: [design-system §2](design-system.md).
121	- **馬賽克 teaser** (SWOT body, 產業鏈資金流, 分點, 即時, AI summary): value layer under
122	  `backdrop-blur` + subtle diagonal-line texture + centered **lock chip** + one calm 升級 CTA; the
123	  **frame/headline/label columns stay sharp**, only values blur. Consistent across all locked surfaces.
124	- **States.** Loading = shaped shimmer (`.animate-shimmer`) matching final layout (header ghost + card
125	  blocks + ghost rows), never a spinner. Empty → route to Chat. Error → inline calm retry.
126	
127	Every rich chart view = the **`ChartWithDataSheet`** primitive — full visual in
128	[design-system.md §7.11](design-system.md) (ControlBar, `--grid`/`--crosshair`, sticky thead,
129	tabular-nums, `--up`/`--down` + ▲▼ signed cells, `--chart-1..5` categorical, active row `--surface-2`,
130	`<Caveat>` footer + unit + `DataFreshnessBadge`). Per-tab docs specify chart kind + datasheet columns.
131	