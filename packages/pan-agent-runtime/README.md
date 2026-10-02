# PanAgent Runtime

<code>pan-agent-runtime</code> 是一個輕量、與業務無關的 Python Agent 執行核心，負責把
“模型輸出工具呼叫”安全地變成“可觀測、可暫停、可恢復的任務”。它不繫結 FastAPI、
資料庫、具體模型供應商或任何業務領域，適合被 PanWatch、BeeCount-Cloud 以及其他專案
作為底層依賴複用。

> 當前版本：<code>0.1.0</code><br>
> Python：<code>>=3.10</code><br>
> 執行時依賴：<code>pydantic>=2.0</code>

## 為什麼單獨抽成 runtime

業務專案通常會同時遇到幾類 Agent 需求：

- 模型需要呼叫宿主專案提供的查詢、寫入或外部服務工具；
- 寫入類工具必須先經過使用者確認，確認後還能從中斷處繼續；
- 前端需要即時看到 token、工具開始/結束和審批請求；
- 任務必須有步驟數、工具呼叫數、單次超時和總超時上限；
- 模型偶爾會重複提交相同工具呼叫，需要被及時熔斷。

這些能力與“股票、記帳、CRM”等業務無關，因此放在 runtime 中統一實現。業務專案
只負責提供模型介面卡、工具實現、權限策略、事件傳輸和持久化。

## 設計邊界

### runtime 負責

- provider-neutral 的訊息、工具、權限、審批、checkpoint 和事件契約；
- 有界的序列 Agent loop；
- 工具可見性和每次呼叫的權限決策；
- Human-in-the-loop 暫停、部分審批和恢復；
- 工具超時、重試、總超時、最大步驟數和重複呼叫檢測；
- 工具目錄描述、確定性檢索和策略過濾（Tool Research）；
- 將執行過程轉換為穩定的結構化事件。

### 宿主專案負責

- 呼叫 OpenAI-compatible、Anthropic 或本地模型的 <code>ModelPort</code> 介面卡；
- 具體業務工具和工具結果；
- 使用者、租戶、角色和工具權限的持久化；
- 審批卡片展示、審批決定儲存和 checkpoint 持久化；
- SSE、WebSocket、訊息佇列或其他 UI 傳輸；
- 資料庫事務、快取、限流和業務審計。

runtime 不會直接連線資料庫，也不會替宿主決定“誰可以執行什麼”。預設策略只允許
無須確認的讀工具，寫入和外部副作用必須由宿主顯式注入策略。

### 持久化與部署邊界

`pan-agent-runtime` 只產生 provider-neutral 的 `RunResult`、`AgentCheckpoint`
和 `RuntimeEvent`，不內建 SQLite、Redis、佇列、worker 程式或 SSE。宿主可以把這些
物件對映到關係庫、物件儲存或其他任務系統，並自行決定是否需要跨程式執行。

當前 PanWatch 的宿主適配使用 SQLite 儲存任務快照、事件和審批 checkpoint；瀏覽器重新整理
透過資料庫事件 replay/tail 恢復展示，不代表 runtime 自己擁有持久化能力。

## 安裝

### 在 monorepo 中本地安裝

從倉庫根目錄執行：

~~~bash
python -m pip install -e packages/pan-agent-runtime
~~~

PowerShell 也可以使用：

~~~powershell
python -m pip install -e .\packages\pan-agent-runtime
~~~

### 作為獨立包安裝

釋出到包索引後，其他專案只需要：

~~~bash
python -m pip install pan-agent-runtime
~~~

包的 import 名稱是 <code>pan_agent</code>，不是帶連字元的發行包名：

~~~python
from pan_agent import AgentRuntime, ToolRegistry
~~~

## 5 分鐘示例：執行一個只讀 Agent

下面的示例使用一個假的模型介面卡，展示 runtime 的最小接入面。真實專案只需要把
<code>DemoModel</code> 換成自己的模型 SDK 介面卡。

~~~python
import asyncio
from datetime import UTC, datetime

