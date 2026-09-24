"""TA load_ohlcv 接管:A股/港股走 PanWatch K線,美股透傳 yfinance。

新上游 get_verified_market_snapshot → load_ohlcv 直連 yfinance,A股(無 .SS)拉不到
→ NoMarketDataError 整個分析失敗。這裡驗證 PanWatch 接管能為 A股構建 OHLCV,且不誤傷美股。
"""

from __future__ import annotations

from datetime import date, timedelta
import threading

import pandas as pd
import pytest

from src.modules.automation.tradingagents import toolkit_adapter as ta
from src.platform.marketdata.collectors.kline_collector import KlineCollector, KlineData


def _sample_klines(n: int = 40) -> list[KlineData]:
    base = date(2026, 4, 1)
    return [
        KlineData(
            date=str(base + timedelta(days=i)),
            open=1.0 + i,
            close=2.0 + i,
            high=3.0 + i,
            low=0.5 + i,
            volume=100.0 + i,
        )
        for i in range(n)
    ]


def test_build_df_columns_and_date_filter(monkeypatch):
    """構建的 DataFrame 含 Date/OHLCV 列,Date 為 datetime,且按 curr_date 截斷。"""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(40))
    df = ta._build_panwatch_ohlcv_df("601238", "2026-04-20")
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert str(df["Date"].dtype).startswith("datetime64")
    assert (df["Date"] <= pd.to_datetime("2026-04-20")).all()
    assert len(df) == 20  # 04-01..04-20


def test_build_df_reuses_injected_klines_before_fetching_again(monkeypatch):
    """驗證快照應複用採集階段的 K 線，避免 analyst 再發一輪外部請求。"""
    cached = _sample_klines(12)

    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("should reuse PanWatch K-lines already in context")

    monkeypatch.setattr(KlineCollector, "get_klines", unexpected_fetch)
    stock = type("Stock", (), {"symbol": "601238"})()
    with ta.panwatch_data_context({"stock": stock, "klines": cached}):
        df = ta._build_panwatch_ohlcv_df("601238", "2026-04-20")

    assert len(df) == 12


def test_build_df_reuses_empty_injected_klines_without_retrying(monkeypatch):
    """採集階段已確認無 K 線時，後續工具不應再次聯網重試同一標的。"""
    calls = []

    def unexpected_fetch(self, symbol, days=60):
        calls.append((symbol, days))
        raise AssertionError("known empty snapshot must not trigger another fetch")

    monkeypatch.setattr(KlineCollector, "get_klines", unexpected_fetch)
    stock = type("Stock", (), {"symbol": "601238"})()
    with ta.panwatch_data_context({"stock": stock, "klines": []}):
        assert ta._build_panwatch_ohlcv_df("601238", "2026-04-20") is None

    assert calls == []


def test_build_df_does_not_reuse_klines_for_another_symbol(monkeypatch):
    """模型誤傳其它程式碼時，不能把當前標的快取冒充成對方行情。"""
    cached = _sample_klines(12)
    fetched = _sample_klines(8)
    calls = []

    def fetch(self, symbol, days=60):
        calls.append((symbol, days))
        return fetched

    monkeypatch.setattr(KlineCollector, "get_klines", fetch)
    stock = type("Stock", (), {"symbol": "601238"})()
    with ta.panwatch_data_context({"stock": stock, "klines": cached}):
        df = ta._build_panwatch_ohlcv_df("300624", "2026-04-20")

    assert len(df) == 8
    assert calls == [("300624", 750)]


def test_cancelled_ta_context_does_not_fetch_another_symbol(monkeypatch):
    """任務超時後，殘留 worker 再呼叫行情工具時必須立即停止。"""
    calls = []

    def unexpected_fetch(self, symbol, days=60):
        calls.append((symbol, days))
        raise AssertionError("cancelled task must not fetch another symbol")

    monkeypatch.setattr(KlineCollector, "get_klines", unexpected_fetch)
    stock = type("Stock", (), {"symbol": "300624"})()
    cancel_event = threading.Event()
    cancel_event.set()

    from src.modules.automation.tradingagents.toolkit_adapter import (
        TradingAgentsCancelled,
        panwatch_data_context,
    )

    with panwatch_data_context(
        {"stock": stock, "klines": _sample_klines(12)},
        cancel_event=cancel_event,
    ):
        with pytest.raises(TradingAgentsCancelled):
            ta._build_panwatch_ohlcv_df("300624", "2026-04-20")

    assert calls == []


