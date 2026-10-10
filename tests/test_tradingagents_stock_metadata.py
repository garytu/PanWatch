"""標的元資訊注入單測 — 修復 LLM 在 A 股 ticker 上瞎編公司的問題。"""

from __future__ import annotations

from src.modules.automation.tradingagents.data_context import build_stock_metadata_context
from src.modules.automation.tradingagents.toolkit_adapter import (
    _stock_meta_header,
    _serve_from_panwatch,
    panwatch_data_context,
)


class _FakeStock:
    def __init__(self, name, symbol, market_value):
        self.name = name
        self.symbol = symbol
        self.market = type("M", (), {"value": market_value})()


def test_metadata_context_has_company_name():
    """meta context 必須包含公司中文名,LLM 不會瞎編"""
    ctx = build_stock_metadata_context(
        stock_symbol="601127",
        stock_name="賽力斯",
        market="CN",
        current_price=83.26,
    )
    assert "賽力斯" in ctx
    assert "601127" in ctx
    assert "A 股" in ctx
    assert "83.26" in ctx
    assert "DO NOT guess" in ctx  # 強制約束


def test_metadata_context_includes_industry():
    """如有行業,加進上下文"""
    ctx = build_stock_metadata_context(
        stock_symbol="601127",
        stock_name="賽力斯",
        market="CN",
        industry="汽車製造",
    )
    assert "汽車製造" in ctx


def test_metadata_context_empty_symbol_returns_empty():
    """空 symbol → 空串"""
    assert build_stock_metadata_context(stock_symbol="") == ""


def test_metadata_context_us_market_label():
    """美股市場標籤正確渲染"""
    ctx = build_stock_metadata_context(stock_symbol="AAPL", stock_name="Apple", market="US")
    assert "美股" in ctx


def test_stock_meta_header_from_cache():
    """工具返回字首包含公司名(來自 panwatch_data_context 注入的資料)"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    quote = {"current_price": 83.26, "change_pct": -2.5, "industry": "汽車"}
    with panwatch_data_context({"stock": stock, "quote": quote}):
        header = _stock_meta_header("601127")
    assert "賽力斯" in header
    assert "601127" in header
    assert "中國 A 股" in header
    assert "83.26" in header
    assert "汽車" in header
    assert "DO NOT guess" in header


def test_serve_fundamentals_includes_company_name():
    """fundamentals 工具返回必須帶公司名,避免 LLM 把 601127 當中國平安"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        result = _serve_from_panwatch("get_fundamentals_openai", "601127", {})
    assert "賽力斯" in result
    assert "601127" in result


def test_serve_news_empty_does_not_leak_global_news():
    """新聞為空時,工具返回明確說"沒有個股新聞",阻止 LLM 拉無關全球新聞"""
    stock = _FakeStock("廣汽集團", "601238", "CN")
    with panwatch_data_context({"stock": stock, "events": []}):
        result = _serve_from_panwatch("get_news", "601238", {})
    assert "廣汽集團" in result
    assert "DO NOT pull unrelated global news" in result


def test_serve_klines_empty_returns_company_aware_message():
    """K 線為空時返回明確空提示,帶公司名"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "klines": []}):
        result = _serve_from_panwatch("get_stockstats_indicators", "601127", {})
    assert "賽力斯" in result
    assert "601127" in result


def test_serve_get_balance_sheet_hits_with_balance_keyword():
    """get_balance_sheet 必須命中(之前沒 balance 關鍵詞,會 MISS)"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        result = _serve_from_panwatch("get_balance_sheet", "601127", {})
    assert "601127" in result
    assert "Balance sheet" in result
    assert "Avoid invented" in result


def test_serve_get_cashflow_distinct_from_balance_sheet():
    """get_cashflow 返回獨立內容,不和 balance sheet 複用同一段文字"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        bs = _serve_from_panwatch("get_balance_sheet", "601127", {})
        cf = _serve_from_panwatch("get_cashflow", "601127", {})
    assert "Cash flow" in cf
    assert bs != cf  # 不能完全一樣


def test_serve_get_stock_data_hits():
    """get_stock_data 必須命中(之前 method 關鍵詞缺 stock_data)"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83, "volume": 1000})()]
    with panwatch_data_context({"stock": stock, "klines": klines, "quote": {}}):
        result = _serve_from_panwatch("get_stock_data", "601127", {})
    assert "2026-05-15" in result  # CSV 命中


