"""首頁指數 spark(近20日收盤) 注入 + 60s 快取 + fail-soft 測試"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import src.modules.market.api.market as mkt


def _official_read(benchmark_id):
    base = Decimal("22000") if benchmark_id == "TAIEX" else Decimal("260")
    bars = tuple(SimpleNamespace(trade_date=day, close=value) for day, value in (
        ("2026-10-06", base - Decimal("1")), ("2026-10-07", base),
    ))
    return SimpleNamespace(
        benchmark_id=benchmark_id, venue="TWSE" if benchmark_id == "TAIEX" else "TPEX",
        unit="index_points", basis="raw_price_index", price_kind="benchmark_index",
        returned_count=len(bars), bars=bars, provider="TWSE" if benchmark_id == "TAIEX" else "TPEx",
        source_alias="MI_5MINS_HIST" if benchmark_id == "TAIEX" else "tpex_index",
        partial=True, truncated=benchmark_id == "TAIEX",
    )


def _set_twmd_config(monkeypatch):
    import src.platform.marketdata.marketdata_client as client_module
    monkeypatch.setattr(client_module, "twmd_config", lambda: {"base_url": "http://fixture", "token": "test-token"})


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
        def benchmark_bars(self, benchmark_id, **_kwargs):
            return _official_read(benchmark_id)

    def _fake_get_index_klines(code, market, days=120):
        captured_days[code] = days
        return [_K(100 + i) for i in range(20)]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    _set_twmd_config(monkeypatch)
    monkeypatch.setattr(mkt, "get_index_klines", _fake_get_index_klines)

    out = asyncio.run(mkt.get_market_indices())

    assert len(out) == len(mkt.MARKET_INDICES)
    for item in out:
        if item["market"] == "TW":
            assert len(item["spark"]) == 2 and len(item["spark_dates"]) == 2
        else:
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
    _set_twmd_config(monkeypatch)
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
        def benchmark_bars(self, benchmark_id, **_kwargs):
            return _official_read(benchmark_id)

    def _fake_get_index_klines(code, market, days=120):
        call_count["klines"] += 1
        return [_K(100 + i) for i in range(20)]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    _set_twmd_config(monkeypatch)
    monkeypatch.setattr(mkt, "get_index_klines", _fake_get_index_klines)

    out1 = asyncio.run(mkt.get_market_indices())
    out2 = asyncio.run(mkt.get_market_indices())

    assert out1 == out2
    assert call_count["quotes"] == 1
    assert call_count["klines"] == len(mkt.MARKET_INDICES) - 2  # Taiwan official bars use the benchmark endpoint.


def test_official_benchmark_partial_truncated_cache_preserves_flags_and_defensive_copies(monkeypatch):
    mkt.clear_indices_cache()
    _set_twmd_config(monkeypatch)
    calls = []

    class _MD:
        def benchmark_bars(self, benchmark_id, **_kwargs):
            calls.append(benchmark_id)
            return _official_read(benchmark_id)

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    item = {"symbol": "TAIEX", "name": "加權指數", "benchmark_id": "TAIEX", "expected_venue": "TWSE"}
    first = mkt._official_benchmark_for(item)
    assert first["availability"] == "available"
    assert first["source_partial"] is True and first["source_truncated"] is True
    first["spark"].clear()
    first["spark_dates"][0] = "mutated"
    second = mkt._official_benchmark_for(item)
    assert calls == ["TAIEX"]
    assert second["spark"] == [21999.0, 22000.0]
    assert second["spark_dates"] == ["2026-10-06", "2026-10-07"]
    assert second["source_partial"] is True and second["source_truncated"] is True


def test_official_benchmark_read_error_is_not_cached(monkeypatch):
    mkt.clear_indices_cache()
    _set_twmd_config(monkeypatch)
    calls = []

    class _MD:
        def benchmark_bars(self, benchmark_id, **_kwargs):
            calls.append(benchmark_id)
            raise RuntimeError("query failed")

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    item = {"symbol": "TAIEX", "name": "加權指數", "benchmark_id": "TAIEX", "expected_venue": "TWSE"}
    assert mkt._official_benchmark_for(item)["availability"] == "unavailable"
    assert mkt._official_benchmark_for(item)["availability"] == "unavailable"
    assert calls == ["TAIEX", "TAIEX"]
