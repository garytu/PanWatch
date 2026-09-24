# 盯盤俠 PanWatch

**自託管 AI 盯盤助手 · 整合 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 多 Agent 投資決策** — A 股 / 港股 / 美股即時監控、持倉管理、智慧分析、全管道推送

[![GitHub stars](https://img.shields.io/github/stars/TNT-Likely/PanWatch?style=flat&logo=github&color=yellow)](https://github.com/TNT-Likely/PanWatch/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/sunxiao0721/panwatch?logo=docker&label=docker%20pulls&color=2496ED)](https://hub.docker.com/r/sunxiao0721/panwatch)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/TNT-Likely/PanWatch)](https://github.com/TNT-Likely/PanWatch/commits/main)
[![PWA](https://img.shields.io/badge/PWA-installable-5A0FC8?logo=pwa&logoColor=white)](https://github.com/TNT-Likely/PanWatch)

![盯盤俠 PanWatch · TradingAgents 深度分析演示](docs/screenshots/tradingagents-demo.gif)

> 🧠 **持倉頁點一下 → TradingAgents 9-Agent 投研團隊接力分析 → 看多看空辯論 → 風控審查 → PM 決策書,3-5 分鐘一條完整推理鏈,結論直推到你的 IM。**

## 📸 功能一覽

| 持倉 · 多帳戶彙總 | 機會頁 · AI 評分選股 |
|:---:|:---:|
| ![持倉管理](./docs/screenshots/portfolio.png) | ![機會頁 AI 評分](./docs/screenshots/opportunities.png) |
| **模擬交易 · 淨值曲線 + 績效** | **個股深度詳細資訊** |
| ![模擬交易](./docs/screenshots/papertrading.png) | ![個股詳細資訊](./docs/screenshots/stock-detail.png) |
| **技術指標共振 · 一眼 MACD/RSI/KDJ** | **價格提醒 · 條件組合觸發** |
| ![技術指標](./docs/screenshots/technicals.png) | ![價格提醒](./docs/screenshots/alerts.png) |

<details>
<summary>移動端截圖</summary>

<img src="./docs/screenshots/mobile.png" width="300" /> <img src="./docs/screenshots/mobile-detail.png" width="300" />

> 📱 支援 PWA，移動端可「新增到主螢幕」當原生 App 用。

</details>

> 💡 如果盯盤俠對你有幫助，點右上角 ⭐ **Star** 支援一下 —— 這是對開源專案最好的鼓勵，也能讓更多人發現它。

## 🧠 深度分析：TradingAgents 多 Agent 決策

接入 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（76k+ star）多 Agent 投資決策框架，在持倉頁點 🧠 圖示即可觸發：

- **4 類分析師**（技術 / 情緒 / 新聞 / 基本面） → **看多看空辯論** → **風控審查** → **PM 整合決策**
- 3-5 分鐘輸出完整推理鏈，結論同步推送到 Telegram / 微信 / 釘釘
- 預設 deepseek-chat，單次 ~$0.05，月度預算可控

## 為什麼選擇盯盤俠？

- **資料私有** — 自託管部署，持倉資料不經過任何第三方
- **AI 原生** — 不是簡單的指標堆砌，而是讓 AI 理解你的持倉、風格和目標
- **開箱即用** — Docker 一鍵部署，5 分鐘完成配置

## 核心功能

<details>
<summary><b>智慧 Agent 系統</b></summary>

| Agent | 觸發時機 | 功能 |
|-------|---------|------|
| **盤前分析** | 每日開盤前 | 綜合隔夜美股、新聞訊息、技術形態，給出今日操作策略 |
| **盤中監測** | 交易時段即時 | 監控異動訊號，RSI/KDJ/MACD 共振時推送提醒 |
| **盤後日報** | 每日收盤後 | 覆盤當日走勢，分析資金流向，規劃次日操作 |
| **新聞速遞** | 定時採集 | 抓取財經新聞，AI 篩選與持倉相關的重要資訊 |

</details>

<details>
<summary><b>專業技術分析</b></summary>

- **趨勢指標**：MA 多空排列、MACD 金叉死叉、布林帶突破
- **動量指標**：RSI 超買超賣、KDJ 鈍化與背離
- **量價分析**：量比異動、縮量回檔、放量突破
- **形態識別**：錘子線、吞沒形態、十字星等 K 線形態
- **支撐壓力**：自動計算多級支撐位和壓力位

</details>

<details>
<summary><b>多市場 & 多帳戶</b></summary>

- **覆蓋市場**：A 股、港股、美股即時行情
- **帳戶管理**：支援多券商帳戶獨立管理，彙總展示總資產
- **交易風格**：按短線/波段/長線分別設定，AI 建議更精準

</details>

<details>
<summary><b>全管道通知</b></summary>

Telegram / 企業微信 / 釘釘 / 飛書 / Bark / 自定義 Webhook

</details>

<details>
<summary><b>價格提醒</b></summary>

- 支援價格、漲跌幅、成交額、量比等條件組合（AND / OR）
- 支援交易時段/全天生效、冷卻時間、日觸發上限、重複觸發模式
- 到期時間使用彈跳視窗內日期面板 + `HH:mm` 輸入，留空表示永不過期
- 可按規則選擇通知管道，不選則走系統預設管道

</details>

## 快速開始

```bash
docker run -d \
  --name panwatch \
  -p 8000:8000 \
  -v panwatch_data:/app/data \
  sunxiao0721/panwatch:latest
```

訪問 `http://localhost:8000`，首次使用設定帳號密碼即可。

說明：映象內已包含 Playwright 執行所需的系統依賴；Chromium 瀏覽器會在容器首次啟動時自動下載並安裝到掛載卷（預設 `/app/data/playwright`），首次啟動可能需要幾分鐘且需要網路可達。

如果不需要截圖等瀏覽器能力，可以在啟動容器時設定 `PLAYWRIGHT_SKIP_BROWSER_INSTALL=1` 跳過首次 Chromium 下載/安裝。

<details>
<summary>Docker Compose</summary>

```yaml
version: '3.8'
services:
  panwatch:
    image: sunxiao0721/panwatch:latest
    container_name: panwatch
    ports:
      - "8000:8000"
    volumes:
      - panwatch_data:/app/data
    restart: unless-stopped

volumes:
  panwatch_data:
```

```bash
docker-compose up -d
```

</details>

<details>
<summary>環境變數</summary>

| 變數名 | 說明 | 預設值 |
|--------|------|--------|
| `AUTH_USERNAME` | 預設登入使用者名稱 | 首次訪問時設定 |
| `AUTH_PASSWORD` | 預設登入密碼 | 首次訪問時設定 |
| `JWT_SECRET` | JWT 簽名金鑰 | 自動生成 |
| `DATA_DIR` | 資料儲存目錄 | `./data` |
| `TZ` | 應用時區（影響 Agent 排程觸發時間與時間展示） | `Asia/Shanghai` |
| `PLAYWRIGHT_SKIP_BROWSER_INSTALL` | 跳過首次 Chromium 安裝（不需要截圖時可用） | 未設定 |
| `LOG_LEVEL` | 主控台日誌級別。預設 `INFO`（只輸出業務事件 + 錯誤）；排查問題時設 `DEBUG` 可看到排程心跳、採集過程等底層日誌。UI 日誌板始終保留完整記錄，不受影響 | `INFO` |
| `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` | 出站 HTTP 代理。三種配置方式任選其一: ① 啟動前 `export HTTP_PROXY=...`；② `.env` 裡寫 `http_proxy=http://host:port`；③ UI「設定 → 全域性 HTTP 代理」。三者優先順序:外部環境變數 > UI > `.env`。生效後所有 httpx 使用者端走代理。`NO_PROXY` 預設包含 `localhost,127.0.0.1` | 未設定 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry OTLP 匯出端點(如 `http://jaeger:4318`)。**配置後**才啟用 OTel trace 匯出;留空則完全關閉(零副作用)。還需安裝可選依賴 `requirements-otel.txt`。詳見下方「OTel 匯出」 | 未設定(關閉) |

</details>

<details>
<summary>首次配置</summary>

1. 訪問 Web 介面，設定登入帳號
2. **設定 → AI 服務商**：配置 OpenAI 相容 API（支援 OpenAI / 智譜 / DeepSeek / Ollama 等）
3. **設定 → 通知管道**：新增 Telegram 或其他推送管道
4. **持倉 → 新增股票**：新增自選股，啟用對應 Agent

</details>

<details>
<summary>本地開發</summary>

**環境要求**：Python 3.10+ / Node.js 18+ / pnpm

```bash
# 一鍵開發（推薦）
make dev-api          # 啟動後端（自動 venv+依賴，監聽 :8000）
make dev-web          # 啟動前端（自動 pnpm install，監聽 :5183）

# 或手動
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python server.py                              # 後端 :8000

cd frontend && pnpm install && pnpm dev       # 前端 :5183
```

前端 dev server 跑在 `http://localhost:5183`，並把 `/api` 代理到 `127.0.0.1:8000`。
前端用 `:5183` 而非預設 `:5173`，是為了和 BeeCount-Cloud 等本地常駐前端錯開。

</details>

<details>
<summary><b>技術棧</b></summary>

**後端**：FastAPI / SQLAlchemy / APScheduler / OpenAI SDK

**前端**：React 18 / TypeScript / Tailwind CSS / shadcn/ui

</details>

<details>
<summary><b>OTel 匯出（可選，預設關閉）</b></summary>

PanWatch 內建一套自建可觀測體系(結構化日誌 `trace_id` 貫穿 / `agent_runs` 執行表 / TradingAgents 節點級進度與成本),開箱即用、無需任何外部元件。

在此之上,可**可選地**再掛一層標準 [OpenTelemetry](https://opentelemetry.io/) 匯出,把 trace 送到 Jaeger / Tempo / Langfuse 等標準 APM。三類 span 對映:

- **Agent 一次執行** → root span(複用 `agent_runs` 的 `trace_id` 關聯)
- **單次 LLM 呼叫** → `gen_ai` 子 span,遵循 [OpenTelemetry GenAI 語義約定](https://opentelemetry.io/docs/specs/semconv/gen-ai/)(`gen_ai.system` / `gen_ai.request.model` / `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.operation.name`),可被標準 APM 識別為一次模型呼叫
- **TradingAgents 節點** → 子 span(複用節點級進度回撥)

**預設完全關閉**:不裝依賴、不配 endpoint 時,匯出層全程 no-op,不改變任何現有行為。

**開啟三步**:

```bash
# 1. 安裝可選依賴
pip install -r requirements-otel.txt

# 2. 配置 OTLP 端點(指向你的 collector / APM)
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=panwatch   # 可選,預設 panwatch

# 3. 正常啟動;啟動日誌出現 "OTel 匯出已啟用" 即生效
python server.py
```

**本地起一個 Jaeger 驗證**:

```bash
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 \
  jaegertracing/all-in-one:latest
# 觸發任意 Agent 執行後,開啟 http://localhost:16686 選 service=panwatch 檢視 trace
```

Langfuse / Tempo 同理,把 `OTEL_EXPORTER_OTLP_ENDPOINT` 指向對應 OTLP 入口即可。

</details>

<details>
<summary><b>釋出（Docker 映象）</b></summary>

本專案內建 GitHub Actions 釋出流程：

- 打 tag（例如 `0.2.3`）會自動構建並推送 Docker 映象
  - `sunxiao0721/panwatch:0.2.3`
  - `sunxiao0721/panwatch:latest`
- 也支援在 GitHub Actions 裡手動觸發（workflow_dispatch）指定版本號

需要在倉庫 Secrets 中配置：

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

</details>

## 捐贈支援

如果你覺得 PanWatch 有幫助，歡迎請作者喝杯咖啡：

| 微信讚賞 | 支付寶 |
|:---:|:---:|
| <img src="./docs/donate/wechat.png" width="240" /> | <img src="./docs/donate/alipay.png" width="240" /> |

## 貢獻

歡迎提交 Issue 和 PR！自定義 Agent 和資料來源開發請參考 [貢獻指南](CONTRIBUTING.md)。
社群交流（Telegram）：[t.me/panwatch](https://t.me/panwatch)

## License

[MIT](LICENSE)
