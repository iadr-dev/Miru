# 個股詳情 tabs — folder index

Per-tab design docs for the Stock Detail page. **Full page overview** (shell, AssetHeader, tab-set,
ASCII, governance): [../stock-detail.md](../stock-detail.md). Shared visual/interaction language:
[../design-system.md](../design-system.md) · [../interaction-spec.md](../interaction-spec.md).

Every tab doc carries: components → real backend endpoint · a **Visual design** section (tokens,
layout, `ChartWithDataSheet` per view) · governance (紅漲綠跌+▲▼, tabular-nums, non-trading `<Caveat>`,
sponsor→馬賽克, dark-first).

## TW-stock tabs (8 + 即時)

| Tab | Doc | Headline content |
|-----|-----|------------------|
| 基本資料 | [基本資料.md](基本資料.md) | 個股資訊卡 · 財務快照 · 內部人 · 公告/事件 · 被N支ETF持有 · ESG |
| 產業分析 | [產業分析.md](產業分析.md) | 同業比較 · 供應鏈圖 · 多空訊號 · 產業鏈資金流🔒 |
| 財務分析 | [財務分析.md](財務分析.md) | 股利 · 營收+YoY · 三率+EPS · 財務結構 · 現金流 · 自結 · 法說 |
| 籌碼分析 | [籌碼分析.md](籌碼分析.md) | 法人 · 融資券 · 借券 · 分點🔒 · 股權分散 |
| 技術分析 | [技術分析.md](技術分析.md) | K線 + MA/KD/MACD/RSI/布林 + 支撐壓力/N波 |
| 智能摘要 | [智能摘要.md](智能摘要.md) | 多空 gauge + factor bars **+** AI 摘要/SWOT (含多空評分) |
| 相關新聞 | [相關新聞.md](相關新聞.md) | 即時 / 財經 / PTT / 名人 |
| 研究圖表 | [研究圖表.md](研究圖表.md) | 估值河流 · 分析師深度 · 個股事件calendar · 除權息 |
| 即時 (Premium) | [即時.md](即時.md) | best-5 ladder + tick/分K + 當沖率 🔒 |

**TW-ETF variant tabs** (概覽/智能摘要/持股·操作🔒/折溢價/技術/新聞) are described in
[../stock-detail.md](../stock-detail.md) §3 + the shared components (`EtfPremiumHistory`, `ActiveEtfDaily`).
