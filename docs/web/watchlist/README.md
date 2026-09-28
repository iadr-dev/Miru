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

## Default presets
Server-seeded once per user, per `(market, asset_class)` combo — `backend/src/config/
watchlist_presets.py::DEFAULT_WATCHLIST_PRESETS`, seeded on first `GET /watchlists` for a combo
with zero non-deleted groups; never re-seeds over a user's own edits/deletions.

| Tab | Group | Symbols |
|-----|-------|---------|
| TW | SpaceX 供應鏈 | 3665 貿聯-KY, 3491 昇達科, 2455 全新, 4979 華星光, 2059 川湖, 6141 柏承, 3081 聯亞, 6213 聯茂 |
| TW | 軍工&無人機 | 2634 漢翔, 1503 士電, 2371 大同, 4551 智伸科, 1522 堤維西, 4526 東台 |
| TW | 記憶體 | 2408 南亞科, 8299 群聯, 5347 世界, 4919 新唐, 3532 台勝科, 8054 安國, 6533 晶心科 |
| TW | 金融&航空 | 2882 國泰金, 2881 富邦金, 2891 中信金, 2886 兆豐金, 2884 玉山金, 2885 元大金, 2610 華航, 2618 長榮航 |
| TW | 半導體 | 2330 台積電, 2454 聯發科, 3711 日月光投控, 2303 聯電, 3661 世芯-KY, 3529 力旺, 3034 聯詠, 3413 京鼎 |
| US | AI Infrastructure & Data Centers | NVDA, AVGO, MU, VRT, SMCI, ANET, ETN, VST, CEG, ORCL |
| US | Nuclear Power & SMRs | CEG, VST, NRG, OKLO, SMR, BWXT, CCJ, LEU, GEV |
| US | Quantum Computing | IONQ, RGTI, QBTS, IBM, GOOGL, MSFT, HON |
| US | Defense & Drones | LMT, RTX, NOC, GD, KTOS, AVAV, RCAT, PLTR |
| US | Stablecoins & Crypto Infrastructure | COIN, CRCL, MSTR, MA, V, PYPL, XYZ, HOOD |
| ETF | Active ETF | 00981A, 00403A, 00991A, 00982A, 00992A, 00406A, 00400A |
| ETF | Leveraged ETF | 00631L, 00685L, 00675L, 00632R, 00663L, 00670L, 00637L |
| ETF | Popular ETF | 0050, 0056, 00878, 00919, 006208, 0052, 00881 |

## Endpoints
`GET /watchlists?market=` (seeds presets when empty) · POST/PATCH/DELETE group · `PUT/DELETE
/watchlists/{id}/items/{symbol}` (idempotent star) · reorder/move. Batch quotes reuse `/quote(/stream)`.

## Visual design
Full component spec: [../design-system.md](../design-system.md) §7.7 (rail) + §7.8 (`StockRow`).
Tokens/radius from design-system (card 16px, pill 999px, `VISUAL_DENSITY:6`).

- **Rail** 240–480px (user-resizable via a left-edge drag handle, persisted; dragging past the
  min width snaps the rail closed), `--surface` on `--bg`, `1px --border` left divider, toggle
  from top-bar `☆`. Header: `● Watchlist` + ✕. **Market tabs** (TW/US/ETF) = segmented pill row,
  active `--accent`.
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
Public user data, non-trading, ungated CRUD (soft caps only, all tiers) — presets are seeded
identically for Free/Premium/Pro, no tier gate anywhere in the seeding path; live-quote column
inherits Premium+ (EOD free / live-ish sponsor). 紅漲綠跌+▲▼, tabular-nums, reduced-motion
(keyboard reorder).
