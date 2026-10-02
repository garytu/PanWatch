"""K線獲取:併發合併 + 失敗負快取。

復活的 Phase 0-4 批次消費者(entry_candidates/strategy_engine/backtest)+ 組合歸因
會在收盤後併發地對同一批標的取 K 線。源短暫故障時,若失敗結果既不快取也不合並,
每個併發消費者都會各自打一次 marketdata 包(出現 "Server disconnected" 日誌風暴),
且空結果不快取導致每輪重複打爆。這裡固化兩條防線:
  1) 同一標的的併發取數合併為一次聯網;
  2) 取數失敗後在冷卻視窗內不再聯網(負快取)。
"""

from __future__ import annotations

import threading
import time

import pytest

from src.platform.marketdata.collectors import kline_collector as kc
from src.platform.marketdata.models import MarketCode


@pytest.fixture(autouse=True)
def _clear_caches():
    """每個用例前後清空程式級快取,避免相互汙染。"""
    for name in ("_KLINE_CACHE", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()
    yield
    for name in ("_KLINE_CACHE", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()


class _FakeMarketData:
    """假的 marketdata.MarketData,只實現 klines(),記錄呼叫次數。"""

    def __init__(self, fetch):
        self._fetch = fetch
        self.calls = 0
        self._guard = threading.Lock()

    def klines(self, symbol, *, market, days, min_count=1):
        with self._guard:
            self.calls += 1
        return self._fetch(symbol, market, days)


def test_failed_fetch_is_negative_cached(monkeypatch):
    """同一標的取數失敗後,冷卻視窗內再次呼叫不再聯網(負快取)。"""
    fake = _FakeMarketData(lambda symbol, market, days: [])
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.CN)
    assert col.get_klines("600519") == []
    assert col.get_klines("600519") == []  # 冷卻視窗內,應直接短路
    assert fake.calls == 1, f"失敗後應負快取,實際聯網 {fake.calls} 次"


def test_concurrent_same_symbol_fetches_coalesced(monkeypatch):
    """同一標的的併發取數應合併為一次聯網(防突發打爆資料來源)。"""

    def slow_fetch(symbol, market, days):
        time.sleep(0.25)
        return []

    fake = _FakeMarketData(slow_fetch)
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.CN)
    threads = [threading.Thread(target=lambda: col.get_klines("600519")) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.calls == 1, f"5 併發應合併為 1 次聯網,實際 {fake.calls} 次"


def test_different_symbols_not_blocked(monkeypatch):
    """不同標的使用不同鎖,不應相互阻塞(各自聯網一次)。"""
    fake = _FakeMarketData(lambda symbol, market, days: [])
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.CN)
    col.get_klines("600519")
    col.get_klines("000001")
    assert fake.calls == 2, f"兩個不同標的各應聯網一次,實際 {fake.calls} 次"


def test_insufficient_result_negative_cached(monkeypatch):
    """取到資料但不足 need(HK 源少量返回)→ 冷卻內不再聯網。

    復現 outcome_eval 刷屏:正快取因 count<need 永不命中,舊邏輯只在"空結果"時負快取,
    導致每輪都重打補全源。
    """

    def short_fetch(symbol, market, days):
        return [
            kc.KlineData(date=f"2026-01-{i + 1:02d}", open=1, close=1, high=1, low=1, volume=1)
            for i in range(30)
        ]

    fake = _FakeMarketData(short_fetch)
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.HK)
    col.get_klines("06082", days=120)  # 拿到 30 < need(120) → 冷卻 + 快取部分
    col.get_klines("06082", days=120)  # 冷卻內,服務快取,不再聯網
    assert fake.calls == 1, f"不足 need 時也應負快取,實際聯網 {fake.calls} 次"
