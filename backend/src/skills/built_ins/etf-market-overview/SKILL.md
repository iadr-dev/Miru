---
name: etf-market-overview
version: 1.0.0
description: 台股 ETF 全市場總覽 — 全部上市 ETF 的價格、漲跌、成交量、近一週報酬、規模、費用率下限與近 12 個月配息率，以及某月份的 ETF 配息日曆 (除息日、最後買進日、發放日、現金股利)。描述性清單，不做推薦。
triggers: [ETF清單, ETF總覽, 全部ETF, ETF排行, ETF規模, ETF配息日曆, ETF除息, 本月除息ETF, 本月配息ETF, 除息日曆, etf list, etf universe, etf dividend calendar, ex-dividend etfs]
design_pattern: tool_wrapper
inputs:
  market:
    type: string
    required: false
  trade_date:
    type: string
    required: false
  year:
    type: integer
    required: false
  month:
    type: integer
    required: false
tools: [get_etf_list, get_dividend_calendar]
output_structure:
  - 'ETF 清單: 代號、名稱、價格、漲跌、成交量、近一週報酬、規模、費用率下限、近 12 個月配息率與配息頻率，標明交易日'
  - '配息日曆: 指定月份內每檔 ETF 的除息日、最後買進日、發放日與現金股利，依日期排序'
  - '資料範圍: 交易日或月份，以及缺少欄位的說明'
freshness_rule: ETF 清單讀取所選交易日（預設最新可用日）的收盤後資料，不是盤中即時；配息日曆為指定月份（預設當月）內已公布的事件，須標明月份，尚未公布的不得推測。
boundaries:
  - 清單與日曆只做描述與整理，不得依配息率、報酬或規模排出「最佳 ETF」或建議買哪一檔；若使用者要排序，只說明依哪個欄位排序。
  - 近 12 個月配息率是過去已發放配息的統計，不代表未來配息；不得外推或保證。
  - 配息日曆只列已公布日期與金額，不推論除息後的填息或價格表現。
  - 單一 ETF 的完整分析請改用 etf-report，兩檔比較請改用 etf-compare，3 檔以上非台股比較請用 etf-overlap-and-comparison；主動式 ETF 的持股異動請用 active-etf-flow。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# ETF market overview (descriptive, non-trading)

## Procedure

1. For "list the ETFs / which ETFs are large or active", call `get_etf_list(market, trade_date)`.
   It returns the whole listed-ETF table for one trading day, so filter and sort in the answer by
   the column the caller asked about, and say which column and day you used. Keep the list short
   (top rows) unless the caller asks for more.
2. For "which ETFs go ex-dividend this month / when is the payment date", call
   `get_dividend_calendar(market, year, month)` (defaults to the current month). List the ex-dividend
   date, last-buy date, payment date and cash amount per event, in date order.
3. State the trading day or month used. If the table or calendar is empty, relay the reason
   rather than implying that no ETF qualifies.
4. Keep the tone descriptive. Do not rank ETFs as better or worse, and do not predict future
   distributions or price behavior around ex-dividend dates.
