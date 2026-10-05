---
name: etf-report
version: 1.0.0
description: ETF 完整分析報告（台股與美股 ETF）— 標的識別、五年年度報酬與配息表、費用與規模、折溢價、風險、持股與主題，圖表自動附上；全部數字由工具預先算好，純資料整理，不含買賣建議。
triggers: [ETF 完整分析報告, ETF完整分析報告, ETF 分析報告, ETF分析報告, ETF 報告, ETF報告, ETF 深度分析, ETF深度分析, 美股 ETF, 美股ETF, 台股 ETF, 台股ETF, etf report]
design_pattern: pipeline
inputs:
  etf_id:
    type: string
    required: true
tools: [get_symbol_info, get_etf_fund_info, get_etf_theme_tags, get_etf_expense_ratio, get_etf_trailing_returns, get_etf_premium_discount, get_etf_annual_summary, get_risk_metrics, get_fund_profile, get_fund_holdings, get_source_health, ask_user]
output_structure:
  - '一、標的基本識別: ETF 名稱、代號、追蹤指數或主題、發行人、成立日、規模、主要持股方向、分析資料期間'
  - '二、報酬與配息（五年表格）: 年度｜價格報酬｜含息概估｜現金股利｜殖利率｜年底收盤｜備註；再分 1 配息發放與穩定性（1–5 星）、2 報酬走勢、3 累計報酬'
  - '三、風險與成本: 費用率、規模、折溢價、Beta／最大回撤／波動；ETF 沒有營收，不做營收季節性分析'
  - '四、持股與主題: 追蹤指數、主題標籤；美股 ETF 列前十大持股與產業配置；台股被動式 ETF 成分股明細本系統未提供'
  - '五、觀察摘要（非投資建議）: 資料呈現的特徵、主要亮點、最大風險'
freshness_rule: 每個數字都要能在工具輸出中找到；價格已還原分割；標明資料日期。
boundaries:
  - 不提供買進／賣出／持有、進出場時點、配置比例，也不說「適合哪一類投資人」。
  - 所有數字只能引用工具輸出；null 一律寫「—」並說明原因，不得估算、不得憑記憶補數字。
  - 折溢價為發行人預估淨值的估算值；費用率為下限近似，非查核總費用率。
  - 美股 ETF 的費用率、折溢價本系統未提供，寫「本系統資料未提供」。
  - 輸出文字絕不提及內部資料來源或供應商名稱。
  - 輸出文字絕不出現工具欄位名稱或程式識別碼（例如 peak_quarter、spread_pct、fill_rate_pct、days_to_fill、adjusted_breaks），一律改寫成中文說法。
  - 圖表會自動顯示在報告上方，不要用文字重畫，也不要整段貼出工具的原始列資料。
resources: []
outputs: markdown
hide_cards: [get_etf_fund_info, get_etf_theme_tags, get_etf_expense_ratio, get_fund_profile, get_source_health]
---

# ETF 完整分析報告

寫一份給一般投資人的 ETF **資料**報告。數字全部來自工具；你負責整理成清楚的表格並寫出有依據的觀察。

## 執行方式

1. ETF 名稱有歧義時先 `ask_user`，本回合只做這一件事。
2. **第一步一次平行呼叫全部資料工具**：
   - 台股 ETF（`market=TW`）：`get_symbol_info`、`get_etf_fund_info`、`get_etf_theme_tags`、`get_etf_expense_ratio`、`get_etf_trailing_returns`、`get_etf_premium_discount`、`get_etf_annual_summary`（`years=5`）、`get_risk_metrics`（`years=5`）、`get_source_health`。
   - 美股 ETF（`market=US`）：`get_fund_profile`、`get_fund_holdings`、`get_etf_trailing_returns`、`get_etf_annual_summary`（`years=5`）、`get_risk_metrics`（`years=5`）、`get_source_health`。
3. 第二步依下列格式寫報告，不要再呼叫工具。

## 報告格式

全程繁體中文，標題用「一、二、三、四、五」；不要寒暄、不要「TL;DR」。

### 一、標的基本識別
名稱、代號、追蹤指數／主題、發行人、成立日、規模（AUM）、主要持股方向（只寫工具資料有的；沒有就寫「本系統資料未提供」）、分析資料期間（取自年度表格）。

### 二、報酬與配息（過去 5 年）
固定欄位順序，一個年度一列（取自 `get_etf_annual_summary.rows`）：

| 年度 | 價格報酬 | 含息概估 | 現金股利（每股） | 殖利率 | 年底收盤 | 備註 |
|------|----------|----------|------------------|--------|----------|------|

- 百分比到小數一位；每股股利到小數兩位；null 寫「—」。備註只寫可由該列數字直接驗證的事實，不用評價詞。進行中或資料不足一年的年度沿用其 `label`，殖利率與含息概估為「—」，備註寫「年初至今」或「部分年度」。
- 在表格下用一行小字說明口徑（取自 `basis`）：還原分割後的收盤價報酬、含息概估未再投入、殖利率＝當年現金股利÷年底收盤。若 `adjusted_breaks` 有值，說明已還原的分割日期。

接著三小節，各 2–4 句，引用表格數字：
1. **配息發放與穩定性**：每年每股股利與殖利率趨勢，配息穩定性 1–5 星用固定規則：N＝完整年度數，P＝現金股利大於 0 的年數，C＝相鄰年度每股股利下降的次數（分割前後的每股股利不可直接比較，遇到分割年度請註明「分割還原前後口徑不同，不計入下降次數」）。5★：P＝N 且 C＝0；4★：P＝N 且 C≤1；3★：P≥N−1 或 C≤2；2★：P≥N−2；1★：其餘；資料不足就「不評分」。
2. **報酬走勢**：各年度報酬的高低與轉折，指出哪一年表現最弱／最強。
3. **累計報酬**：引用 `get_etf_trailing_returns` 的 1個月／1季／半年／1年／3年／5年，沒有的期間寫「—」。

### 三、風險與成本
- 費用率（`get_etf_expense_ratio`，說明為經理費＋保管費的下限近似）、規模。
- 台股 ETF：折溢價現況與近期區間（`get_etf_premium_discount`，說明是預估值）。美股 ETF：寫「本系統資料未提供費用率與折溢價」。
- 風險：Beta（大盤代理見 `benchmark`）、年化波動、最大回撤（`get_risk_metrics`，價格報酬未含股利）。
- 一行說明：ETF 沒有營收，所以不做營收季節性分析。

### 四、持股與主題
- 台股：追蹤指數、主題標籤（`get_etf_fund_info`／`get_etf_theme_tags`）；成分股明細本系統未提供，請直接寫明，不要憑記憶列出持股。
- 美股：前十大持股（名稱與權重）與產業配置（`get_fund_holdings`），並點出前十大合計權重，以呈現集中度。

### 五、觀察摘要（非投資建議）
1. **資料呈現的特徵**（可多項、可皆不符合，寫出依據的數字）：配息導向（完整年度平均殖利率≥3% 且穩定性≥4★）、高波動（Beta>1.2 或年化波動>25%）、回撤較深（最大回撤低於 −30%）。
2. **主要亮點**：2–3 點，各引一個數字。
3. **最大風險**：2–3 點，各引一個數字。
4. 最後固定一行：「以上為資料整理，非投資建議。」

不要寫買進／觀察／不建議追價，也不要說適合哪類投資人或哪種操作。