from pan_agent import (
    AgentRuntime,
    ModelMessage,
    ModelTurn,
    ReadOnlyToolPolicy,
    RunRequest,
    RuntimeEvent,
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolRisk,
)


class DemoModel:
    """真實專案中，這裡負責把 ModelPort 呼叫轉換成模型供應商協議。"""

    async def run_turn(self, messages, tools, emit_token):
        # 第一次返回工具呼叫；下一次直接返回最終答案。
        if not any(message.role == "tool" for message in messages):
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="lookup_balance",
                        arguments={"account_id": "demo"},
                    )
                ]
            )
        await emit_token("帳戶餘額查詢完成。")
        return ModelTurn(content="帳戶餘額查詢完成。")


class ConsoleSink:
    async def publish(self, event: RuntimeEvent):
        print(event.type.value, event.data)


async def lookup_balance(_request, arguments):
    return ToolResult.success(
        summary=f"帳戶 {arguments['account_id']} 餘額為 100.00。",
        data={"account_id": arguments["account_id"], "balance": 100.0},
        sources=[{"name": "demo ledger"}],
        observed_at=datetime.now(UTC),
    )


async def main():
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="lookup_balance",
            title="查詢餘額",
            description="查詢一個帳戶的當前餘額。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["account_id"],
                "properties": {"account_id": {"type": "string"}},
            },
        ),
        lookup_balance,
    )

    request = RunRequest(
        run_id="demo-run-1",
        messages=[ModelMessage(role="user", content="查詢我的餘額")],
    )
    runtime = AgentRuntime(DemoModel(), tools, policy=ReadOnlyToolPolicy())
    result = await runtime.run(request, ConsoleSink())
    print(result.status, result.answer)


asyncio.run(main())
~~~

宿主提供的工具執行器必須是非同步 callable，簽名為：

~~~python
async def executor(request: RunRequest, arguments: dict) -> ToolResult:
    ...
~~~

<code>ToolResult.summary</code> 是給模型和 UI 的短摘要，<code>data</code> 才是模型後續
推理所需的結構化資料，<code>sources</code> 用於來源展示。建議摘要簡潔、資料可序列化，
並避免把整張資料庫表或完整回應原樣塞進上下文。

## 執行生命週期

一次 <code>AgentRuntime.run()</code> 的流程如下：

~~~text
RunRequest
   │
   ├─ RUN_CREATED
   ├─ 模型回合（只看到 policy 允許暴露的工具）
   ├─ 工具權限決策
   │    ├─ ALLOW → TOOL_STARTED → TOOL_COMPLETED
   │    ├─ DENY  → 返回 permission_denied 工具結果，模型可以繼續
   │    └─ ASK   → APPROVAL_REQUIRED + AgentCheckpoint，暫停
   ├─ ANSWER_TOKEN（模型介面卡持續 emit）
   └─ RUN_COMPLETED / RUN_FAILED
~~~

## Optional Runtime Extensions

runtime 只定義通用的 `RuntimeExtension` 協議，不內建 Tool Research、記憶、MCP 或具體
可觀測性實現。擴充套件可以在每個模型回合前讀取請求、訊息和當前已透過策略的工具集合，
選擇已註冊工具、提供虛擬擴充套件工具，並在模型呼叫虛擬工具時處理它；它不能擴大權限邊界。

~~~python
from pan_agent import AgentRuntime

runtime = AgentRuntime(
    model,
    tools,
    policy=policy,
    extensions=[my_extension],
)
~~~

擴充套件透過 `emit_event()` 傳送通用的 `extension_event`，事件資料包含副檔名、事件名和
業務負載。擴充套件失敗時 runtime 會發出 fallback 事件並繼續使用預設工具集合。Tool Research
是一個獨立的可選包，PanWatch 透過顯式組裝接入；不安裝它時，`pan-agent-runtime` 仍可
單獨執行。

模型返回多個工具呼叫時，runtime 會按原順序處理。只要有一個呼叫需要審批，當前
任務就返回 <code>WAITING_FOR_APPROVAL</code>，尚未批准的呼叫不會執行。

## Human-in-the-loop 審批

### 預設只讀策略

