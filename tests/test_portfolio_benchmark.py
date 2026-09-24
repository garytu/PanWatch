"""組合 vs 基準對比(M2):超額收益 / 資訊比率 / 相對回檔 + 淨值曲線。"""

from __future__ import annotations

from src.platform.marketdata.collectors.kline_collector import KlineData
from src.modules.portfolio import portfolio_benchmark as pb


def _bars(dates_closes):
    return [
        KlineData(date=d, open=c, close=c, high=c, low=c, volume=0) for d, c in dates_closes
    ]


def test_metrics_outperform_flat_benchmark():
    """基準走平、組合上行 → 超額為正、資訊比率為正、相對回檔≈0。"""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"]
    port = [100, 101, 102, 103, 104]
    bench = [100, 100, 100, 100, 100]
    m = pb.compute_benchmark_metrics(dates, port, bench)
    assert m is not None
    assert m["portfolio_return"] == 4.0
    assert m["benchmark_return"] == 0.0
    assert m["excess_return"] == 4.0
    assert m["information_ratio"] > 0
    assert m["relative_drawdown"] == 0.0
    assert len(m["curve"]) == 5 and m["curve"][0]["portfolio"] == 100.0


def test_metrics_identical_series_zero_excess():
    """組合與基準完全相同 → 超額 0、資訊比率 0、相對回檔 0。"""
    dates = ["d1", "d2", "d3"]
    s = [100, 105, 103]
    m = pb.compute_benchmark_metrics(dates, list(s), list(s))
    assert m["excess_return"] == 0.0
    assert m["information_ratio"] == 0.0
    assert m["relative_drawdown"] == 0.0


def test_metrics_invalid_returns_none():
    """長度不足/不等長 → None(不拋)。"""
    assert pb.compute_benchmark_metrics(["d1"], [100], [100]) is None
    assert pb.compute_benchmark_metrics(["d1", "d2"], [100, 101], [100]) is None
    assert pb.compute_benchmark_metrics(["d1", "d2"], [0, 101], [100, 101]) is None


def test_parse_tencent_kline_matches_collector_format():
    """本地 _parse_tencent_kline 解析騰訊 kline JSON 文本,欄位與舊版一致。"""
    text = (
        'kline_dayqfq={"data":{"sh000300":{"day":['
        '["2026-01-02","3900.1","3910.5","3915.0","3895.2","123456"],'
        '["2026-01-03","3910.5","3920.0","3925.0","3905.0","234567"]'
        "]}}}"
    )
    bars = pb._parse_tencent_kline(text, "sh000300")
    assert len(bars) == 2
    b0 = bars[0]
    assert b0.date == "2026-01-02"
    assert b0.open == 3900.1
    assert b0.close == 3910.5
    assert b0.high == 3915.0
    assert b0.low == 3895.2
    assert b0.volume == 123456.0
    b1 = bars[1]
    assert b1.date == "2026-01-03"
    assert b1.close == 3920.0


def test_build_portfolio_benchmark_with_mocked_fetch(monkeypatch):
    """組合走平、基準上行 → 超額為負;基準元資訊回填。"""
    dates = ["2026-01-02", "2026-01-03", "2026-01-04"]
    monkeypatch.setattr(
        pb, "_fetch_benchmark_series", lambda code, days: (dates, [100.0, 110.0, 121.0])
    )

    def fake_fetch(symbol, market):
        return _bars([(d, 10.0) for d in dates])  # 持倉走平

    res = pb.build_portfolio_benchmark(
        [{"symbol": "600519", "market": "CN", "quantity": 100, "fx": 1.0}],
        days=60,
        benchmark_code="000300",
        kline_fetch=fake_fetch,
    )
    assert res is not None
    assert res["benchmark_code"] == "000300"
    assert res["benchmark_label"] == "滬深300"
    assert res["portfolio_return"] == 0.0
    assert res["benchmark_return"] == 21.0
    assert res["excess_return"] == -21.0
    assert res["relative_drawdown"] < 0


def test_build_benchmark_excludes_poor_coverage_holding(monkeypatch):
    """單隻覆蓋極差的持倉(壞源只回最近1根)被剔除並記入 excluded,不再一票否決基準對比。"""
    dates = [f"2026-01-{d:02d}" for d in range(2, 14)]  # 12 個交易日
    monkeypatch.setattr(
        pb, "_fetch_benchmark_series",
        lambda code, days: (dates, [100.0 + i for i in range(len(dates))]),
    )

    def fake_fetch(symbol, market):
        if symbol == "BABA":
            return _bars([(dates[-1], 200.0)])  # 只有最近 1 根 → 覆蓋不足
        return _bars([(d, 10.0 + i * 0.1) for i, d in enumerate(dates)])

    res = pb.build_portfolio_benchmark(
        [
            {"symbol": "600519", "market": "CN", "quantity": 100, "fx": 1.0},
            {"symbol": "BABA", "market": "US", "quantity": 10, "fx": 7.0},
        ],
        days=60,
        kline_fetch=fake_fetch,
    )
    assert res is not None and len(res.get("curve") or []) >= 2
    assert res.get("excluded") == ["BABA"]
