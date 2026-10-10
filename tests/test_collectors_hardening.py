"""採集層批次整治 P1:共享 market_http(節流/重試/來源)+ 各 collector 快取。

價格提醒所需的量比直接從騰訊報價 parts[49] 取,免再拉 K線;
報價/資金流/異動加 TTL 快取,避免排程任務每輪重複聯網觸發限流。
"""

from __future__ import annotations

import logging

from src.platform.marketdata.collectors import capital_flow_collector, market_http
from src.platform.marketdata.models import MarketCode


def test_capital_flow_cached(monkeypatch):
    """資金流為日級資料,同一只在 TTL 內應命中快取,不重複呼叫 marketdata 包。"""
    from marketdata.types import CapitalFlow as MdCF

    calls = {"n": 0}

    class _MD:
        def capital_flow(self, symbol, *, market="CN"):
            calls["n"] += 1
            return MdCF(
                symbol=symbol, name="貴州茅臺",
                main_net_inflow=100.0, main_net_inflow_pct=1.0,
                super_net_inflow=4.0, big_net_inflow=3.0,
                mid_net_inflow=2.0, small_net_inflow=1.0,
                main_net_5d=None,
            )

    monkeypatch.setattr(capital_flow_collector, "get_market_data", lambda: _MD())
    c = capital_flow_collector.CapitalFlowCollector(MarketCode.CN)
    assert c.get_capital_flow("600519") is not None
    assert c.get_capital_flow("600519") is not None
    assert calls["n"] == 1, f"第二次應命中資金流快取,實際呼叫 {calls['n']} 次"


def test_market_get_retries_and_logs_source(monkeypatch, caplog):
    """market_get 失敗應退避重試,並在日誌帶上 [src=...] 呼叫來源。"""
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(market_http.httpx, "Client", _FakeClient)
    monkeypatch.setattr(market_http.time, "sleep", lambda *_: None)

    with caplog.at_level(logging.WARNING):
        with market_http.fetch_source("unit_src"):
            out = market_http.market_get(
                "http://x", host_key="x", retries=2, log_label="測試"
            )

    assert out is None
    assert calls["n"] == 3, f"應 1 次 + 重試 2 次 = 3 次,實際 {calls['n']}"
    assert any(
        "[src=unit_src]" in r.getMessage() for r in caplog.records
    ), caplog.text