def test_load_ohlcv_routes_a_share_to_panwatch(monkeypatch):
    """A股呼叫走 PanWatch,不觸發原生 yfinance load_ohlcv。"""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(10))
    real_calls = {"n": 0}

    def fake_real(*a, **k):
        real_calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_real_load_ohlcv", fake_real)
    df = ta._panwatch_load_ohlcv("601238", "2026-06-18")
    assert not df.empty
    assert real_calls["n"] == 0, "A股不應回落到 yfinance"


def test_load_ohlcv_passthrough_for_us(monkeypatch):
    """美股放行原生 load_ohlcv(yfinance),不被 PanWatch 接管。"""
    sentinel = pd.DataFrame({"Date": [pd.to_datetime("2026-01-01")], "Close": [1.0]})
    monkeypatch.setattr(ta, "_real_load_ohlcv", lambda symbol, curr_date, *a, **k: sentinel)
    out = ta._panwatch_load_ohlcv("AAPL", "2026-06-18")
    assert out is sentinel


def test_load_ohlcv_us_rate_limit_falls_back_to_marketdata(monkeypatch):
    """Yahoo 限流時，美股必須使用 MarketData 返回的真實 K 線，而不是中斷。"""
    from yfinance.exceptions import YFRateLimitError

    calls = []

    def rate_limited(*args, **kwargs):
        raise YFRateLimitError()

    def marketdata_klines(self, symbol, days=60):
        calls.append((self.market.value, symbol, days))
        return _sample_klines(10)

    monkeypatch.setattr(ta, "_real_load_ohlcv", rate_limited)
    monkeypatch.setattr(KlineCollector, "get_klines", marketdata_klines)

    out = ta._panwatch_load_ohlcv("AAPL", "2026-06-18")

    assert len(out) == 10
    assert calls == [("US", "AAPL", 750)]


def test_load_ohlcv_us_service_error_falls_back_to_marketdata(monkeypatch):
    """Yahoo 503 這類可用性錯誤也必須走 MarketData，不得中斷分析。"""
    monkeypatch.setattr(
        ta,
        "_real_load_ohlcv",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("503 Server Error: Service Unavailable")),
    )
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(10))

    out = ta._panwatch_load_ohlcv("AAPL", "2026-06-18")

    assert len(out) == 10


def test_verified_snapshot_returns_unavailable_message_when_all_sources_fail(monkeypatch):
    """行情源全失敗時，驗證快照應返回不可用提示而非向 LangGraph 拋異常。"""
    from tradingagents.dataflows.errors import NoMarketDataError

    def no_data(*args, **kwargs):
        raise NoMarketDataError("AAPL", "AAPL", "all market data sources failed")

    monkeypatch.setattr(ta, "_real_build_verified_market_snapshot", no_data)

    out = ta._safe_build_verified_market_snapshot("AAPL", "2026-06-18")

    assert "Verified market data unavailable for AAPL" in out
    assert "Do not make exact price, indicator, stop-loss, or trade-action claims" in out


def test_verified_snapshot_preserves_indicators_argument_when_degraded(monkeypatch):
    """安全包裝器必須保持上游的 indicators 引數，避免呼叫方因簽名變化中斷。"""
    from tradingagents.dataflows.errors import NoMarketDataError

    seen = {}

    def no_data(symbol, curr_date, look_back_days=30, indicators=None):
        seen["indicators"] = indicators
        raise NoMarketDataError(symbol, symbol, "all market data sources failed")

    monkeypatch.setattr(ta, "_real_build_verified_market_snapshot", no_data)

    out = ta._safe_build_verified_market_snapshot(
        "AAPL", "2026-06-18", indicators=("rsi",)
    )

    assert "Verified market data unavailable for AAPL" in out
    assert seen == {"indicators": ("rsi",)}


