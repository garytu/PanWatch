"""stock 行情直連方收口路由測試:驗證 dict 形狀與 StockData 形狀站點均已改走
marketdata 包的相容層(md_quote_rows / md_stock_data),而非舊的
_fetch_tencent_quotes / AkshareCollector.get_stock_data 直連。
"""

import asyncio

from src.platform.marketdata.models import MarketCode, StockData


def test_insights_fundamental_context_uses_md_quote_rows(monkeypatch):
    """insights._fetch_fundamental_context(dict 消費方)應呼叫 md_quote_rows 而非舊直連。"""
    import src.modules.research.api.insights as insights

    calls = []

    def _fake_md_quote_rows(symbols, market):
        calls.append((list(symbols), market))
        return [{
            "symbol": symbols[0], "pe_ratio": 10.0, "turnover_rate": 1.0,
            "circulating_market_value": None, "total_market_value": None,
            "high_price": None, "low_price": None, "prev_close": None,
        }]

    monkeypatch.setattr(insights, "md_quote_rows", _fake_md_quote_rows)
    result = asyncio.run(insights._fetch_fundamental_context("600519", "CN"))

    assert calls == [(["600519"], "CN")]
    assert "市盈率" in result


def test_entry_candidates_seed_inputs_uses_md_stock_data(monkeypatch):
    """entry_candidates._load_market_scan_seed_inputs(StockData 消費方)應呼叫 md_stock_data。"""
    import src.modules.strategy.entry_candidates as ec

    calls = []

    def _fake_md_stock_data(symbols, market):
        calls.append((list(symbols), market))
        return [StockData(
            symbol=symbols[0], name="貴州茅臺", market=MarketCode(market),
            current_price=1800.0, change_pct=1.2, change_amount=20.0,
            volume=1000.0, turnover=1_000_000.0,
            open_price=1780.0, high_price=1820.0, low_price=1770.0, prev_close=1780.0,
        )]

    monkeypatch.setattr(ec, "md_stock_data", _fake_md_stock_data)
    out = ec._load_market_scan_seed_inputs(market="CN", limit=15)

    assert calls and calls[0][1] == "CN"
    key = f"CN:{calls[0][0][0]}"
    assert key in out
    assert out[key]["symbol"] == calls[0][0][0]
