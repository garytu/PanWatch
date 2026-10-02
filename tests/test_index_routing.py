"""指數取數(K線 + market.py /indices)路由測試"""
import asyncio

import src.platform.marketdata.collectors.kline_collector as kc
import src.modules.market.api.market as mkt


def test_get_index_klines_uses_marketdata(monkeypatch):
    """get_index_klines 走 md.index_klines(同一 INDEX_SECID 語義),轉換為 KlineData。"""
    from marketdata.types import Bar

    captured: dict = {}

    class _MD:
        def index_klines(self, code, *, market, days):
            captured["code"] = code
            captured["market"] = market
            captured["days"] = days
            return [Bar(date="2026-07-01", open=3180.0, close=3200.0, high=3210.0, low=3170.0, volume=1e8)]

    monkeypatch.setattr(kc, "get_market_data", lambda: _MD())

    out = kc.get_index_klines("000001", kc.MarketCode.CN, days=120)

    assert captured == {"code": "000001", "market": "CN", "days": 120}
    assert len(out) == 1 and isinstance(out[0], kc.KlineData)
    assert out[0].date == "2026-07-01" and out[0].close == 3200.0


def test_get_market_indices_uses_marketdata(monkeypatch):
    """/indices 走 md.index_quotes;quote_map/response_symbol 匹配邏輯與返回欄位不變。"""
    captured: dict = {}

    class _MD:
        def index_quotes(self, tencent_symbols):
            captured["symbols"] = list(tencent_symbols)
            return [
                {
                    "symbol": "000001",
                    "name": "上證指數",
                    "current_price": 3200.0,
                    "change_pct": 0.63,
                    "change_amount": 20.0,
                    "prev_close": 3180.0,
                },
            ]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    # spark 取數不是本用例關注點,樁掉避免真實聯網(見 test_market_indices_spark.py 專測 spark)。
    monkeypatch.setattr(mkt, "get_index_klines", lambda *a, **k: [])

    out = asyncio.run(mkt.get_market_indices())

    assert captured["symbols"] == [idx["tencent_symbol"] for idx in mkt.MARKET_INDICES]
    sh = next(i for i in out if i["symbol"] == "000001")
    assert sh["current_price"] == 3200.0 and sh["change_pct"] == 0.63
    # 未命中行情的指數仍返回基本資訊佔位(current_price=None),匹配邏輯不變
    hsi = next(i for i in out if i["symbol"] == "HSI")
    assert hsi["current_price"] is None
