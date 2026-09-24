"""marketdata 包接入下,外層快取/冷卻機制仍應生效。

kline_collector._fetch_all_sources 與 capital_flow_collector.get_capital_flow
(均已去 flag,恆定走包)內層取數都走 marketdata 包
(get_market_data().klines()/.capital_flow()),但外層的
_KLINE_CACHE/_get_fetch_lock/_FAIL_UNTIL(kline)與 _FLOW_CACHE(capital_flow)
是取數路徑無關的——包一層皮不管裡面換了哪條取數路徑,都不應重複聯網。
這裡 mock 包層,驗證外層機制在新路徑下依然成立。
"""

from __future__ import annotations

from src.platform.marketdata.collectors import capital_flow_collector as cfc
from src.platform.marketdata.collectors import kline_collector as kc
from src.platform.marketdata.models import MarketCode

from marketdata.types import Bar
from marketdata.types import CapitalFlow as MDCapitalFlow


def _mk_bars(n: int) -> list[Bar]:
    """造 n 根 marketdata.types.Bar,供假包層返回。"""
    return [
        Bar(date=f"2026-01-{(i % 28) + 1:02d}", open=10.0, close=10.0, high=11.0, low=9.0, volume=100.0 + i)
        for i in range(n)
    ]


class _FakeMarketData:
    """假的 marketdata.MarketData,只實現 klines(),記錄呼叫次數。"""

    def __init__(self, bars: list[Bar]):
        self.bars = bars
        self.calls = 0

    def klines(self, symbol: str, market: str, days: int, min_count: int) -> list[Bar]:
        self.calls += 1
        return list(self.bars)


def test_flagon_kline_cache_within_ttl(monkeypatch):
    """走 marketdata 包下,TTL 內第二次取數仍應命中 _KLINE_CACHE,不重複呼叫包。"""
    fake = _FakeMarketData(_mk_bars(30))
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.US)
    out1 = col.get_klines("AAPL", days=20)
    out2 = col.get_klines("AAPL", days=20)

    assert fake.calls == 1, f"第二次應命中快取,實際呼叫包 {fake.calls} 次"
    assert len(out1) == 20
    assert len(out2) == 20


def test_flagon_kline_insufficient_bars_triggers_cooldown(monkeypatch):
    """包返回條數不足 need 時應固化失敗冷卻,冷卻視窗內不再呼叫包。"""
    fake = _FakeMarketData(_mk_bars(5))
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.US)
    out1 = col.get_klines("AAPL", days=100)  # 只拿到 5 < need(100) → 冷卻
    out2 = col.get_klines("AAPL", days=100)  # 冷卻視窗內,應直接服務快取,不再呼叫包

    assert fake.calls == 1, f"冷卻視窗內不應重複呼叫包,實際 {fake.calls} 次"
    assert len(out1) == 5
    assert len(out2) == 5


class _FakeMarketDataCF:
    """假的 marketdata.MarketData,只實現 capital_flow(),記錄呼叫次數。"""

    def __init__(self, flow: MDCapitalFlow):
        self.flow = flow
        self.calls = 0

    def capital_flow(self, symbol: str, market: str) -> MDCapitalFlow:
        self.calls += 1
        return self.flow


def test_flagon_capital_flow_cache_within_ttl(monkeypatch):
    """走 marketdata 包下,TTL 內第二次取數仍應命中 _FLOW_CACHE,不重複呼叫包。"""
    fixed = MDCapitalFlow(
        symbol="600519",
        name="貴州茅臺",
        main_net_inflow=111.0,
        main_net_inflow_pct=1.1,
        super_net_inflow=222.0,
        big_net_inflow=333.0,
        mid_net_inflow=444.0,
        small_net_inflow=555.0,
        main_net_5d=666.0,
    )
    fake = _FakeMarketDataCF(fixed)
    monkeypatch.setattr(cfc, "get_market_data", lambda: fake)

    col = cfc.CapitalFlowCollector(MarketCode.CN)
    out1 = col.get_capital_flow("600519")
    out2 = col.get_capital_flow("600519")

    assert fake.calls == 1, f"第二次應命中快取,實際呼叫包 {fake.calls} 次"
    assert out1 is not None and out1.main_net_inflow == 111.0
    assert out2 is not None and out2.main_net_inflow == 111.0
