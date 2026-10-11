# PanWatch 後端架構

## 目標與形態

PanWatch 是一個**模組化單體**：一個 FastAPI 應用、一個共享資料庫，但以穩定的
業務邊界組織程式碼。目標不是為每個領域拆微服務，而是讓每段業務程式碼都有明確
的所有者、依賴方向和測試邊界，避免重新形成無邊界的 `core` 目錄。

```text
src/
├── bootstrap/       # 應用啟動與依賴裝配
├── platform/        # 與業務無關的技術能力
├── modules/         # 產品業務能力
└── web/             # 跨模組複用的 HTTP 中介軟體與回應適配
```

`collectors/`、`models/` 和 `compat/` 已完成收口並被刪除。採集器位於
`platform/marketdata/collectors/`，市場共用的行情值物件位於
`platform/marketdata/models.py`；不得重新建立這些根目錄。

## 依賴方向

```text
web ───────────────► modules ───────────────► platform
 │                    │                         │
 │                    └─ public service / DTO ──┘
 └────────────────────────► platform
```

- `platform` 不得匯入 `modules`，也不做產品或投資決策。
- `modules` 可使用 `platform`，但不能直接匯入另一個模組的 ORM models 或 repository。
- 一個模組不得匯入另一個模組的 `api/` router 或 `*_api.py`；跨模組 HTTP 程式碼也
  必須改為呼叫目標模組公開的 service、DTO 或受支援的工具邊界。
- 跨模組協作必須經過擁有模組公開的 service、DTO 或 event。
- `web` 只做 HTTP 輸入輸出對映與服務裝配，不承載複雜 SQL、Agent 工具迴圈或策略判斷。
- `bootstrap` 只負責啟動期裝配，不實現業務流程。

`tests/test_architecture_boundaries.py` 負責守衛這些規則。修改模組邊界時，應同步
更新架構測試與本檔案。

## `platform/`：技術平臺

平臺程式碼描述“怎樣連線或執行”，不描述“使用者應做什麼”。

| 子目錄 | 責任 | 不應包含 |
| --- | --- | --- |
| `persistence/` | engine、Session、ORM Base、全部表定義、版本遷移 | 持倉、策略等業務判斷 |
| `ai/` | AI provider client、failover、模型傳輸適配 | 提示詞、工具授權 |
| `marketdata/` | 外部行情使用者端、採集器、行情值物件、程式碼規範化、供應商路由、資料歸一化 | 告警閾值、選股規則 |
| `events/` | SSE 等事件傳輸 | 事件的業務含義 |
| `scheduling/` | cron 解析、交易日曆、登入檔 | Agent 排程流程 |
| `notifications/` | 通道傳送、基礎去重和策略 | 哪種業務事件應通知 |
| `observability/` | 日誌上下文、trace、指標匯出 | 領域指標推導 |
| `runtime/` | 程式配置、環境變數與其他橫切執行期設定 | 產品業務規則 |

## `modules/`：業務能力

一個一級目錄擁有一個產品能力。推薦但非強制的內部結構：

```text
<module>/
├── api.py          # 可選：模組專屬 router
├── service.py      # 用例編排，也是優先的公開邊界
├── repository.py   # 可選：持久化查詢與寫入
├── models.py       # 可選：模組使用的領域/ORM 模型引用
├── schemas.py      # 可選：DTO、命令與回應模型
└── ...             # 領域專屬實現
```

不要為了形式建立空層。其他模組應呼叫 service，而不是繞過它匯入 repository。

| 模組 | 擁有的能力 | 典型公開邊界 |
| --- | --- | --- |
| `assistant` | 對話、任務快照、PanAgent host adapter、已批准工具 | `AssistantService` |
| `automation` | 定時分析 Agent、執行記錄、TradingAgents、AgentScheduler | agent service / scheduler |
| `market` | 標的、採集編排、新聞、K 線上下文、價格告警 | market/alert service |
| `portfolio` | 帳戶、倉位、診斷、業績基準 | `PortfolioService` |
| `research` | 分析歷史、上下文、證據、結果評估、signals | research/context service |
| `strategy` | 因子、訊號、候選標的、校準、backtest | strategy service |
| `paper_trading` | 模擬交易執行、帳本、分配、通知 | paper-trading service |
| `reporting` | 報告與 PDF 等產物渲染 | render/export function |
| `administration` | 健康檢查、設定維護、PAT、升級檢查 | administration service |

## `bootstrap/`：應用裝配