<code>ReadOnlyToolPolicy</code> 只暴露 <code>risk=read</code> 且
<code>confirmation_required=False</code> 的工具。它適合公開查詢、離線分析或尚未接入
宿主權限系統的安全預設場景。

### 宿主自定義策略

需要寫入或外部副作用時，宿主實現 <code>ToolPolicy</code>。策略至少包含兩個方法：

~~~python
from pan_agent import ToolPermissionDecision, ToolRisk


class MyPolicy:
    def is_tool_visible(self, request, tool):
        # 決定模型本輪能發現哪些工具
        return True

    async def decide(self, request, tool, call):
        # 決定本次呼叫是 allow、ask 還是 deny
        if tool.risk is ToolRisk.READ and not tool.confirmation_required:
            return ToolPermissionDecision.allow()
        return ToolPermissionDecision.ask("該操作會修改資料，需要使用者確認")
~~~

生產環境的 <code>decide</code> 通常會讀取當前使用者、租戶和工具權限設定，並對寫入、
刪除、外部傳送等操作返回 <code>ask</code>。不要讓模型透過引數覆蓋策略結果。

### 暫停與恢復

審批請求返回後，宿主應持久化 <code>RunResult.checkpoint</code>，並把其中的
<code>pending_approvals</code> 轉成 UI 卡片。使用者決定後呼叫 <code>resume</code>：

~~~python
from pan_agent import ApprovalDecision, RunStatus

paused = await runtime.run(request, sink)
if paused.status is RunStatus.WAITING_FOR_APPROVAL:
    checkpoint = paused.checkpoint
    # 可以只決定一張卡，剩餘卡片會保留在新的 checkpoint 中。
    decisions = {
        checkpoint.pending_approvals[0].call_id: ApprovalDecision.APPROVED,
    }
    resumed = await runtime.resume(request, checkpoint, decisions, sink)
~~~

<code>resume</code> 不要求使用同一個 runtime 例項，因此只要宿主已經持久化了 checkpoint，
就可以在新的 runtime 例項或程式中恢復。runtime 不負責啟動任務、租約、重試或保證程式
重啟後自動續跑；這些屬於宿主的 task runner/queue 層。建議將 checkpoint 以 JSON 形式
持久化，並使用任務 ID、使用者 ID 和版本號做併發校驗，避免同一張審批卡被重複消費。

## 核心公開 API

### Context Engineering 擴充套件

長會話的上下文控制已經作為 `pan_agent.context` 的通用能力提供，不依賴
PanWatch 的資料庫、FastAPI 或模型廠商。它包括：

- `ContextBudget`：最大 token、soft/hard 閾值和最近訊息視窗；
- `ContextUsage`：系統指令、歷史訊息、最近訊息、頁面上下文和摘要的分段用量；
- `ContextSummary`：目標、約束、決定、事實、當前狀態、未完成事項和工具發現；
- `ContextEngine`：自動壓縮和 `force_compress=True` 主動壓縮；
- `ContextSummarizer`：宿主接入任意摘要模型的協議；
- `ExtractiveContextSummarizer`：模型不可用時的確定性 fallback。

Token 統計也遵循可插拔邊界：runtime 只定義 `TokenMeter` 和
`TokenMeasurement` 協議，預設使用無依賴的粗略估算，不繫結 tokenizer。需要更準確的
預估或 provider 用量歸一化時，宿主可以安裝可選的
`pan-agent-token-meter` 包；其中 `TiktokenTokenMeter` 透過可選依賴提供 tokenizer
計數，`normalize_provider_usage()` 將不同 provider 的回應轉換為統一的
`ModelUsage`。provider 返回的真實用量透過 `model_usage` 事件上報，但不會反向改變
已經完成的上下文壓縮決策。

示例：

~~~python
from pan_agent import ContextBudget, ContextEngine

result = await ContextEngine(my_summarizer).prepare(
    messages,
    budget=ContextBudget(
        max_tokens=12_000,
        soft_limit_tokens=8_400,
        hard_limit_tokens=10_200,
        keep_recent_messages=8,
        summary_max_tokens=800,
    ),
)
next_request_messages = result.messages
~~~

