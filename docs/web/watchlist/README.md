1	# 自選 / Watchlist (right-side rail)
2	
3	The right-side drag-and-drop rail (toggle from top bar `☆`, persists across pages — **not** a route).
4	Full component spec: [../design-system.md](../design-system.md) §7.7 (rail) + §7.8 (`StockRow`).
5	Backend: [ADR-0027](../../backend/adr/0027-first-class-watchlist.md), `watchlists`/`watchlist_items`.
6	
7	## Structure
8	- **Market tabs** (`market`×`asset_class`): TW=`(TW,stock)` · ETF=`(TW,etf)` · US=`(US,*)`.
9	- **Named groups** — expand/collapse + count; `+ 新增分組`; **drag-to-reorder** groups & items
10	  (lexorank). `/watchlists`, `/watchlists/{id}/items`.
11	- **Rows** = `StockRow` (§7.8): grip · candlestick · 代號名稱 · [sector chip] · price · ▲▼% · sparkline ·
12	  hover ✕ remove + ☆ star. Category extras (多空 dot / 折溢價 / 處置 flag). Live via `/quote(/stream)`.
13	
14	## Default presets (server-seeded on empty — from Kestrel `DEFAULT_WATCHLISTS`)
15	| Tab | Group | Symbols |
16	|-----|-------|---------|
17	| TW | 半導體 | 2330 · 2454 · 3711 · 2303 |
18	| TW | 權值股 | 2317 · 2382 · 2881 · 2882 · 2412 |
19	| US | US Tech | NVDA · AAPL · TSLA · MSFT |
20	| US | US Index | SPY · QQQ · DIA |
21	| ETF | 台股 ETF | 0050 · 0056 · 00878 · 00919 |
22	| ETF | 債券/海外 ETF | 00679B · 00713 · 00757 |
23	
24	## Endpoints
25	`GET /watchlists?market=` (seeds presets when empty) · POST/PATCH/DELETE group · `PUT/DELETE
26	/watchlists/{id}/items/{symbol}` (idempotent star) · reorder/move. Batch quotes reuse `/quote(/stream)`.
27	
28	## Visual design
29	Full component spec: [../design-system.md](../design-system.md) §7.7 (rail) + §7.8 (`StockRow`).
30	Tokens/radius from design-system (card 16px, pill 999px, `VISUAL_DENSITY:6`).
31	
32	- **Rail** 300–320px, `--surface` on `--bg`, `1px --border` left divider, toggle from top-bar `☆`.
33	  Header: `● Watchlist` + ✕. **Market tabs** (TW/US/ETF) = segmented pill row, active `--accent`.
34	- **Groups.** Collapsible header (▾ + name + count badge in `--surface-2` pill); `+ 新增分組` ghost row.
35	  Grouping by `divide-y --border`, not boxes.
36	- **`StockRow`** (§7.8): `⠿` grip (`--text-muted`, drag → 50% opacity) · single **candlestick cell**
37	  (紅漲綠跌 body) · `代號 名稱` (no mid-token break) · `SectorChip` (sector-token muted tint + hairline) ·
38	  tabular price (`--font-num`) · **▲▼ + %** in `--up`(漲)/`--down`(跌) · hover-reveal mini sparkline
39	  (accent stroke, up/down-tinted fill) + ✕ remove (`opacity-0 group-hover:opacity-100`).
40	- **Category extras** degrade gracefully: 多空 `ScoreBandDot` (偏多/中性/偏空) · ETF 折溢價% / 殖利率 ·
41	  處置 `--warn` flag chip. sponsor extras (分點/主動ETF) → 馬賽克 lock for Free.
42	- **States.** Live tick → row bg flashes `--up-weak`/`--down-weak`. Loading → shimmer rows. Empty
43	  group → 「尚無股票，點 ☆ 或問 Miru 加入」; empty tab → seeded with the preset above. Drag = `@dnd-kit`
44	  sortable; reduced-motion → no drag transition, keyboard reorder (Space lift, ↑/↓, aria-live announce).
45	- 紅漲綠跌 + ▲▼ + tabular-nums throughout; `--accent` teal is chrome-only, never a price.
46	
47	## Governance
48	Public user data, non-trading, ungated CRUD (soft caps only); live-quote column inherits Premium+
49	(EOD free / live-ish sponsor). 紅漲綠跌+▲▼, tabular-nums, reduced-motion (keyboard reorder).
50	