def test_serve_get_indicators_without_args_fallback_to_kline_csv():
    """get_indicators 沒傳 indicator 引數時(罕見),fallback 到 K 線 CSV"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83, "volume": 1000})()]
    # args 為空 → 不命中單指標分支,落到 stockstats/yfin 分支返回完整 CSV
    with panwatch_data_context({"stock": stock, "klines": klines, "quote": {}}):
        result = _serve_from_panwatch("get_indicators", "601127", {}, args=())
    # 因為沒匹配到單指標,降級走 stockstats 分支 → 返回完整 K 線 CSV
    assert "2026-05-15" in result


def test_serve_fundamentals_uses_real_quote_data():
    """get_fundamentals 用 quote 真實資料(PE/市值)填充,而不是純空文本"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    quote = {
        "current_price": 83.26,
        "pe_ratio": 25.5,
        "total_market_value": 125_000_000_000,
        "turnover_rate": 3.2,
    }
    with panwatch_data_context({"stock": stock, "quote": quote}):
        result = _serve_from_panwatch("get_fundamentals", "601127", {})
    assert "25.5" in result  # PE
    assert "125000000000" in result or "1.25e" in result.lower()  # 市值
    assert "3.2" in result  # 周轉率
    assert "Lightweight Fundamentals" in result


def test_serve_klines_with_data_returns_csv():
    """K 線有資料時返回 CSV,字首帶公司名"""
    stock = _FakeStock("賽力斯", "601127", "CN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83.26, "volume": 1000})()]
    with panwatch_data_context({"stock": stock, "klines": klines}):
        result = _serve_from_panwatch("get_stockstats_indicators", "601127", {})
    assert "賽力斯" in result
    assert "2026-05-15,80,85,79,83.26,1000" in result


def test_patch_route_to_vendor_handles_positional_args():
    """根因修復:上游 route_to_vendor(method, ticker, ...) 是 positional 呼叫,
    patch 必須接 *args,否則 TypeError 直接放行到 yfinance"""
    import sys
    from unittest.mock import MagicMock
    from src.modules.automation.tradingagents.toolkit_adapter import patch_route_to_vendor

    # 構造一個假的 tradingagents.dataflows.interface 模組用於測試
    fake_ti = MagicMock()
    captured_calls = []

    def original_func(method, *args, **kwargs):
        captured_calls.append((method, args, kwargs))
        return "ORIGINAL_RESULT"

    fake_ti.route_to_vendor = original_func
    fake_module = type(sys)("tradingagents.dataflows.interface")
    fake_module.route_to_vendor = original_func

    # patch sys.modules 讓 toolkit_adapter import 拿到我們的假模組
    sys.modules["tradingagents"] = type(sys)("tradingagents")
    sys.modules["tradingagents.dataflows"] = type(sys)("tradingagents.dataflows")
    sys.modules["tradingagents.dataflows"].interface = fake_module
    sys.modules["tradingagents.dataflows.interface"] = fake_module

    try:
        stock = _FakeStock("賽力斯", "601127", "CN")
        with panwatch_data_context({"stock": stock, "klines": [], "quote": {}}):
            with patch_route_to_vendor():
                # 模擬上游 positional 呼叫:route_to_vendor("get_fundamentals", "601127", "2026-05-17")
                result = fake_module.route_to_vendor("get_fundamentals", "601127", "2026-05-17")

        # 我們的 patch 必須能識別 positional ticker,不能 TypeError
        assert "賽力斯" in result
        assert "601127" in result
        # 不應該放行到 original(那會觸發 captured_calls 增加)
        assert len(captured_calls) == 0
    finally:
        for k in ["tradingagents.dataflows.interface", "tradingagents.dataflows", "tradingagents"]:
            sys.modules.pop(k, None)


def test_patch_route_to_vendor_intercepts_global_news_with_cache():
    """get_global_news(curr_date, look_back_days, limit) 不帶 symbol,
    但 cache 裡有 A 股標的時,必須攔截,避免拉 Yahoo 無關全球新聞"""
    import sys
    from src.modules.automation.tradingagents.toolkit_adapter import patch_route_to_vendor

    def original_func(method, *args, **kwargs):
        return "GLOBAL_SHOE_NEWS_LEAKED"

    fake_module = type(sys)("tradingagents.dataflows.interface")
    fake_module.route_to_vendor = original_func
    sys.modules["tradingagents"] = type(sys)("tradingagents")
    sys.modules["tradingagents.dataflows"] = type(sys)("tradingagents.dataflows")
    sys.modules["tradingagents.dataflows"].interface = fake_module
    sys.modules["tradingagents.dataflows.interface"] = fake_module

    try:
        stock = _FakeStock("賽力斯", "601127", "CN")
        with panwatch_data_context({"stock": stock, "events": [], "quote": {}}):
            with patch_route_to_vendor():
                # get_global_news 第一個引數是日期,不是 ticker
                result = fake_module.route_to_vendor(
                    "get_global_news", "2026-05-17", 7, 20
                )
        assert "GLOBAL_SHOE_NEWS_LEAKED" not in result
        assert "DO NOT pull unrelated global news" in result
    finally:
        for k in ["tradingagents.dataflows.interface", "tradingagents.dataflows", "tradingagents"]:
            sys.modules.pop(k, None)
