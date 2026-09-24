"""首頁指數 spark(近20日收盤) 注入 + 60s 快取 + fail-soft 測試"""
import asyncio

import src.modules.market.api.market as mkt


class _K:
    """極簡 K 線樁,只需 .close 供 spark 取值。"""

    def __init__(self, close: float):
        self.close = close


def test_spark_injected_for_each_index(monkeypatch):
    """每個指數都應附上 spark(近20日收盤價列表),與 get_index_klines 返回的 close 序列一致。"""
    mkt.clear_indices_cache()

    captured_days: dict[str, int] = {}

    def _fake_quotes(tencent_symbols):
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

    class _MD:
        def index_quotes(self, tencent_symbols):
            return _fake_quotes(tencent_symbols)

    def _fake_get_index_klines(code, market, days=120):
        captured_days[code] = days
        return [_K(100 + i) for i in range(20)]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", _fake_get_index_klines)

    out = asyncio.run(mkt.get_market_indices())

    assert len(out) == len(mkt.MARKET_INDICES)
    for item in out:
        assert item["spark"] == [100 + i for i in range(20)]
    # 近20日收盤:days=20 原樣透傳
    assert all(d == 20 for d in captured_days.values())


def test_spark_failsoft_on_error_or_unmapped(monkeypatch):
    """單指數取 spark 異常(如美股指數無 INDEX_SECID 對映)→ spark=[],不影響 quote 主體也不拋異常。"""
    mkt.clear_indices_cache()

    class _MD:
        def index_quotes(self, tencent_symbols):
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

    def _boom(code, market, days=120):
        raise RuntimeError(f"boom for {code}")

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", _boom)

    out = asyncio.run(mkt.get_market_indices())

    # quote 主體不受影響:上證指數仍返回正確行情
    sh = next(i for i in out if i["symbol"] == "000001")
    assert sh["current_price"] == 3200.0
    assert sh["spark"] == []
    # 未對映/取數失敗的指數(如美股)同樣 spark=[] 且仍在結果裡
    assert all(i["spark"] == [] for i in out)
    assert len(out) == len(mkt.MARKET_INDICES)


def test_indices_response_cached_60s(monkeypatch):
    """整個 indices 回應加 60s 程式內快取:短時間內重複呼叫不應重複拉取 quote/K線。"""
    mkt.clear_indices_cache()

    call_count = {"quotes": 0, "klines": 0}

    class _MD:
        def index_quotes(self, tencent_symbols):
            call_count["quotes"] += 1
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

    def _fake_get_index_klines(code, market, days=120):
        call_count["klines"] += 1
        return [_K(100 + i) for i in range(20)]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", _fake_get_index_klines)

    out1 = asyncio.run(mkt.get_market_indices())
    out2 = asyncio.run(mkt.get_market_indices())

    assert out1 == out2
    assert call_count["quotes"] == 1
    assert call_count["klines"] == len(mkt.MARKET_INDICES)  # 只在第一次呼叫時逐指數拉取一次
