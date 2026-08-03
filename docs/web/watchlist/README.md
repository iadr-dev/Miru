# 自選 / Watchlist (right-side rail)

The right-side drag-and-drop rail (toggle from top bar `☆`, persists across pages — **not** a route).
Full component spec: [../design-system.md](../design-system.md) §7.7 (rail) + §7.8 (`StockRow`).
Backend: [ADR-0027](../../backend/adr/0027-first-class-watchlist.md), `watchlists`/`watchlist_items`.

## Structure
- **Market tabs** (`market`×`asset_class`): TW=`(TW,stock)` · ETF=`(TW,etf)` · US=`(US,*)`.
- **Named groups** — expand/collapse + count; `+ 新增分組`; **drag-to-reorder** groups & items
  (lexorank). `/watchlists`, `/watchlists/{id}/items`.
- **Rows** = `StockRow` (§7.8): grip · candlestick · 代號名稱 · [sector chip] · price · ▲▼% · sparkline ·
  hover ✕ remove + ☆ star. Category extras (多空 dot / 折溢價 / 處置 flag). Live via `/quote(/stream)`.

## Default presets (server-seeded on empty — from Kestrel `DEFAULT_WATCHLISTS`)
| Tab | Group | Symbols |
|-----|-------|---------|
| TW | 半導體 | 2330 · 2454 · 3711 · 2303 |
| TW | 權值股 | 2317 · 2382 · 2881 · 2882 · 2412 |
| US | US Tech | NVDA · AAPL · TSLA · MSFT |
| US | US Index | SPY · QQQ · DIA |
| ETF | 台股 ETF | 0050 · 0056 · 00878 · 00919 |
| ETF | 債券/海外 ETF | 00679B · 00713 · 00757 |

## Endpoints
`GET /watchlists?market=` (seeds presets when empty) · POST/PATCH/DELETE group · `PUT/DELETE
/watchlists/{id}/items/{symbol}` (idempotent star) · reorder/move. Batch quotes reuse `/quote(/stream)`.

## Visual design
Full component spec: [../design-system.md](../design-system.md) §7.7 (rail) + §7.8 (`StockRow`).
Tokens/radius from design-system (card 16px, pill 999px, `VISUAL_DENSITY:6`).

- **Rail** 300–320px, `--surface` on `--bg`, `1px --border` left divider, toggle from top-bar `☆`.
  Header: `● Watchlist` + ✕. **Market tabs** (TW/US/ETF) = segmented pill row, active `--accent`.
- **Groups.** Collapsible header (▾ + name + count badge in `--surface-2` pill); `+ 新增分組` ghost row.
  Grouping by `divide-y --border`, not boxes.
- **`StockRow`** (§7.8): `⠿` grip (`--text-muted`, drag → 50% opacity) · single **candlestick cell**
  (紅漲綠跌 body) · `代號 名稱` (no mid-token break) · `SectorChip` (sector-token muted tint + hairline) ·
  tabular price (`--font-num`) · **▲▼ + %** in `--up`(漲)/`--down`(跌) · hover-reveal mini sparkline
  (accent stroke, up/down-tinted fill) + ✕ remove (`opacity-0 group-hover:opacity-100`).
- **Category extras** degrade gracefully: 多空 `ScoreBandDot` (偏多/中性/偏空) · ETF 折溢價% / 殖利率 ·
  處置 `--warn` flag chip. sponsor extras (分點/主動ETF) → 馬賽克 lock for Free.
- **States.** Live tick → row bg flashes `--up-weak`/`--down-weak`. Loading → shimmer rows. Empty
  group → 「尚無股票，點 ☆ 或問 Miru 加入」; empty tab → seeded with the preset above. Drag = `@dnd-kit`
  sortable; reduced-motion → no drag transition, keyboard reorder (Space lift, ↑/↓, aria-live announce).
- 紅漲綠跌 + ▲▼ + tabular-nums throughout; `--accent` teal is chrome-only, never a price.

## Governance
Public user data, non-trading, ungated CRUD (soft caps only); live-quote column inherits Premium+
(EOD free / live-ish sponsor). 紅漲綠跌+▲▼, tabular-nums, reduced-motion (keyboard reorder).
