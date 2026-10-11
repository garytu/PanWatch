"""價格提醒的量比條件應優先用報價欄位,不再無謂地拉 K線(批次整治 P2)。

CN/HK 報價已帶量比(騰訊 parts[49]);僅當報價缺量比(如美股)才回退 K線摘要。
"""

from __future__ import annotations

import asyncio

from src.modules.market.price_alert_engine import PriceAlertEngine
from src.platform.marketdata.models import MarketCode


def test_volume_ratio_uses_quote_not_kline(monkeypatch):
    """報價帶量比時,量比條件直接用報價,不應再拉 K線。"""
    eng = PriceAlertEngine()
    called = {"kline": 0}

    async def fake_kline(market, symbol):
        called["kline"] += 1
        return {"volume_ratio": 9.9}

    monkeypatch.setattr(eng, "_get_kline_summary_cached", fake_kline)

    quote = {"current_price": 10.0, "volume_ratio": 2.5}
    ok, detail = asyncio.run(
        eng._eval_condition(
            {"type": "volume_ratio", "op": ">", "value": 2.0},
            quote,
            MarketCode.CN,
            "600519",
        )
    )

    assert ok is True
    assert detail["actual"] == 2.5
    assert called["kline"] == 0, "有報價量比時不應再拉 K線"


def test_volume_ratio_falls_back_to_kline_when_quote_missing(monkeypatch):
    """報價無量比(如美股)時,量比條件回退到 K線摘要。"""
    eng = PriceAlertEngine()
    called = {"kline": 0}

    async def fake_kline(market, symbol):
        called["kline"] += 1
        return {"volume_ratio": 3.0}

    monkeypatch.setattr(eng, "_get_kline_summary_cached", fake_kline)

    quote = {"current_price": 200.0}  # 無 volume_ratio 欄位
    ok, detail = asyncio.run(
        eng._eval_condition(
            {"type": "volume_ratio", "op": ">", "value": 2.0},
            quote,
            MarketCode.US,
            "AAPL",
        )
    )

    assert ok is True
    assert detail["actual"] == 3.0
    assert called["kline"] == 1, "報價缺量比時應回退 K線一次"
