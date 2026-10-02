# 貢獻指南

感謝你對 PanWatch 的興趣！本檔案將指導你如何貢獻程式碼，特別是如何編寫 Agent 和資料來源。

## 目錄

- [專案結構](#專案結構)
- [開發環境](#開發環境)
- [編寫 Agent](#編寫-agent)
- [編寫資料來源](#編寫資料來源)
- [提交規範](#提交規範)

---

## 專案結構

```
PanWatch/
├── src/
│   ├── agents/           # Agent 實現
│   │   ├── base.py       # 基類和資料結構
│   │   ├── daily_report.py
│   │   └── ...
│   ├── collectors/       # 資料採集器
│   │   ├── news_collector.py
│   │   ├── kline_collector.py
│   │   └── ...
│   ├── core/             # 核心模組
│   │   ├── ai_client.py
│   │   └── notifier.py
│   └── web/              # Web API
├── prompts/              # AI Prompt 模板
├── frontend/             # React 前端
└── server.py             # 入口檔案
```

---

## 開發環境

```bash
# 後端
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py

# 前端
cd frontend
pnpm install
pnpm dev
```

---

## 編寫 Agent

Agent 是 PanWatch 的核心分析單元，負責採集資料、呼叫 AI 分析、傳送通知。

### 1. 建立 Agent 檔案

在 `src/modules/automation/` 目錄建立新檔案，例如 `my_agent.py`：

```python
import logging
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult

logger = logging.getLogger(__name__)

# Prompt 檔案路徑
PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "my_agent.txt"


class MyAgent(BaseAgent):
    """我的自定義 Agent"""

    # 必填：Agent 標識（英文，用於資料庫和 API）
    name = "my_agent"

    # 必填：顯示名稱（中文，用於介面展示）
    display_name = "我的 Agent"

    # 必填：描述
    description = "這是一個自定義 Agent 的示例"

    async def collect(self, context: AgentContext) -> dict:
        """
        採集資料

        Args:
            context: 包含 watchlist（自選股列表）、portfolio（持倉資訊）等

        Returns:
            採集到的資料字典，將傳遞給 build_prompt
        """
        data = {
            "stocks": [],
            "timestamp": datetime.now().isoformat(),
        }

        # 遍歷自選股採集資料
        for stock in context.watchlist:
            # stock.symbol: 股票程式碼
            # stock.name: 股票名稱
            # stock.market: 市場（CN/HK/US）
            pass

        # 獲取持倉資訊
        # context.portfolio.all_positions: 所有持倉列表
        # context.portfolio.get_aggregated_position(symbol): 獲取某隻股票的彙總持倉

        return data

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """
        構建 AI Prompt

        Args:
            data: collect() 返回的資料
            context: Agent 上下文

        Returns:
            (system_prompt, user_content) 元組
        """
        # 讀取 Prompt 模板
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 構建使用者輸入
        lines = []
        lines.append("## 資料")
        # ... 格式化資料

        user_content = "\n".join(lines)
        return system_prompt, user_content

    async def should_notify(self, result: AnalysisResult) -> bool:
        """
        是否傳送通知（可選重寫）

        預設返回 True，可根據分析結果決定是否通知
        """
        # 例如：只有重要訊號才通知
        # return "重要" in result.content
        return True
```

### 2. 建立 Prompt 模板

在 `prompts/` 目錄建立對應的 Prompt 檔案 `my_agent.txt`：

```
你是一個專業的股票分析師。

## 任務
根據提供的資料進行分析...

## 輸出格式
請按以下格式輸出：
1. 概述
2. 詳細分析
3. 建議
```

### 3. 註冊 Agent

在 `server.py` 中註冊：

```python
# 1. 匯入
from src.modules.automation.my_agent import MyAgent

# 2. 新增到 AGENT_REGISTRY
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    # ...
    "my_agent": MyAgent,  # 新增這行
}

# 3. 在 seed_agents() 中新增配置
def seed_agents():
    agents = [
        # ...
        {
            "name": "my_agent",
            "display_name": "我的 Agent",
            "description": "這是一個自定義 Agent",
            "enabled": False,  # 預設停用，使用者手動啟用
            "schedule": "0 16 * * 1-5",  # cron 表示式
            "execution_mode": "batch",  # batch: 批次分析 / single: 逐只分析
        },
    ]
```

### 4. Agent 上下文說明

`AgentContext` 提供以下資訊：

| 屬性 | 型別 | 說明 |
|------|------|------|
| `watchlist` | `list[StockConfig]` | 關聯的自選股列表 |
| `portfolio` | `PortfolioInfo` | 持倉組合資訊 |
| `ai_client` | `AIClient` | AI 使用者端 |
| `notifier` | `NotifierManager` | 通知管理器 |
| `model_label` | `str` | 當前使用的模型標籤 |

### 5. 執行模式

- **batch**：所有股票一起分析，適合日報類
- **single**：逐只股票分析，適合即時監控類

---

## 編寫資料來源

資料來源負責從外部 API 獲取資料（行情、新聞、K線等）。

### 1. 資料來源型別

| 型別 | 說明 | 示例 |
|------|------|------|
| `quote` | 即時行情 | 騰訊行情 |
| `kline` | K線資料 | 騰訊K線 |
| `news` | 新聞資訊 | 東方財富新聞 |
| `capital_flow` | 資金流向 | 東方財富資金 |
| `chart` | K線截圖 | 雪球截圖 |

### 2. 建立資料採集器

以新聞採集器為例，在 `src/collectors/` 建立檔案：

```python
"""我的新聞採集器"""
import logging
from datetime import datetime
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """新聞資料結構"""
    source: str           # 資料來源標識
    external_id: str      # 外部唯一ID
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    url: str = ""


class MyNewsCollector:
    """我的新聞採集器"""

    source = "my_news"

    def __init__(self, config: dict = None):
        """
        初始化

        Args:
            config: 資料來源配置（來自資料庫 DataSource.config）
        """
        self.config = config or {}
        self.api_key = self.config.get("api_key", "")

    async def fetch_news(
        self,
        symbols: list[str] | None = None,
        since: datetime | None = None,
    ) -> list[NewsItem]:
        """
        獲取新聞

        Args:
            symbols: 股票程式碼列表（可選，用於過濾）
            since: 起始時間（可選）

        Returns:
            NewsItem 列表
        """
        results = []

        async with httpx.AsyncClient() as client:
            # 呼叫 API
            resp = await client.get("https://api.example.com/news")
            data = resp.json()

            for item in data:
                results.append(NewsItem(
                    source=self.source,
                    external_id=str(item["id"]),
                    title=item["title"],
                    content=item["content"],
                    publish_time=datetime.fromisoformat(item["time"]),
                    symbols=item.get("symbols", []),
                    url=item.get("url", ""),
                ))

        return results
```

### 3. 註冊資料來源

在 `server.py` 的 `seed_data_sources()` 中新增：

```python
def seed_data_sources():
    sources = [
        # ...
        {
            "name": "我的新聞源",
            "type": "news",
            "provider": "my_news",  # 對應 collector 的 source
            "config": {
                "api_key": "",  # 使用者在介面配置
            },
            "enabled": False,
            "priority": 10,  # 優先順序，數字越小優先順序越高
            "supports_batch": True,  # 是否支援批次查詢
            "test_symbols": ["600519"],  # 測試用股票程式碼
        },
    ]
```

### 4. 在 Agent 中使用資料來源

```python
from src.platform.marketdata.collectors.my_collector import MyNewsCollector

class MyAgent(BaseAgent):
    async def collect(self, context: AgentContext) -> dict:
        collector = MyNewsCollector()
        news = await collector.fetch_news(
            symbols=[s.symbol for s in context.watchlist]
        )
        return {"news": news}
```

---

## 提交規範

### Commit 格式

```
<type>: <subject>

<body>
```

**Type 型別：**
- `feat`: 新功能
- `fix`: Bug 修復
- `docs`: 檔案更新
- `refactor`: 重構
- `style`: 格式調整
- `test`: 測試相關

**示例：**
```
feat: 新增盤中監控 Agent

- 支援價格異動檢測
- 支援成交量異動檢測
- AI 智慧判斷是否需要通知
```

### PR 要求

1. 確保程式碼透過 lint 檢查
2. 新增功能需要更新檔案
3. Agent 需要提供 Prompt 模板
4. 資料來源需要說明 API 來源和限制

---

## 問題回饋

如有問題，請提交 Issue 或 PR。