`bootstrap/application.py` 是唯一建立 FastAPI 應用並註冊 router 的位置。它可以
依賴 `web`、`modules` 和 `platform` 的公開 HTTP 入口，但不得承載領域規則、SQL 或
Agent 執行迴圈。`bootstrap` 中只保留確有啟動期職責的檔案；沒有呼叫者的“容器”或
轉發 facade 不應為了預留結構而存在。

## 模組擁有 HTTP router

應用組裝由 `bootstrap/application.py` 負責；業務 router 位於各自模組的
`api/` 包，而非集中到新的 `web/api` 目錄。例如，`modules/market/api/` 擁有
行情、K 線、標的、新聞和價格告警介面；`modules/portfolio/api/` 擁有帳戶、歷史和
儀表盤介面。

`web/` 只保留跨模組複用的 HTTP 技術元件，例如回應包裝中介軟體。每個業務 router
僅負責：

1. 校驗 HTTP 輸入並建立 command/DTO；
2. 獲取模組 service；
3. 將 service 結果對映為 HTTP response、SSE 或錯誤碼。

資料庫、ORM models 與 migrations 均在 `platform/persistence/`。禁止恢復
`src/web/api/`、`src/web/app.py`、`src/web/database.py`、`src/web/models.py` 或
`src/web/migrations.py`。

| 模組 | HTTP router 目錄 | 介面範圍 |
| --- | --- | --- |
| `administration` | `modules/administration/api/` | 鑑權、設定、健康檢查、日誌、資料來源、PAT、MCP |
| `assistant` | `modules/assistant/api.py`、`chat_api.py` | 導航級助手與相容的聊天介面；共享舊聊天工具在 `legacy_chat_tools.py` |
| `automation` | `modules/automation/api/` | Agent、建議池、模板 |
| `market` | `modules/market/api/` | 標的、行情、K 線、新聞、發現、價格告警 |
| `portfolio` | `modules/portfolio/api/` | 帳戶、持倉歷史、儀表盤 |
| `research` | `modules/research/api/` | 上下文、洞察、評估、回饋、建議 |
| `strategy` | `modules/strategy/api/` | 因子介面 |
| `paper_trading` | `modules/paper_trading/api/` | 模擬交易介面 |

## 關鍵流程

### 導航級助手

```text
/assistant 頁面
  → /api/assistant router
  → AssistantService
  → AgentRuntime (packages/pan-agent-runtime)
  → ModelPort + 已批准 ToolRegistry
  → runtime events → task persistence + SSE → UI
```

`pan-agent-runtime` 的匯入名為 `pan_agent`。它只定義受限執行迴圈、資源限制和
可移植事件，不能匯入 FastAPI、SQLAlchemy、`src.*` 或 LangChain。PanWatch 的
介面卡、工具、持久化都屬於 `modules/assistant`。

### 跨模組呼叫

若 `strategy` 需要持倉摘要，不應匯入 `portfolio.repository` 或
`portfolio.models`；應由 `portfolio` 暴露專門 service/DTO。非同步、可延遲或涉及
多個所有者的工作應釋出領域 event，由訂閱模組自行處理。

## 持久化與遷移

全部 SQLAlchemy 表註冊在 `platform/persistence/models.py`；engine、`Base`、
Session 與 `get_db` 在 `database.py`；版本遷移在 `migrations.py`。

新增 schema 時：先在所屬模組確定行為和測試；再登入檔、新增可重複執行的新
版本遷移；不要修改已釋出遷移，也不要在 router 中執行 schema 變更。

## 新程式碼放置指南

| 需求 | 放置位置 |
| --- | --- |
| 新 AI、行情或通知供應商 | 對應 `platform/*` adapter |
| 新投資、分析或使用者工作流 | 對應 `modules/<domain>` service |
| 新 API | 所屬模組的 `api/` 包；只有該模組確實只有一個 router 時才用單個 `api.py` |
| 新後臺任務 | 業務執行在模組，cron/日曆使用 `platform/scheduling` |
| 新 ORM 表或遷移 | `platform/persistence`，由所屬模組 service 使用 |
| 帶業務含義的 helper | 其所屬 module；不得建立新的 `core` |

## 禁止項

- 不恢復 `src/core`、`src/agents`、`src/web/api` 或舊 `src/web` 持久化檔案；
- 不讓 `platform` 匯入 `modules`；
- 不跨模組匯入 `models.py`、`repository.py`；
- 不把業務規則、SQL 或工具迴圈塞進 HTTP router；
- 不讓 `pan_agent` 依賴 PanWatch、資料庫或具體 AI SDK；
- 不以“通用 helper”為名建立沒有所有者的根目錄模組。

這些規則不是為了增加層數，而是讓每段程式碼的歸屬、依賴和演進方式都清晰可見。