def test_install_load_ohlcv_patch_updates_yfinance_indicator_import(monkeypatch):
    """技術指標工具持有的 load_ohlcv 引用也必須接入同一個 US fallback。"""
    from tradingagents.dataflows import market_data_validator, stockstats_utils, y_finance

    def upstream_load_ohlcv(*args, **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_LOAD_OHLCV_PATCHED", False)
    monkeypatch.setattr(ta, "_real_load_ohlcv", None)
    for module in (stockstats_utils, y_finance, market_data_validator):
        monkeypatch.setattr(module, "load_ohlcv", upstream_load_ohlcv)

    ta._ensure_load_ohlcv_patched()

    assert y_finance.load_ohlcv is ta._panwatch_load_ohlcv


def test_load_ohlcv_a_share_no_klines_raises_not_fallback(monkeypatch):
    """A股取不到 K線時,直接拋 NoMarketDataError 報清晰錯,**不回退 yfinance**。

    A股/港股在 Yahoo 無資料 + 限流,回退只會把"K線獲取失敗"變成誤導的"Yahoo no rows"。
    """
    import pytest
    from tradingagents.dataflows.errors import NoMarketDataError

    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: [])
    real_calls = {"n": 0}

    def fake_real(*a, **k):
        real_calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_real_load_ohlcv", fake_real)
    with pytest.raises(NoMarketDataError):
        ta._panwatch_load_ohlcv("601238", "2026-06-18")
    assert real_calls["n"] == 0, "A股拉空不應回退 yfinance"


def test_route_to_vendor_keeps_numeric_requested_symbol(monkeypatch):
    """數字股票程式碼也是合法 ticker，不能因全是數字而複用快取標的。"""
    stock = type("Stock", (), {"symbol": "300624"})()
    monkeypatch.setattr(
        ta,
        "_serve_from_panwatch",
        lambda method_name, symbol, kwargs, args=(): f"served:{symbol}",
    )

    with ta.panwatch_data_context({"stock": stock, "klines": _sample_klines(4)}):
        out = ta._patched_route_to_vendor("get_stock_data", "300624", "2026-06-18")

    assert out == "served:300624"


def test_route_to_vendor_rejects_cached_snapshot_for_different_numeric_symbol(monkeypatch):
    """快取快照與請求標的不一致時，不能靜默把萬興科技資料當成其它股票。"""
    stock = type("Stock", (), {"symbol": "601238"})()
    monkeypatch.setattr(
        ta,
        "_serve_from_panwatch",
        lambda method_name, symbol, kwargs, args=(): "wrong cached data",
    )

    with ta.panwatch_data_context({"stock": stock, "klines": _sample_klines(4)}):
        out = ta._patched_route_to_vendor("get_stock_data", "300624", "2026-06-18")

    assert "DATA_UNAVAILABLE" in out
    assert "300624" in out


def test_route_to_vendor_marks_expected_upstream_outage_as_data_unavailable(monkeypatch):
    """已知外部資料不可用應給 LLM 明確訊號，而不是吞成空字串。"""

    def boom(method_name, *a, **k):
        raise RuntimeError("FRED_API_KEY environment variable is not set")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)
    # get_macro_indicators:首參是指標名(非 A股/港股) → 走上游 passthrough → 明確資料不可用
    out = ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)
    assert "DATA_UNAVAILABLE" in out
    assert "FRED_API_KEY" in out


def test_route_to_vendor_propagates_programming_errors(monkeypatch):
    """呼叫契約/實現錯誤不能偽裝成資料缺失，否則會掩蓋升級迴歸。"""

    def boom(method_name, *a, **k):
        raise TypeError("unexpected keyword argument 'vendor'")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)

    import pytest
    with pytest.raises(TypeError, match="unexpected keyword"):
        ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)


def test_route_to_vendor_does_not_misclassify_generic_not_set_error(monkeypatch):
    """只有資料來源配置缺失才可降級，內部狀態未設定仍應暴露。"""

    def boom(method_name, *a, **k):
        raise RuntimeError("internal state not set")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)

    import pytest
    with pytest.raises(RuntimeError, match="internal state not set"):
        ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)
