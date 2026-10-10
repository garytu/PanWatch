"""模擬交易倉位管理(Phase 1)單元測試 —— 純函式,不觸發 DB/網路。"""

from src.modules.strategy.backtest.cost_model import CostModel
from src.modules.paper_trading.paper_trading_engine import _compute_quantity, _position_weight


def test_position_weight_tiers():
    """訊號強度越高,單筆資金佔比越大(分檔)。"""
    assert _position_weight(90) == 0.25
    assert _position_weight(80) == 0.18
    assert _position_weight(70) == 0.12
    assert _position_weight(50) == 0.08
    assert _position_weight(90) > _position_weight(50)


def test_compute_quantity_respects_budget():
    """按市場預算 × 強度比例分配,買入 100 股整數倍且不超預算對應股數。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=1_000_000, cost_model=cm,
    )
    assert qty > 0 and qty % 100 == 0
    assert qty <= 25000  # 25% 預算 / 10 元


def test_compute_quantity_respects_cash():
    """可用現金不足時回退到買得起的手數,買入含費不超現金。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=3000, cost_model=cm,
    )
    assert qty % 100 == 0
    if qty > 0:
        outlay = -cm.fill("buy", 10.0, qty).cash_delta
        assert outlay <= 3000


def test_compute_quantity_insufficient_cash_returns_zero():
    """現金連最小一手都買不起時返回 0(應跳過建倉)。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=100.0,
        available_cash=500, cost_model=cm,
    )
    assert qty == 0


def test_engine_imports_ok():
    """改造後 paper_trading_engine 可正常匯入(無語法/迴圈 import 錯),關鍵符號在位。"""
    import src.modules.paper_trading.paper_trading_engine as e

    assert hasattr(e, "ENGINE")
    assert hasattr(e, "COST_MODEL")
    assert hasattr(e, "_compute_quantity")
