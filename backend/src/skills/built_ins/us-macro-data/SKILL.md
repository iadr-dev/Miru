---
name: us-macro-data
version: 1.0.0
description: 美國總經資料 — 儀表板（殖利率曲線、通膨、就業、信用、政策利率、成長、房市、美元、貨幣供給）、單一總經序列歷史、經濟數據發布時程與最新值、數據修正紀錄，以及含預期值的全球總經事件日曆。描述性資料，不預測也不給進出場建議。
triggers: [美國總經, 美國通膨, CPI, PCE, 非農, 失業率, 初領失業金, 殖利率曲線, 殖利率倒掛, 衰退, 聯準會, Fed, 利率決策, 資產負債表, GDP, 經濟數據, 數據發布, 數據修正, 總經日曆, 本週數據, US macro, yield curve, inflation, payrolls, jobless claims, economic calendar, data release, revision]
design_pattern: tool_wrapper
inputs:
  series_id:
    type: string
    required: false
  from_date:
    type: string
    required: false
  to_date:
    type: string
    required: false
tools: [get_us_macro_dashboard, get_us_macro_series, get_us_economic_releases, get_series_revisions, get_macro_calendar]
output_structure:
  - '總經儀表板: 分組面板 (殖利率曲線與衰退、通膨、就業、信用與金融狀況、政策利率與流動性、成長、房市、美元、貨幣供給)，每個序列列最新值、前值、變動、最後觀測日與下次發布日'
  - '單一序列: 指標名稱、單位、頻率、是否季調，以及所選期間的歷史數值與最新讀值'
  - '數據發布: 近期與即將發布的經濟數據 (名稱、發布日、涵蓋序列、最新值、前值、變動)；此工具不含預測或市場共識'
  - '事件日曆 (含預期值): 事件、時間、重要性，以及預期值／前值／實際值 (若有)'
  - '數據修正: 各期首次公布值、最新值、修正幅度與中間各次數值'
  - '資料日期: 每個數字附觀測日期；單位與頻率一律照工具回傳'
freshness_rule: 總經序列有發布時間差，每個數值須標明其觀測日期與（若有）下次發布日，不能把月資料說成當下；事件日曆的「預期值」是第三方彙整的共識，與已公布的實際值分開陳述；經濟數據發布清單本身沒有預測值。
boundaries:
  - 只描述已發布的數據與已公布的日程，不預測下一次數據、利率決策或衰退，也不把殖利率倒掛等現象包裝成買賣訊號。
  - get_us_economic_releases 不含預測或共識；需要預期值時改用 get_macro_calendar，並標明預期值來自第三方彙整。
  - 修正紀錄只描述數字如何被修正，不推論原因或政策意涵。
  - 序列代碼必須取自工具支援的清單，不憑記憶捏造代碼；序列不存在或期間無資料時照實說明。
  - 與 macro-dashboard 的分工：台灣景氣對策信號、匯率、黃金、原油、Fear & Greed 屬於 macro-dashboard；本技能專注於美國官方總經序列與全球事件日曆。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# US macro data (descriptive, non-trading)

## Procedure

1. For "how is the US economy / macro overview", call `get_us_macro_dashboard()`. Summarize each
   panel's latest value, prior value and change with the observation date and the next release
   date; do not combine panels into a single verdict.
2. For one indicator's history (yields and spreads, inflation, unemployment and payrolls,
   jobless claims, policy rates, balance sheet, GDP, housing, dollar, money supply), call
   `get_us_macro_series(series_id, units, frequency, start_date, end_date, limit)`. Report its
   units, frequency and seasonal adjustment from the returned metadata.
3. For "what data comes out this week / what was released", call `get_us_economic_releases(
   from_date, to_date)`: release name, date, series, latest value, previous value and change. It
   carries no forecasts.
4. For "what is the consensus / when is the Fed decision", call `get_macro_calendar(impact,
   days_ahead, from_date, to_date)`: GDP, CPI, PCE, PMI, jobs report and central-bank rate
   decisions with consensus, prior and actual. Keep expected, prior and actual values apart.
   When `health_status` is `unreachable` (or any non-`ok` tier) an empty `events` means the
   calendar source could not be reached, not that nothing is scheduled - say it is temporarily
   unavailable, never "no events".
5. For "was the number revised", call `get_series_revisions(series_id)`: first release, latest
   value, revision size and the values in between.
6. Present only what the tools returned, each with its date. If a series or window is empty,
   relay the reason and stop there.
