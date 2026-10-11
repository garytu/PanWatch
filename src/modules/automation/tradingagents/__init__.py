"""TradingAgents 整合模組。

把 TauricResearch/TradingAgents (多 Agent 投資決策框架,76k star) 適配進 PanWatch:
- 橋接 PanWatch AI Service 到 TradingAgents LLM config
- 把 PanWatch Provider 體系的資料注入 TradingAgents 的 data vendor 層(A 股專用)
- 把 TradingAgents 的 final_state 對映成 PanWatch 的 AnalysisResult
- 透過 LangChain callbacks 回饋進度

軟依賴:`tradingagents` 庫不在 PyPI,需使用者自行 git clone + pip install -e。
未安裝時 TradingAgentsAgent.run() 會返回明確錯誤,不會讓服務 crash。

詳細設計:`.docs/tradingagents/02-technical-design.md`
"""

from src.modules.automation.tradingagents.agent import TradingAgentsAgent
from src.modules.automation.tradingagents.data_context import (
    build_stock_metadata_context,
    patch_instrument_context,
    to_tradingagents_portfolio,
)
from src.modules.automation.tradingagents.decision import map_state_to_result
from src.modules.automation.tradingagents.observability import (
    PanWatchProgressHandler,
    aggregate_progress,
    check_budget,
    estimate_cost,
)

__all__ = [
    "TradingAgentsAgent",
    "PanWatchProgressHandler",
    "aggregate_progress",
    "build_stock_metadata_context",
    "check_budget",
    "estimate_cost",
    "map_state_to_result",
    "patch_instrument_context",
    "to_tradingagents_portfolio",
]
