"""共用 pytest fixtures。

預設情況下所有通知傳送函式被替換為 no-op，避免單測誤發通知。
傳入 --notify 引數可恢復真實傳送（用於整合測試）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def pytest_itemcollected(item):
    """用測試函式的中文 docstring 替換 pytest -v 輸出中的節點名。"""
    doc = (item.function.__doc__ or "").strip().split("\n")[0]
    if doc:
        item._nodeid = f"{item.parent.nodeid}::{doc}"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--notify",
        action="store_true",
        default=False,
        help="啟用真實通知傳送（預設關閉）",
    )


@pytest.fixture(autouse=True)
def _suppress_notifications(request, monkeypatch):
    """自動遮蔽通知傳送，除非傳入 --notify。"""
    if request.config.getoption("--notify"):
        return

    # patch NotifierManager.notify / notify_with_result
    monkeypatch.setattr(
        "src.platform.notifications.notifier.NotifierManager.notify",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "src.platform.notifications.notifier.NotifierManager.notify_with_result",
        AsyncMock(return_value={"success": True, "suppressed": True}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _mock_stock_link_platform(monkeypatch):
    """避免 stock_link 模組訪問資料庫讀取平臺設定。"""
    monkeypatch.setattr(
        "src.modules.administration.stock_link.get_platform",
        lambda: "xueqiu",
    )


@pytest.fixture(autouse=True)
def _clear_market_caches():
    """清空採集層記憶體快取,避免用例間互相汙染(K線/報價/資金流等現按 TTL 快取)。"""
    from src.platform.marketdata.collectors import (
        capital_flow_collector,
        kline_collector,
    )
    from src.modules.market.api import market as market_api

    def _clear():
        kline_collector.clear_kline_cache()
        capital_flow_collector._FLOW_CACHE.clear()
        market_api.clear_indices_cache()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True, scope="session")
def _ensure_db_schema():
    """確保真實 DB 引擎已建表。

    少數用例直接用 SessionLocal 傳給 async 介面(只讀查詢),CI 全新環境的
    data/panwatch.db 無表會報 'no such table: stocks'。這裡在會話開始時冪等建表
    (本地已有表則無副作用),與各用例自建的記憶體庫互不影響。
    """
    import src.platform.persistence.models  # noqa: F401  註冊所有 ORM 模型到 Base.metadata
    from src.platform.persistence.database import Base, engine

    Base.metadata.create_all(engine)
    yield


# ---------------------------------------------------------------------------
# 共用工廠 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account() -> dict:
    """模擬交易帳戶資料。"""
    return {
        "id": 1,
        "name": "測試帳戶",
        "initial_capital": 100_000.0,
        "current_capital": 100_000.0,
    }


@pytest.fixture
def mock_signal() -> dict:
    """模擬策略訊號。"""
    return {
        "strategy": "trend_follow",
        "symbol": "002837",
        "market": "CN",
        "action": "BUY",
        "confidence": 0.85,
        "reason": "趨勢向上突破",
    }