runtime 只負責這組 provider-neutral contracts。摘要模型選擇、snapshot
持久化、HTTP/SSE 和 UI 都由宿主專案注入。更完整的邊界說明見
[`docs/architecture.md`](docs/architecture.md) 和
[`docs/context.md`](docs/context.md)。

| 型別 | 用途 |
| --- | --- |
| <code>AgentRuntime</code> | 啟動、暫停和恢復有界 Agent loop |
| <code>ToolRegistry</code> | 註冊工具、按策略暴露工具、執行工具 |
| <code>ToolSpec</code> | 工具名稱、描述、風險等級、暴露層級和 JSON Schema |
| <code>ToolResult</code> | 工具成功/失敗、摘要、結構化資料和來源 |
| <code>ToolPolicy</code> | 宿主定義工具可見性和每次呼叫權限 |
| <code>ReadOnlyToolPolicy</code> | 安全預設策略，只允許無確認讀工具 |
| <code>ModelPort</code> | 宿主接入模型供應商的非同步協議 |
| <code>EventSink</code> | 宿主接收結構化執行事件的非同步協議 |
| <code>RunRequest</code> | 一次執行的訊息、上下文和限制 |
| <code>RunLimits</code> | 步驟數、工具呼叫數、超時和重試上限 |
| <code>AgentCheckpoint</code> | 審批暫停後可持久化的恢復狀態 |
| <code>RunResult</code> | 執行狀態、答案、錯誤碼和 checkpoint |
| <code>RuntimeEvent</code> | SSE/WebSocket 等傳輸使用的統一事件 |
| <code>RuntimeExtension</code> | 可選的模型回合擴充套件協議 |
| <code>ToolExposureDecision</code> | 擴充套件選擇已註冊工具並提供虛擬工具 schema |
| <code>TokenMeter</code> | 可選的上下文 token 預估協議 |
| <code>ModelUsage</code> | provider 返回的單回合實際用量 |

### 風險與權限

<code>ToolRisk</code> 當前包含：

- <code>read</code>：讀取資料，不產生業務副作用；
- <code>write</code>：建立或修改資料，通常應詢問使用者；
- <code>external</code>：傳送訊息、呼叫外部系統等副作用；
- <code>destructive</code>：刪除或不可逆操作，建議在宿主策略中預設拒絕或強制二次確認。

<code>PermissionMode</code> 的三個結果是 <code>allow</code>、<code>ask</code>、<code>deny</code>。
風險等級只是工具宣告，最終決定權始終在宿主的 <code>ToolPolicy</code>。

### 執行限制

<code>RunLimits</code> 預設值如下：

| 限制 | 預設值 | 可配置範圍 |
| --- | ---: | ---: |
| <code>max_steps</code> | 6 | 1–32 |
| <code>max_tool_calls</code> | 8 | 1–64 |
| <code>tool_timeout_seconds</code> | 20 | 1–120 |
| <code>run_timeout_seconds</code> | 90 | 1–600 |
| <code>step_retry_count</code> | 1 | 0–3 |

runtime 還會檢測連續重複的相同工具呼叫。達到閾值後返回
<code>repeated_tool_call</code>，防止模型在錯誤引數上無限迴圈。

## 事件與流式輸出

<code>RuntimeEvent.type</code> 可能是：

| 事件 | 典型用途 |
| --- | --- |
| <code>run_created</code> | 建立前端任務狀態 |
| <code>plan_created</code> | 預留給宿主展示計劃 |
| <code>step_updated</code> | 顯示當前 Agent 步驟 |
| <code>extension_event</code> | 持久化可選擴充套件的結構化事實 |
| <code>tool_started</code> | 顯示工具開始執行 |
| <code>tool_completed</code> | 顯示工具結果摘要和錯誤碼 |
| <code>model_usage</code> | 記錄 provider 返回的單回合實際用量 |
| <code>answer_token</code> | 增量渲染模型答案 |
| <code>approval_required</code> | 建立一張或多張審批卡 |
| <code>run_completed</code> | 任務成功結束 |
| <code>run_failed</code> | 任務以錯誤或部分結果結束 |

