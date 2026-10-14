<div align="center">

<img src="web/public/logo.png" alt="Miru" width="120" />

# Miru

**你的台股、美股與 ETF AI 助理：看懂市場，每個回答都附上資料來源。**

[![Backend CI](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-f2873a.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/node-22+-339933?logo=node.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-App%20Router-000000?logo=next.js&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-read--only-6E56CF)

[English](README_EN.md) | **繁體中文**

</div>

<!-- 宣傳影片：在 GitHub 的 README 編輯器中，把 promo/out/miru-promo-zh-TW.mp4 拖到下面的空行。 -->


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
| 🌏 **台股＋美股** | 價量、法人籌碼、融資融券、基本面、ETF 成分股與淨值、衍生性商品、除權息與公司事件、新聞；資料路由涵蓋台、美與全球共 146 項資料能力 |
| 📊 **個股與 ETF 頁** | 圖表、技術指標、描述性的多因子「多空分數」、ETF 比較與重疊度、本益比河流圖 |
| 🔎 **選股與題材** | 標籤選股、題材與產業鏈分層、市場廣度與排行 |
| 🧩 **41 個內建技能** | 封裝好的分析流程：台股／美股個股報告、ETF 報告、晨報、籌碼回顧、財報品質、總經儀表板等 |
| 🔬 **深度研究**（Pro） | 在一次對話中進行有預算上限、多步驟、附結構化引用的網路研究 |
| 🔔 **提醒與定時簡報** | 由你自己授權的規則與簡報，可送到 LINE、Telegram、Email 或網頁推播 |
| 🎭 **Miru 的語氣** | 四種語氣（穩健派・白話派・簡潔派・數據派）：同一個引擎、四種說話方式，不是四位不同的顧問 |
| 🧠 **記憶** | 對話與長期記憶，可在設定中檢視與編輯 |
| 🔌 **自選模型** | 不綁定供應商的 LLM 介面：Anthropic、OpenAI、Gemini、Groq、Cerebras、OpenRouter，或透過 Ollama 使用本地模型；支援 BYOK |
| 🛰️ **MCP 伺服器** | 同一套工具以唯讀方式透過 MCP（streamable HTTP）提供，讓其他代理也能使用 |
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
<summary><b>連接 MCP 用戶端</b></summary>

唯讀 MCP 端點位於 `http://localhost:8100/mcp`（streamable HTTP），存活檢查在 `/healthz`。
它是獨立的程序，與 API 分開執行。
</details>

## ⚙️ 設定

所有設定都在 `backend/.env`，每個變數的說明見 [`backend/.env.example`](backend/.env.example)。
網頁端只需要在 `web/.env.local` 設定 `NEXT_PUBLIC_API_BASE`。

| 類別 | 變數 | 說明 |
|---|---|---|
| LLM | `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `GEMINI_API_KEY` · `GROQ_API_KEY` · `CEREBRAS_API_KEY` · `OPENROUTER_API_KEY` · `OLLAMA_BASE_URL` | 填你要用的即可；`LLM_PROVIDER`／`MODEL_ROLE_*` 依角色指定模型 |
| 市場資料 | `FINMIND_API_KEY` | 全市場匯入需 FinMind **Backer** 以上方案 |
| 選用資料 | `FINNHUB_KEY` · `ALPHA_VANTAGE_API_KEY` · `FRED_KEY` · `FMP_API_KEY` · `BRAVE_SEARCH_API_KEY` | 美股、總經、分析師預估、網路搜尋備援 |
| 資料庫 | `DATABASE_URL` · `REDIS_URL` · `DUCKDB_PATH` · `PARQUET_ROOT` | 開發時用 SQLite 與本地路徑即可 |
| 登入 | `GOOGLE_*` · `LINE_*` · `TELEGRAM_LOGIN_*` · `JWT_*` | Google、LINE Login、Telegram 登入 |
| 通知 | `LINE_MESSAGING_*` · `TELEGRAM_BOT_*` · `RESEND_*` · `APNS_*` · `FCM_*` | 提醒與簡報的發送管道 |
| 付款 | `PADDLE_*` | 訂閱；權限以 webhook 為準 |

> [!WARNING]
> `backend/.env` 存放真實機密，已列入 gitignore。請勿提交、貼上或寫入日誌。

## 🗂️ 專案結構

```
Miru/
├─ backend/   FastAPI + MCP 後端（Python 3.13、uv）：src/<domain>/、tests/、docker-compose.yml
├─ web/       Next.js App Router 前端（pnpm）：對話、市場、選股、個股頁、設定
├─ docs/      權威規格：架構、API 參考、26 份模組規格、品牌
├─ promo/     Remotion 宣傳影片（繁中＋英文）
└─ scripts/   dev-backend.sh · dev-frontend.sh
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

[MIT](LICENSE) © 2026 Ray Shen
