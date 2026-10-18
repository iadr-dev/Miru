<div align="center">

<img src="web/public/logo.png" alt="Miru" width="120" />

# Miru

**你的台股、美股與 ETF AI 助理：看懂市場，每個回答都附上資料來源。**

[![Backend CI](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-f2873a.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/node-22+-339933?logo=node.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-App%20Router-000000?logo=next.js&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-read--only-6E56CF)

[English](README.md) | **繁體中文**

</div>

<!-- 宣傳影片 -->
https://github.com/user-attachments/assets/654c2090-b586-4b1f-b166-96f91d4184f0

> [!NOTE]
> Miru 提供的是**研究參考脈絡，非投資建議**。它不提供交易指示、價格預測或部位建議；
> 每個回答都標明資料來源與資料時點。

## ✨ 為什麼是 Miru

市場每天都在說話：新聞、漲跌、資金流向、財報、傳聞。你只要用一句話提問，Miru 會整理相關資料，
**先給重點摘要，再列出佐證**，每個數字都能追溯到來源。

名字來自日文「見る（miru，看見）」。Miru 想做的是幫你看清市場，而不是替你行動。

## 🧭 功能

| | |
|---|---|
| 💬 **用一句話提問** | 會規劃、會呼叫資料工具的對話代理，以 SSE 串流回覆並附上來源；可停止、重新產生、編輯後重送，也能附加圖片或文件 |
| 🌏 **台股＋美股** | 價量、法人籌碼、融資融券、基本面、ETF 成分股與淨值、衍生性商品、除權息與公司事件、新聞；資料路由涵蓋台、美與全球共 150 項資料能力 |
| 📊 **個股與 ETF 頁** | 圖表、技術指標、描述性的多因子「多空分數」、ETF 比較與重疊度、本益比河流圖 |
| 🔎 **選股與題材** | 標籤選股、市場廣度與排行；概念題材（CoWoS、HBM、低軌衛星等）有題材頁與市場「題材」分頁，另有產業鏈 |
| 🕸️ **供應鏈與關聯** | 供應鏈關係圖：每一條供應商、客戶與同業連線都標示可信度、資料日期與可點擊的來源；公司在產業鏈中的位置（上游、中游、下游）附判定依據，證據不足時顯示「尚未分類」；另有關聯圖譜，呈現公司被一併提及的技術、材料與其他公司 |
| 👁️ **AI 盯盤** | 五種規則式盯盤策略（波動率擠壓、超跌反彈、量價背離、籌碼集中盤整、強勢整理）：每天 08:00 選股，盤中每 10 分鐘檢查條件；你訂閱的條件成立時，會透過你設定的管道通知。提供的是描述性脈絡，不是交易訊號 |
| 🏛️ **美國總經** | 殖利率曲線（標示景氣衰退區間），以及通膨、就業、信用、流動性、成長、房市與貨幣供給等指標，並有「經濟數據發布」清單，列出最新值與前值（需免費的 `FRED_API_KEY`） |
| 🧩 **41 個內建技能** | 封裝好的分析流程：台股／美股個股報告、ETF 報告、晨報、籌碼回顧、財報品質、總經儀表板等 |
| 🔬 **深度研究**（Pro） | 在一次對話中進行有預算上限、多步驟、附結構化引用的網路研究 |
| 🔔 **提醒與定時簡報** | 由你自己授權的規則與簡報，可送到 LINE、Telegram、Email 或網頁推播 |
| 🎭 **Miru 的語氣** | 四種語氣（穩健派・白話派・簡潔派・數據派）：同一個引擎、四種說話方式，不是四位不同的顧問 |
| 🧠 **記憶** | 對話與長期記憶，可在設定中檢視與編輯 |
| 🔌 **自選模型** | 不綁定供應商的 LLM 介面：Anthropic、OpenAI、Gemini、OpenRouter，或透過 Ollama 使用本地模型；支援 BYOK |
| 🛰️ **MCP 伺服器** | 同一套工具以唯讀方式透過 MCP（streamable HTTP）提供，讓其他代理也能使用；可作為 Claude 連接器：claude.ai 自訂連接器、Messages API 的 MCP 連接器，或 Claude Code |
| 🌐 **雙語** | 繁體中文優先，全站提供英文（`web/messages/`） |

## 🏗️ 運作方式

```
Next.js 網頁  ──REST + SSE──▶  FastAPI  ◀──MCP──  其他代理
                                  │
                  協調器 · 對話 · 記憶 · 技能
                                  │
                    代理執行環境（迴圈 · 規劃 · 護欄）
                       │                       │
                  LLM 介面                 工具註冊表（唯讀）
                 （7 家供應商）                 │
                                     脈絡管線（附註與來源）
                                               │
                         資料存取路由（市場, 能力）→ 資料來源
                                               │
              FinMind · TWSE · TPEx · TAIFEX · MOPS · TDCC · yfinance · Finnhub · FRED · 新聞
```

- **單向相依**：下層永遠不匯入上層，只有 `sources/` 會連網。
- **市場資料與應用狀態分開存放**：市場資料存放於 DuckDB／Parquet 與 Redis，只有一個匯入程序寫入，讀取端可以有許多個；
  應用狀態存放於 Postgres（開發時可用 SQLite）。
- **治理即程式碼**：非交易輸出檢查、Sponsor 資料遮罩、附註與不可信內容規則，全部都有治理測試
  （`pytest -m governance`），詳見
  [`docs/backend/modules/25-governance-enforcement.md`](docs/backend/modules/25-governance-enforcement.md)。

## 🚀 快速開始

**需要**：Docker、Node 22+ 與 pnpm、Python 3.13 與 [uv](https://docs.astral.sh/uv/)（只有不使用 Docker 時才需要 uv）。

```bash
git clone https://github.com/iadr-dev/Miru.git
cd Miru

# 1. 機密設定：複製範本，至少填入一組 LLM 金鑰與 FINMIND_API_KEY
cp backend/.env.example backend/.env

# 2. 後端：api :8000 · mcp :8100 · scheduler · ingest · Postgres · Redis
./scripts/dev-backend.sh --backfill   # --backfill 會在全新的 volume 上先回補市場資料（先台股、再美股）

# 3. 網頁：http://localhost:3000
./scripts/dev-frontend.sh
```

<details>
<summary><b>輕量模式：只跑 API（SQLite，不需 Docker）</b></summary>

```bash
cd backend
uv sync
uv run uvicorn api.app:app --reload
```

使用行程內事件匯流排，不啟動 scheduler、ingest 與 MCP。
</details>

<details>
<summary><b>連接 MCP 用戶端</b>（Claude、Gemini CLI、Codex、Cursor、VS Code）</summary>

唯讀 MCP 端點位於 `http://localhost:8100/mcp`（streamable HTTP），存活檢查在 `/healthz`。
它是獨立的程序，與 API 分開執行。用戶端以你的 Miru 帳號登入：按下用戶端的「Sign in」或 `/mcp` 按鈕，
瀏覽器會開啟 Miru 的授權頁面（需要時先登入，再按「允許」或「拒絕」），工具回傳的內容依你的方案而定。
只有在本機開發、不需登入時才設定 `MCP_REQUIRE_AUTH=false`。細節見
[docs/backend/mcp-claude-connector.md](docs/backend/mcp-claude-connector.md)。

| 用戶端 | 加入伺服器（`http://localhost:8100/mcp`） |
|---|---|
| Claude Code | `claude mcp add --transport http miru http://localhost:8100/mcp` |
| Gemini CLI | `gemini mcp add --transport http miru http://localhost:8100/mcp` |
| Codex CLI | `codex mcp add miru --url http://localhost:8100/mcp` |
| Cursor | `~/.cursor/mcp.json`：`{"mcpServers": {"miru": {"url": "http://localhost:8100/mcp"}}}` |
| VS Code | `.vscode/mcp.json`：`{"servers": {"miru": {"type": "http", "url": "http://localhost:8100/mcp"}}}` |
| claude.ai／Claude Desktop | 自訂（Customize）→ 連接器（Connectors）→ 新增自訂連接器，選 **No sign in**；需要**公開的 HTTPS** 網址，不能用 localhost |
| Claude API | `mcp_servers` 填公開 HTTPS 網址（要帶方案時再加 `authorization_token`） |

以上指令取自各用戶端的官方文件，尚未對真實用戶端逐一實測；ChatGPT 連接器則尚未驗證。詳細說明、HTTPS 通道與 Bearer token 的作法見
[`docs/backend/mcp-claude-connector.md`](docs/backend/mcp-claude-connector.md)。
</details>

## ⚙️ 設定

所有設定都在 `backend/.env`，每個變數的說明見 [`backend/.env.example`](backend/.env.example)。
網頁端只需要在 `web/.env.local` 設定 `NEXT_PUBLIC_API_BASE`。

| 類別 | 變數 | 說明 |
|---|---|---|
| LLM | `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `GEMINI_API_KEY` · `OPENROUTER_API_KEY` · `OLLAMA_BASE_URL` | 填你要用的即可；`LLM_PROVIDER`／`MODEL_ROLE_*` 依角色指定模型 |
| 市場資料 | `FINMIND_API_KEY` | 全市場匯入需 FinMind **Backer** 以上方案；Sponsor 限定的資料（例如法人明細、ETF 成分股）在較低方案下會停止更新，網頁會顯示最後一個有資料的日期 |
| 選用資料 | `FINNHUB_KEY` · `BRAVE_SEARCH_API_KEY` · `FRED_API_KEY` | 美股分析師預估、網路搜尋備援，以及美國總經儀表板（免費金鑰；未設定時每晚的匯入會略過，面板維持空白） |
| 參考資料 | `REFERENCE_DATA_DIR` | 你在本機維護的供應鏈、概念題材與關聯資料（預設 `./reference-data`，已加入 gitignore，Docker 內以唯讀方式掛載）。它不在這個儲存庫內；沒有這些資料時，相關面板會顯示誠實的空白狀態。用 `python -m analytics.supply_chain load` 載入，再執行 `positions`；`eval` 會用你自己標註的資料集評分 |
| 資料庫 | `DATABASE_URL` · `REDIS_URL` · `DUCKDB_PATH` · `PARQUET_ROOT` | 開發時用 SQLite 與本地路徑即可 |
| 登入 | `GOOGLE_*` · `LINE_*` · `TELEGRAM_LOGIN_*` · `JWT_*` | Google、LINE Login、Telegram 登入 |
| 通知 | `LINE_MESSAGING_*` · `TELEGRAM_BOT_*` · `RESEND_*` · `VAPID_*` | 提醒與簡報的發送管道（LINE、Telegram、Email、網頁推播） |
| 付款 | `PADDLE_*` | 訂閱；權限以 webhook 為準 |

> [!WARNING]
> `backend/.env` 存放真實機密，已列入 gitignore。請勿提交、貼上或寫入日誌。

## 🗂️ 專案結構

```
Miru/
├─ backend/   FastAPI + MCP 後端（Python 3.13、uv）：src/<domain>/、tests/、docker-compose.yml
├─ web/       Next.js App Router 前端（pnpm）：對話、市場、選股、個股頁、設定
├─ docs/      權威規格：架構、API 參考、27 份模組規格、品牌
├─ scripts/   dev-backend.sh · dev-frontend.sh
└─ reference-data/   你的本機供應鏈與題材資料（gitignore，不隨專案發佈）
```

## 🧪 開發

```bash
# 後端
cd backend
uv run ruff check --fix . && uv run ruff format .
uv run mypy src
uv run pytest -m "not network"

# 前端
cd web
pnpm lint && pnpm typecheck && pnpm test
```

[`docs/`](docs/) 裡的規格是權威依據，修改任何一層之前請先讀該模組的規格。建議從
[`AGENTS.md`](AGENTS.md)、[`docs/backend/architecture.md`](docs/backend/architecture.md) 與
[`docs/backend/modules/README.md`](docs/backend/modules/README.md) 讀起；程式碼規範見
[`backend/CLAUDE.md`](backend/CLAUDE.md) 與 [`web/OVERVIEW.md`](web/OVERVIEW.md)。

## ⚠️ 免責聲明

Miru 與本專案僅提供**研究與教育用途的分析脈絡，非投資建議**，內容不構成任何交易指示。資料可能延遲或不完整，
每個回答都會標明來源與資料時點。請自行判斷，必要時請諮詢合格的專業人士。

## 📄 授權

[Apache License 2.0](LICENSE) © 2026 Ray Shen
