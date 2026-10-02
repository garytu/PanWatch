from datetime import datetime

import pytest

from marketdata import MarketData, SourceConfig
from marketdata.defaults import StaticConfigProvider
from marketdata.symbol import Symbol
from marketdata.vendors import twmd


@pytest.fixture(autouse=True)
def clear_catalog():
    twmd._catalog_cache.clear()
    yield
    twmd._catalog_cache.clear()


def instrument(identity, **extra):
    return {"instrument_id": identity, "symbol": identity.split(":")[1], "venue": identity.split(":")[0],
            "name": identity, "is_active": True, "security_type": "EQUITY", **extra}


def snapshot(identity="TWSE:2330"):
    return {"instrument_id": identity, "symbol": identity.split(":")[1], "name": "台積電", "provider": "TWSE",
            "close": "100", "open": "99", "high": "101", "low": "98", "volume": 10000, "value": 1000000,
            "trade_date": "2026-09-29", "change": "2", "change_pct": "2.04", "change_basis": "raw_close",
            "previous_observation": {"close": "98", "trade_date": "2026-09-24"}, "adjustment_mode": "raw",
            "availability": {"status": "available", "freshness": {"status": "current"}},
            "units": {"currency": "TWD", "volume": "shares"}}


def live(identity="TWSE:2330", **extra):
    return {"instrument_id": identity, "symbol": identity.split(":")[1], "name": "台積電", "provider": "shioaji",
            "availability": "available", "last_price": "105", "previous_close": None, "reference_price": "100",
            "change": "5", "change_pct": "5", "change_basis": "shioaji_contract_reference",
            "observed_at": "2026-09-29T10:00:00+08:00", "trade_date": "2026-09-29", "volume": 123456,
            "volume_semantics": "cumulative_regular_session", "turnover": "12962900",
            "freshness": {"status": "fresh", "usable_for_trading": True, "max_price_age_seconds": 30},
            "collection_health": {"status": "connected"}, "eod_fallback": snapshot(identity), **extra}


def test_native_quote_contract_preserves_venue_units_and_reference(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return {"quotes": [live("TPEX:6488")]}
    monkeypatch.setattr(twmd, "market_get", get)
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="twmd", config={"timeout_sec": 12})]}))
    quote = md.quotes(["6488.TWO"], market="TW")[0]
    assert calls[0][0] == "http://127.0.0.1:8000/api/v1/quotes"
    assert calls[0][1]["params"] == {"instrument_ids": "TPEX:6488", "include_eod": "true"}
    assert calls[0][1]["timeout"] == 12
    assert quote.symbol == "TPEX:6488" and quote.venue == "TPEX"
    assert quote.current_price == 105 and quote.prev_close is None and quote.reference_price == 100
    assert quote.change_basis == "shioaji_contract_reference"
    assert quote.volume == 123456 and quote.volume_semantics == "cumulative_regular_session"
    assert quote.timestamp == datetime.fromisoformat("2026-09-29T10:00:00+08:00")
    assert quote.usable_for_trading is True


@pytest.mark.parametrize("state", ["stale", "closed", "unknown", "no_trade", "suspended"])
def test_nonfresh_live_quote_never_uses_eod_to_enable_trading(monkeypatch, state):
    row = live(freshness={"status": state, "usable_for_trading": False})
    monkeypatch.setattr(twmd, "market_get", lambda *args, **kw: {"quotes": [row]})
    quote = twmd.TwmdQuoteVendor().fetch([Symbol.parse("TWSE:2330")], {})[0]
    assert quote.current_price == 105 and quote.price_kind == "live"
    assert quote.usable_for_trading is False and quote.eod_fallback["close"] == "100"


def test_eod_is_display_only_without_fabricated_timestamp(monkeypatch):
    row = live(last_price=None, observed_at=None, availability="unavailable",
               freshness={"status": "closed", "usable_for_trading": False})
    monkeypatch.setattr(twmd, "market_get", lambda *args, **kw: {"quotes": [row]})
    quote = twmd.TwmdQuoteVendor().fetch([Symbol.parse("TWSE:2330")], {})[0]
    assert quote.current_price == 100 and quote.price_kind == "eod"
    assert quote.timestamp is None and quote.trade_date == "2026-09-29"
    assert quote.adjustment_mode == "raw" and quote.units["volume"] == "shares"
    assert quote.usable_for_trading is False