runtime 本身不實現 SSE。簡單場景下，FastAPI 宿主可以在 <code>EventSink.publish()</code>
中把事件寫入 <code>asyncio.Queue</code>；需要重新整理、斷線重連或審計時，宿主應先將事件
持久化到 Event Store，再由 SSE 層 replay/tail。這樣瀏覽器傳輸格式、事件儲存策略和
runtime 的執行邏輯保持解耦。

## BeeCount-Cloud 接入建議

推薦把接入分成五層：

1. **模型適配層**：在 <code>ModelPort.run_turn()</code> 中把 BeeCount-Cloud 當前模型
   使用者端的流式 token、tool call 和 finish reason 轉成 <code>ModelTurn</code>。
2. **業務工具層**：在 Cloud 自己的模組中註冊記帳、帳戶、報表等工具；工具實現只
   依賴 Cloud 的 service/repository，不進入 <code>pan-agent-runtime</code>。
3. **權限策略層**：實現 <code>ToolPolicy</code>，根據使用者、租戶、角色和工具設定返回
   <code>allow/ask/deny</code>。寫入、刪除和外部通知建議預設 <code>ask</code>。
4. **持久化層**：把 <code>RunRequest</code>、<code>RuntimeEvent</code>、<code>RunResult</code>
   和 <code>AgentCheckpoint</code> 對映到 Cloud 自己的任務/訊息/審批表。
5. **傳輸層**：將 <code>EventSink</code> 接到現有 SSE 或 WebSocket 通道；前端只消費
   統一事件，不需要知道底層模型供應商。

示意目錄：

~~~text
beecount-cloud/
├─ src/modules/assistant/
│  ├─ model_adapter.py       # ModelPort
│  ├─ policy.py              # ToolPolicy + 使用者權限
│  ├─ tools.py               # Cloud 業務工具註冊
│  ├─ event_sink.py          # SSE/WebSocket 事件橋接
│  └─ repository.py          # checkpoint / approval 持久化
└─ pyproject.toml            # 依賴 pan-agent-runtime
~~~

不要把 FastAPI <code>Request</code>、SQLAlchemy <code>Session</code>、Cloud 的模型類或
業務異常傳進 runtime 包的公共介面。這樣未來換資料庫、換模型供應商或把 runtime 釋出
到其他專案時，不會形成反向耦合。

## 工具變多後的上下文控制

runtime 會在每一輪呼叫 <code>ToolRegistry.model_tools(request, policy)</code>，因此
預設行為是“把策略允許的工具定義交給模型”。工具數量少時最直觀；工具增長後，工具
定義和工具結果都會成為上下文成本。

當前 runtime 已支援工具漸進式暴露：`ToolSpec.exposure` 可設定為
`direct`、`deferred` 或 `hidden`。預設只把 Direct 工具交給模型；Tool Research 等
可選擴充套件可以透過虛擬工具發現並載入 Deferred 工具。無論工具如何被發現，執行時仍然
必須經過宿主 `ToolPolicy` 和 Registry。

建議按以下順序繼續最佳化：

### 1. 按能力域動態暴露工具

在 <code>ToolPolicy.is_tool_visible()</code> 中根據當前請求上下文只暴露相關工具，例如：

- 使用者問餘額，只暴露帳戶和持倉查詢；
- 使用者問帳單，只暴露交易和分類工具；
- 使用者要求修改資料，再臨時暴露對應寫工具。

更大規模的系統可以只暴露一個“工具目錄/搜尋工具”，模型先檢索能力，再由擴充套件把
命中的 Deferred 工具加入後續回合。虛擬擴充套件工具透過 `ToolExposureDecision.additional_tools`
提供 schema，並透過 `RuntimeExtension.handle_tool_call()` 處理，不需要把擴充套件執行器註冊
進業務 Tool Registry。

### 2. 工具結果摘要化

