"""PanWatch 回測模組(Phase 0 地基)。

輕量、純 Python、零第三方依賴的事件式回測核心,服務於:
- 歷史驗證現有 StrategySignalRun 訊號的真實表現
- 為 Phase 2 的因子 IC/IR 與回測驅動調權提供真值
- 與模擬交易(Phase 1)共用 A 股交易成本模型

vectorbt 作為未來可選的向量化升級路徑(見 .docs/quant-framework-comparison.md)。
"""

from src.modules.strategy.backtest.cost_model import CostConfig, CostModel, DEFAULT_COST_MODEL, Fill
from src.modules.strategy.backtest.data_adapter import PriceBar, from_klines, load_price_history
from src.modules.strategy.backtest.engine import (
    Backtester,
    BacktestResult,
    BTTrade,
    Signal,
    fixed_cash_sizer,
    horizon_return,
)
from src.modules.strategy.backtest import metrics

__all__ = [
    "CostConfig",
    "CostModel",
    "DEFAULT_COST_MODEL",
    "Fill",
    "PriceBar",
    "from_klines",
    "load_price_history",
    "Backtester",
    "BacktestResult",
    "BTTrade",
    "Signal",
    "fixed_cash_sizer",
    "horizon_return",
    "metrics",
]
