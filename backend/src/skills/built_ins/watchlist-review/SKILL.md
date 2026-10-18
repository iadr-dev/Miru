---
name: watchlist-review
version: 1.0.0
description: 自選股回顧 — descriptive review of the signed-in user's OWN watchlist - how the stocks moved lately, upcoming ex-dividend / calendar events, and the cached descriptive factor score. Facts only, never a recommendation.
triggers: [我的自選股表現, 自選股表現, 自選股漲跌, 自選股今天, 自選股最近, 自選股股利, 自選股除息, 自選股配息, 自選股財報, 自選股法說, 自選股事件, 自選股行事曆, 自選股評分, 自選股回顧, 看一下我的自選, 我的自選股怎麼樣, how is my watchlist, my watchlist performance, watchlist dividends, watchlist earnings, watchlist review, watchlist calendar, dividends coming up]
design_pattern: pipeline
inputs:
  watchlist_name:
    type: string
    required: false
tools: [get_my_watchlists, get_symbol_quotes, get_price_history, get_dividend_calendar, get_calendar_events, get_stock_score]
output_structure:
  - '先說明範圍: 哪個群組、幾檔（超過 20 檔時只看前 20 檔並照實說明）；清單為空或未登入時用一句話說明即停止'
  - '近期表現: 以 get_symbol_quotes 取得的最新收盤與當日漲跌幅逐檔簡述（收盤資料，非即時）；使用者問近期走勢時再用 get_price_history 取近 2-60 個交易日，描述已發生的價量'
  - '即將到來的事件: 以 get_calendar_events 逐檔查詢未來一段期間的除權息等事件，ETF 另可看 get_dividend_calendar 當月配息日曆；查無事件就說「查無已公布的事件」'
  - '描述性評分 (使用者要求時): get_stock_score 的快取評分與區間，註明批次日期與缺漏的因子'
  - '每個段落標明資料日期與來源的時效說明'
freshness_rule: 自選股清單是呼叫當下的帳戶資料；行情為收盤資料（非即時），評分為夜間批次快取，事件為已公布的行事曆。每一段都須標明資料日期，絕不把收盤資料說成即時。
boundaries:
  - 只描述已發生的價量與已公布的事件；不得預測後續走勢，不得給買進／賣出／持有、目標價、停損或部位大小的建議，也不得暗示哪一檔「值得買」。
  - 評分與五因子是描述性因子脈絡，不是訊號；偏多／中性／偏空只描述目前因子組合。
  - 一次最多檢視 20 檔（get_symbol_quotes 的上限）；清單更長時請使用者指定群組或挑選標的，不得為了湊滿而掃描其他標的。
  - 只看使用者自己的自選股；不得查詢、推測或透露其他使用者的資料。使用者尚未登入時（工具要求登入）平靜說明「登入後才能查看你自己的自選股」並停止。
  - 清單為空就直說為空；查無事件、查無評分就照實說明，不得捏造日期、金額或數字，也不得以市場資料替代使用者的清單。
  - 事件欄位只轉述工具回傳的日期與金額；get_dividend_calendar 只涵蓋台股 ETF，個股的財報／法說日期若工具沒有提供，就說明目前查不到，不得憑記憶補上。
resources: []
outputs: markdown
---

# Watchlist review (descriptive, non-trading)

## Procedure

1. Call `get_my_watchlists` (use `market` or the user's named group to narrow). If it says the
   user must sign in, or the list is empty, say so in one sentence and stop.
2. Take at most 20 symbols from one market (the user's chosen group, or the first group asked
   about) and call `get_symbol_quotes(symbols, market, days)` once: latest close, daily change %
   and volume per stock. Describe moves in plain words; these are closing figures, not live
   prices. For "how has it traded lately" call `get_price_history` (2-60 days) and describe what
   happened, nothing about what comes next.
3. For upcoming events call `get_calendar_events` per symbol (a bounded forward range, e.g. the
   next 30-60 days) and relay dates and amounts as returned. For ETFs, `get_dividend_calendar`
   shows the month's ex-dividend, last-buy and payment dates. If a stock has no event, say
   "查無已公布的事件" rather than guessing; earnings dates only appear when a tool returns them.
4. Only when the user asks for it, `get_stock_score` for the named stocks: report the cached score,
   its descriptive band and `as_of` date, and name any missing factor. It describes the factor mix;
   it is not a signal.
5. Close with the data dates and that nothing here is investment advice. No ranking of which stock
   to buy or sell.
