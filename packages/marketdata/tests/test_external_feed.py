"""外部行情與盤中分K Vendor 單測 (全 mock)。"""

from __future__ import annotations

from marketdata.symbol import Market, Symbol
from marketdata.vendors.external_feed import ExternalKlineVendor, ExternalQuoteVendor


def test_external_quote_vendor_parses(monkeypatch):
    vendor = ExternalQuoteVendor()
    fake_response = {
        "ok": True,
        "data": [
            {
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "current_price": 980.0,
                "prev_close": 975.0,
                "open_price": 976.0,
                "high_price": 985.0,
                "low_price": 975.0,
                "change_amount": 5.0,
                "change_pct": 0.51,
                "volume": 25410000,
                "turnover": 24901800000.0,
                "timestamp": "2026-09-24T13:29:58+08:00",
            }
        ],
    }

    monkeypatch.setattr(
        "marketdata.vendors.external_feed.market_get",
        lambda url, **kwargs: fake_response,
    )

    quotes = vendor.fetch([Symbol(Market.TW, "2330")], config={})
    assert len(quotes) == 1
    q = quotes[0]
    assert q.symbol == "2330"
    assert q.market == "TW"
    assert q.current_price == 980.0
    assert q.prev_close == 975.0
    assert q.change_amount == 5.0
    assert q.change_pct == 0.51
    assert q.volume == 25410000


def test_external_quote_vendor_error_fail_soft(monkeypatch):
    vendor = ExternalQuoteVendor()
    monkeypatch.setattr(
        "marketdata.vendors.external_feed.market_get",
        lambda url, **kwargs: None,
    )
    quotes = vendor.fetch([Symbol(Market.TW, "2330")], config={})
    assert quotes == []


def test_external_kline_vendor_parses(monkeypatch):
    vendor = ExternalKlineVendor()
    fake_response = {
        "ok": True,
        "symbol": "2330",
        "data": [
            {
                "date": "2026-09-24 09:00:00",
                "open": 976.0,
                "high": 978.0,
                "low": 975.0,
                "close": 977.0,
                "volume": 1250000.0,
            },
            {
                "date": "2026-09-24 09:01:00",
                "open": 977.0,
                "high": 979.0,
                "low": 976.0,
                "close": 979.0,
                "volume": 840000.0,
            },
        ],
    }

    monkeypatch.setattr(
        "marketdata.vendors.external_feed.market_get",
        lambda url, **kwargs: fake_response,
    )

    bars = vendor.fetch([Symbol(Market.TW, "2330")], config={"timeframe": "1m", "limit": 2})
    assert len(bars) == 2
    assert bars[0].date == "2026-09-24 09:00:00"
    assert bars[0].open == 976.0
    assert bars[0].close == 977.0
    assert bars[1].close == 979.0
