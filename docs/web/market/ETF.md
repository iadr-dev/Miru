1	# 大盤 › ETF (section)
2	
3	Parent: [../market.md](../market.md). Row-1 market tab (asset-class facet `(TW, etf)`, per
4	[ADR-0027](../../backend/adr/0027-first-class-watchlist.md)) — its own section.
5	
6	## Blocks
7	| Block | Chart / content | Component | Endpoint | Access |
8	|-------|-----------------|-----------|----------|--------|
9	| 主動式ETF 追蹤 | 新增/加碼/減碼/移出 per-ETF daily-change table | `ActiveEtfDaily` | `get_active_etf_report` | 🔒 |
10	| 主動買賣統計 (跨ETF) | 個股 × N家ETF chips × 權重變動 (買進區/賣出區 tabs) | `ActiveEtfDaily` (cross) | `get_etf_money_flow` | 🔒 |
11	| ETF 資金流向 | 流入/流出排行, 多空共識 買賣檔數 | (board) | `get_etf_money_flow` | 🔒 |
12	| ETF 折溢價 | diverging-bar 折溢價% + 股價/淨值 dual-line + sheet | `EtfPremiumHistory` | `get_etf_nav_premium` | pub |
13	| 受益人數 / AUM | trend + size | `EtfBeneficiaries`/`get_etf_aum` | `get_etf_beneficiaries`, `get_etf_aum` | pub |
14	
15	主動ETF holdings/操作 + 資金流向 = **sponsor → 馬賽克** for Free; 折溢價/受益人數/AUM public. Non-trading,
16	紅漲綠跌, tabular-nums. Per-ETF detail = the ETF-variant tabs in [../stock-detail.md](../stock-detail.md) §3.
17	
18	## Visual design
19	Tokens/radius from [../design-system.md](../design-system.md) (card 16px, pad 20px). Cards `--surface`
20	+ `1px --border` + tinted shadow.
21	
22	- **主動式ETF 追蹤 / 主動買賣統計** (`ActiveEtfDaily`) — 新增/加碼/減碼/移出 daily-change table +
23	  cross-ETF 個股 × N家ETF 權重變動 chips (買進區/賣出區 segmented tabs); +變動 `--up` / −變動 `--down`
24	  + ▲▼. **Sponsor → 馬賽克** (ETF/date columns sharp, values blur + lock chip + 升級 CTA).
25	- **ETF 資金流向** — 流入/流出 ranked board, 多空共識 買賣檔數; sponsor → 馬賽克.
26	- **ETF 折溢價** (`EtfPremiumHistory`) — `ChartWithDataSheet`: **diverging-bar 折溢價%** around zero
27	  (溢價 `--up` / 折價 `--down` + ▲▼) + 股價/淨值 dual-line (`--chart-1`/`--chart-2`), + sheet. Public.
28	- **受益人數 / AUM** (`EtfBeneficiaries`/`get_etf_aum`) — trend line (`--chart-1`) + size stat; public.
29	- Signed cells `--up`/`--down` + ▲▼ never color-alone; `<Caveat>` footer; loading = chart+row shimmer.
30	