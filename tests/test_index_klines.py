"""② 指數 K線 secid 對映:指數 secid 規則與個股不同,必須顯式對映,否則相對強度永遠取不到數。"""
from src.platform.marketdata.models import MarketCode


def test_get_index_klines_passes_correct_code_and_market(monkeypatch):
    """滬深300/恒生等指數應把對應 index_code/market/days 原樣透傳給 marketdata 包的
    index_klines(secid 對映規則已內聚進 marketdata 包,見
    packages/marketdata/src/marketdata/client.py + packages/marketdata/tests/test_index_methods.py)。
    """
    from src.platform.marketdata.collectors import kline_collector

    captured: dict[str, tuple] = {}

    class _MD:
        def index_klines(self, code, *, market, days):
            captured[code] = (market, days)
            return []

    monkeypatch.setattr(kline_collector, "get_market_data", lambda: _MD())
    kline_collector.get_index_klines("000300", MarketCode.CN, days=120)
    kline_collector.get_index_klines("HSI", MarketCode.HK, days=120)
    assert captured["000300"] == ("CN", 120)
    assert captured["HSI"] == ("HK", 120)


def test_get_index_klines_unknown_returns_empty():
    """未對映的指數(如美股 .INX,東財K線不支援)→ 空列表,fail-soft 不拋。"""
    from src.platform.marketdata.collectors import kline_collector

    assert kline_collector.get_index_klines(".INX", MarketCode.US) == []


def test_fetch_index_context_us_failsoft():
    """美股指數無東財K線 → _fetch_index_context 返回 available False,不拋。"""
    from src.modules.research.context_builder import ContextBuilder

    ctx = ContextBuilder()._fetch_index_context(".INX", MarketCode.US)
    assert ctx.get("available") is False


def test_fetch_index_context_computes_returns(monkeypatch):
    """有指數K線時,_fetch_index_context 用收盤價算 5日/20日收益。"""
    from src.platform.marketdata.collectors import kline_collector
    from src.modules.research.context_builder import ContextBuilder

    class _K:
        def __init__(self, c):
            self.close = c

    # 25 根:從 100 等比每根 +1,最新 124;-6 根=119,-21 根=104
    closes = [_K(100 + i) for i in range(25)]
    monkeypatch.setattr(kline_collector, "get_index_klines", lambda *a, **k: closes)
    ctx = ContextBuilder()._fetch_index_context("000300", MarketCode.CN)
    assert ctx["available"] is True
    assert round(ctx["ret_5d"], 2) == round((124 - 119) / 119 * 100, 2)
    assert round(ctx["ret_20d"], 2) == round((124 - 104) / 104 * 100, 2)