def test_unknown_and_unsubscribed_quotes_remain_null(monkeypatch):
    monkeypatch.setattr(twmd, "market_get", lambda *args, **kw: {"quotes": []})
    quote = twmd.TwmdQuoteVendor().fetch([Symbol.parse("TWSE:9999")], {})[0]
    assert quote.current_price is None and quote.timestamp is None
    assert quote.availability == "unavailable" and quote.usable_for_trading is False


def test_bare_symbol_resolution_does_not_guess_a_venue(monkeypatch):
    monkeypatch.setattr(twmd, "market_get", lambda *args, **kw: [instrument("TWSE:2330"), instrument("TPEX:2330")])
    quote = twmd.TwmdQuoteVendor().fetch([Symbol.parse("2330", "TW")], {})[0]
    assert quote.current_price is None and quote.availability == "unknown_instrument"
    assert "explicit" in quote.freshness["reason"]


def test_bare_etf_uses_instrument_registry_not_hk_autodetection(monkeypatch):
    calls = []
    def get(url, **kw):
        calls.append(url)
        return [instrument("TWSE:00878")] if url.endswith("instruments") else {"quotes": [live("TWSE:00878")]}
    monkeypatch.setattr(twmd, "market_get", get)
    quotes = twmd.TwmdQuoteVendor().fetch([Symbol.parse("00878", "TW")], {})
    assert quotes[0].symbol == "00878" and quotes[0].instrument_id == "TWSE:00878"
    assert len(calls) == 2


def test_quote_batches_respect_twmd_100_unique_id_limit(monkeypatch):
    calls = []
    def get(url, **kw):
        ids = kw["params"]["instrument_ids"].split(",")
        calls.append(ids)
        return {"quotes": [live(identity) for identity in ids]}
    monkeypatch.setattr(twmd, "market_get", get)
    symbols = [Symbol.parse(f"TWSE:{1000 + i}") for i in range(205)]
    assert len(twmd.TwmdQuoteVendor().fetch(symbols, {})) == 205
    assert [len(ids) for ids in calls] == [100, 100, 5]


def test_daily_bars_skip_missing_prices_and_preserve_raw_semantics(monkeypatch):
    rows = [{"trade_date": f"2026-09-{day:02d}", "open": "100", "close": "101", "high": "102", "low": "99", "volume": 123}
            for day in (23, 24)]
    rows.append({"trade_date": "2026-09-29", "open": None, "close": None, "volume": 0})
    calls = []
    def get(url, **kw):
        calls.append(kw["params"])
        return {"bars": rows, "provider": "TWSE", "adjustment_mode": "raw"}
    monkeypatch.setattr(twmd, "market_get", get)
    bars = twmd.TwmdKlineVendor().fetch([Symbol.parse("TWSE:2330")], {"days": 20000})
    assert calls[0]["limit"] == 1000
    assert [bar.date for bar in bars] == ["2026-09-23", "2026-09-24"]
    assert bars[0].adjustment_mode == "raw" and bars[0].volume_unit == "shares"


def test_incomplete_minute_slots_are_not_zero_price_bars(monkeypatch):
    row = {"timestamp": "2026-09-29T09:00:00+08:00", "open": "100", "close": "101", "high": "102", "low": "99", "volume": 123,
           "status": "observed", "finalized": True}
    rows = [row, {**row, "status": "missing", "close": None}, {**row, "status": "no_trade", "close": None},
            {**row, "finalized": False}]
    monkeypatch.setattr(twmd, "market_get", lambda *args, **kw: {"bars": rows, "adjustment_mode": "provider_reported"})
    bars = twmd.TwmdIntradayVendor().fetch([Symbol.parse("TWSE:2330")], {"timeframe": "5m"})
    assert len(bars) == 1 and bars[0].close == 101
    assert bars[0].date.endswith("+08:00") and bars[0].adjustment_mode == "provider_reported"


def test_taiwan_discovery_uses_current_eod_not_unsubscribed_live_universe(monkeypatch):
    def get(url, **kw):
        if url.endswith("instruments"):
            return [instrument("TWSE:2330"), instrument("TPEX:6488"), instrument("TWSE:9999", security_type="WARRANT")]
        return {"snapshots": [snapshot(), {**snapshot("TPEX:6488"), "value": 2000000},
                              {**snapshot("TWSE:9999"), "availability": {"status": "available", "freshness": {"status": "stale"}}}]}
    monkeypatch.setattr(twmd, "market_get", get)
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="twmd")]}))
    rows = md.hot_stocks(market="TW", mode="turnover", limit=10)
    assert [row.symbol for row in rows] == ["TPEX:6488", "TWSE:2330"]
    assert all(row.price_kind == "eod" for row in rows)
