# 大盤 › ETF (section)

Parent: [../market.md](../market.md). Row-1 market tab (asset-class facet `(TW, etf)`, per
[ADR-0027](../../backend/adr/0027-first-class-watchlist.md)) — its own section.

## Blocks
| Block | Chart / content | Component | Endpoint | Access |
|-------|-----------------|-----------|----------|--------|
| 主動式ETF 追蹤 | 新增/加碼/減碼/移出 per-ETF daily-change table | `ActiveEtfDaily` | `get_active_etf_report` | 🔒 |
| 主動買賣統計 (跨ETF) | 個股 × N家ETF chips × 權重變動 (買進區/賣出區 tabs) | `ActiveEtfDaily` (cross) | `get_etf_money_flow` | 🔒 |
| ETF 資金流向 | 流入/流出排行, 多空共識 買賣檔數 | (board) | `get_etf_money_flow` | 🔒 |
| ETF 折溢價 | diverging-bar 折溢價% + 股價/淨值 dual-line + sheet | `EtfPremiumHistory` | `get_etf_nav_premium` | pub |
| 受益人數 / AUM | trend + size | `EtfBeneficiaries`/`get_etf_aum` | `get_etf_beneficiaries`, `get_etf_aum` | pub |

主動ETF holdings/操作 + 資金流向 = **sponsor → 馬賽克** for Free; 折溢價/受益人數/AUM public. Non-trading,
紅漲綠跌, tabular-nums. Per-ETF detail = the ETF-variant tabs in [../stock-detail.md](../stock-detail.md) §3.

## Visual design
Tokens/radius from [../design-system.md](../design-system.md) (card 16px, pad 20px). Cards `--surface`
+ `1px --border` + tinted shadow.

- **主動式ETF 追蹤 / 主動買賣統計** (`ActiveEtfDaily`) — 新增/加碼/減碼/移出 daily-change table +
  cross-ETF 個股 × N家ETF 權重變動 chips (買進區/賣出區 segmented tabs); +變動 `--up` / −變動 `--down`
  + ▲▼. **Sponsor → 馬賽克** (ETF/date columns sharp, values blur + lock chip + 升級 CTA).
- **ETF 資金流向** — 流入/流出 ranked board, 多空共識 買賣檔數; sponsor → 馬賽克.
- **ETF 折溢價** (`EtfPremiumHistory`) — `ChartWithDataSheet`: **diverging-bar 折溢價%** around zero
  (溢價 `--up` / 折價 `--down` + ▲▼) + 股價/淨值 dual-line (`--chart-1`/`--chart-2`), + sheet. Public.
- **受益人數 / AUM** (`EtfBeneficiaries`/`get_etf_aum`) — trend line (`--chart-1`) + size stat; public.
- Signed cells `--up`/`--down` + ▲▼ never color-alone; `<Caveat>` footer; loading = chart+row shimmer.