<code>ToolResult.summary</code> 用於快速理解，<code>data</code> 只保留後續推理需要的欄位。
列表查詢返回 ID、名稱和關鍵狀態，詳細資訊透過下一次工具呼叫按 ID 查詢。不要每次把完整
行情、完整日誌或整張帳單表複製到對話歷史。

### 3. 歷史訊息分層

- 保留最近幾輪原始訊息和工具結果；
- 將更早的對話壓縮成穩定摘要；
- 將可複用事實放入宿主的結構化 context 或外部儲存，按需檢索；
- 對單次任務設定最大輸入長度。

### 4. 快取和重複呼叫保護

對相同使用者、標的、時間視窗和引數的只讀查詢做短 TTL 快取；在工具層或宿主層去重
併發請求。runtime 自帶連續相同 tool call 熔斷，宿主還可以在 <code>ToolPolicy</code>
或工具適配層增加更細的冪等鍵。

### 5. 預算和可觀測性

根據任務型別設定 <code>RunLimits</code>，記錄每步 token、工具耗時、重試次數和錯誤碼。
發現工具呼叫異常增長時，優先檢查模型提示、工具描述和權限策略，而不是簡單無限增大
<code>max_steps</code>。

## 錯誤處理與狀態

工具異常會在 runtime 邊界被轉換為穩定的 <code>ToolResult</code>/<code>RunResult</code>，
不會把資料庫堆疊直接傳送給模型或瀏覽器。常見終態包括：

| 狀態 | 含義 |
| --- | --- |
| <code>completed</code> | 模型返回最終答案 |
| <code>waiting_for_approval</code> | 有待處理的審批卡 |
| <code>partial</code> | 達到步驟/工具/超時上限，或工具失敗 |
| <code>failed</code> | runtime 邊界發生未預期錯誤 |
| <code>cancelled</code> | 宿主取消了任務 |
| <code>pending</code> / <code>running</code> | 宿主持久化或展示中的中間狀態 |

對外介面應優先使用 <code>RunResult.error_code</code> 做機器判斷，再用事件中的
<code>summary</code> 做人類可讀提示。不要依賴異常文本作為前端協議。

## 測試與本地開發

在倉庫根目錄執行 runtime 測試：

~~~bash
python -m pytest packages/pan-agent-runtime/tests -q
~~~

建議宿主專案至少覆蓋：

- 只讀工具能被暴露並執行；
- 寫工具會生成審批而不是直接執行；
- 拒絕審批不會產生業務寫入；
- 部分審批只執行已決定的呼叫；
- checkpoint 序列化後可以在新程式恢復；
- 工具超時、重試、重複呼叫和未知工具會得到預期錯誤碼；
- 事件順序與前端流式協議一致。

## 獨立釋出檢查清單

釋出新版本前建議確認：

1. 更新 <code>pyproject.toml</code> 中的 <code>version</code>；
2. 檢查 <code>README.md</code> 示例與公共 API 一致；
3. 執行 <code>python -m pytest packages/pan-agent-runtime/tests -q</code>；
4. 構建 wheel 和 source distribution：

   ~~~bash
   python -m pip install build
   python -m build packages/pan-agent-runtime
   ~~~

5. 在乾淨虛擬環境安裝生成的 wheel 並執行最小示例；
6. 透過 PyPI Trusted Publishing 或專案約定的釋出流水線上傳；
7. 為破壞性 API 變更升級主版本或明確記錄遷移說明。

## 版本相容原則

<code>0.x</code> 階段允許在小版本中調整尚未穩定的細節，但應儘量保持以下邊界穩定：

- <code>ToolSpec</code>、<code>ToolResult</code>、<code>RunRequest</code>、<code>RunResult</code>
  的欄位語義；
- <code>ModelPort</code>、<code>ToolPolicy</code>、<code>EventSink</code> 的非同步呼叫約定；
- <code>RuntimeEvent</code> 的事件型別和核心欄位；
- checkpoint 能否被同版本宿主恢復。

業務專案不應依賴 <code>pan_agent.runtime</code> 內部私有函式或未匯出的實現細節，只從
<code>pan_agent</code> 頂層匯入公共 API。
