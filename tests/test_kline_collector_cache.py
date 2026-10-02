"""K線採集的快取 / 單次取數(批次整治 P0)。

日K一天只定稿一次,但排程任務每輪都逐只重新聯網拉 → 批次突發觸發第三方限流。
按市場狀態快取 + 摘要單次取數,是止血的核心兩件套。
"""

from __future__ import annotations

from src.platform.marketdata.collectors.market_http import fetch_source
from src.platform.marketdata.collectors import kline_collector
from src.platform.marketdata.models import MarketCode


def _mk_bars(n: int) -> list[kline_collector.KlineData]:
    """造 n 根有波動的日K,夠算各項指標。"""
    out = []
    for i in range(n):
        close = 10.0 + (i % 7)
        out.append(
            kline_collector.KlineData(
                date=f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                open=close,
                close=close,
                high=close + 1,
                low=close - 1,
                volume=100.0 + i,
            )
        )
    return out


class _FakeMarketData:
    """假的 marketdata.MarketData,只實現 klines(),記錄呼叫次數。"""

    def __init__(self, bars):
        self.bars = bars
        self.calls = 0

    def klines(self, symbol, *, market, days, min_count=1):
        self.calls += 1
        return list(self.bars)


def test_get_klines_caches_within_ttl(monkeypatch):
    """同一只 K線在 TTL 內應命中記憶體快取,不重複聯網(避免批次突發觸發限流)。"""
    fake = _FakeMarketData(_mk_bars(130))
    monkeypatch.setattr(kline_collector, "get_market_data", lambda: fake)

    c = kline_collector.KlineCollector(MarketCode.CN)
    c.get_klines("600519", days=120)
    c.get_klines("600519", days=120)

    assert fake.calls == 1, f"第二次應命中快取,實際聯網 {fake.calls} 次"


def test_cache_serves_shorter_request_from_longer_entry(monkeypatch):
    """快取裡已有較長序列時,更短的請求應直接切片返回,不再聯網。"""
    fake = _FakeMarketData(_mk_bars(130))
    monkeypatch.setattr(kline_collector, "get_market_data", lambda: fake)

    c = kline_collector.KlineCollector(MarketCode.CN)
    c.get_klines("600519", days=120)          # 取並快取 130 根
    out = c.get_klines("600519", days=30)     # 應從快取切 30 根

    assert fake.calls == 1, f"更短請求應命中快取,實際聯網 {fake.calls} 次"
    assert len(out) == 30


def test_empty_result_negative_cached_then_retries(monkeypatch):
    """取數為空時進入短冷卻:冷卻視窗內不再聯網(擋住併發/相鄰消費者重複打爆源);
    冷卻過後仍會重試,不把瞬時故障永久固化為空。"""
    fake = _FakeMarketData([])
    monkeypatch.setattr(kline_collector, "get_market_data", lambda: fake)

    c = kline_collector.KlineCollector(MarketCode.CN)
    assert c.get_klines("600519", days=120) == []
    assert c.get_klines("600519", days=120) == []
    assert fake.calls == 1, "冷卻視窗內不應重複聯網(防突發打爆資料來源)"

    # 模擬冷卻到期:應重新聯網重試,證明瞬時故障未被永久固化為空
    kline_collector._FAIL_UNTIL.clear()
    assert c.get_klines("600519", days=120) == []
    assert fake.calls == 2, "冷卻過後應重新聯網重試"


def test_get_kline_summary_fetches_klines_once(monkeypatch):
    """K線摘要應只取一次 K線(原來 30天 + 120天雙取),指標複用同一份。"""
    calls = {"n": 0}
    bars = _mk_bars(130)

    def fake_get_klines(self, symbol, days=60):
        calls["n"] += 1
        return list(bars)

    monkeypatch.setattr(
        kline_collector.KlineCollector, "get_klines", fake_get_klines
    )

    summary = kline_collector.KlineCollector(MarketCode.CN).get_kline_summary("600519")

    assert calls["n"] == 1, f"摘要應只取一次 K線,實際 {calls['n']} 次"
    assert summary.get("ma5") is not None, "指標應基於複用的 K線算出"


def test_fetch_source_is_visible_to_marketdata_package(monkeypatch):
    """宿主排程器標註的來源應透傳到 marketdata 包的失敗日誌。"""
    from marketdata.http import source_suffix

    with fetch_source("outcome_eval"):
        assert source_suffix() == " [src=outcome_eval]"
