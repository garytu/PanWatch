# PanAgent Tool Research

`pan-agent-tool-research` 是 `pan-agent-runtime` 的可選外掛，用於在工具數量增長
時提供漸進式工具發現能力。它不屬於 runtime 核心，M0 不連線資料庫、Redis、向量服務
或模型供應商。

## 能力

- `ToolDescriptor`：工具用途、關鍵詞、別名、能力域、風險和資料新鮮度；
- `ToolCatalog`：程式內、版本化的描述後設資料目錄；
- `KeywordToolRetriever`：無模型呼叫的關鍵詞和別名檢索；
- `ToolResearchService`：應用啟用狀態、能力域和宿主 `ToolPolicy` 後返回候選；
- `ToolResearchPlugin`：以 shadow 或 active 模式接入 runtime；active 模式提供模型可呼叫
  的 `tool_search` 虛擬工具。

## 接入

```python
from pan_agent import AgentRuntime
from pan_agent_tool_research import ToolResearchPlugin, ToolResearchService

research = ToolResearchService(
    registry,
    descriptors=my_tool_descriptors,
)
runtime = AgentRuntime(
    model,
    registry,
    policy=policy,
    extensions=[ToolResearchPlugin(research, mode="active")],
)
```

`shadow` 模式只發出 `extension_event`，不改變模型看到的工具集合；`active` 模式會：

1. 保留 Registry 標記為 `direct` 的工具；
2. 暴露一個模型可呼叫的 `tool_search`；
3. 搜尋結果進入下一輪訊息，並將選中的 Deferred 工具按完整 schema 暴露；
4. 每次執行仍由 Runtime Policy 和 Registry 再次校驗。

Registry 中的工具可以透過 `ToolSpec.exposure` 設定為 `direct`、`deferred` 或 `hidden`：

```python
from pan_agent import ToolExposure, ToolSpec

ToolSpec(
    name="get_special_report",
    title="專項報告",
    description="查詢專項報告。",
    exposure=ToolExposure.DEFERRED,
)
```

外掛失敗預設回退到當前 Direct 工具集合，不會因為目錄或檢索服務異常而清空模型工具空間。
M0 的 active 檢索仍然使用程式內關鍵詞、別名和結構化後設資料，不強制呼叫意圖模型。

## 事件

外掛透過 runtime 的通用擴充套件事件發出：

- `extension=tool_research, event=started`；
- `exposure`：當前 Direct 工具和已經載入的工具；
- `candidates_scored`；
- `completed`；
- `searched`：模型實際呼叫 `tool_search` 後的結果；
- `fallback`。

宿主可以把 `RuntimeEvent` 直接寫入自己的任務事件表，也可以忽略外掛事件。外掛不會把
使用者原文寫入事件，搜尋事件只記錄查詢雜湊、候選數量、工具名和版本資訊。

## 開發

```bash
python -m pip install -e packages/pan-agent-runtime
python -m pip install -e packages/pan-agent-tool-research
python -m pytest packages/pan-agent-tool-research/tests -q
```
