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
    from marketdata.vendors.twmd import TwmdClient

    def get_response(self, path, **kw):
        if path == "instruments":
            return [
                instrument("TWSE:2330"), instrument("TPEX:6488"),
                instrument("TWSE:9999", security_type="WARRANT"),
                instrument("TWSE:7777", is_active=False),
                instrument("TWSE:00878", security_type="ETF"),
                instrument("TPEX:00679B", security_type="ETN"),
                instrument("TPEX:1234", security_type="PREFERRED"),
            ], {}
        requested = kw["instrument_ids"].split(",")
        rows = [
            snapshot(identity) if identity != "TPEX:6488" else {**snapshot(identity), "value": 2000000}
            for identity in requested if identity in {"TWSE:2330", "TPEX:6488"}
        ]
        # A faulty provider cannot expand the eligible candidate set by
        # injecting an ID outside the exact requested chunk.
        rows.append({**snapshot("TWSE:9999"), "availability": {"status": "available", "freshness": {"status": "current"}}})
        return {"snapshots": rows}, {}

    monkeypatch.setattr(TwmdClient, "get_response", get_response)
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="twmd")]}))
    rows = md.hot_stocks(market="TW", mode="turnover", limit=10)
    assert [row.symbol for row in rows] == ["TPEX:6488", "TWSE:2330"]
    assert all(row.price_kind == "eod" for row in rows)

    pool = md.taiwan_discovery_pool(mode="turnover", limit=10, max_universe_size=10)
    assert [row.symbol for row in pool.items] == ["TPEX:6488", "TWSE:2330"]
    assert pool.partial_scan is True
    assert pool.excluded_security_type_counts == {"WARRANT": 1, "ETN": 1, "PREFERRED": 1}
    assert pool.eligible_catalog_count == 3
    assert pool.price_snapshot_request_count == 1
    assert pool.scanned_instrument_count == 3
    assert pool.provider_scope == "twmd"


def test_taiwan_discovery_never_falls_back_when_quote_twmd_is_disabled(monkeypatch):
    from marketdata.vendors.twmd import TwmdClient

    monkeypatch.setattr(
        TwmdClient,
        "get_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disabled TWMD was queried")),
    )
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="twmd", enabled=False)]}))
    pool = md.taiwan_discovery_pool(max_universe_size=20)
    assert pool.status == "quote_source_disabled"
    assert pool.items == []
    assert pool.catalog_request_count == 0
    assert pool.price_snapshot_request_count == 0


def test_taiwan_discovery_universe_limit_rejects_non_integer_overrides():
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="twmd")]}))
    with pytest.raises(ValueError, match="positive integer"):
        md.taiwan_discovery_pool(max_universe_size=1.5)


def test_taiwan_discovery_hard_caps_custom_price_universe(monkeypatch):
    from marketdata.vendors.twmd import TwmdClient

    catalog = [instrument(f"TWSE:{10000 + index}") for index in range(2001)]
    requested_batches = []

    def get_response(self, path, **kw):
        if path == "instruments":
            return catalog, {}
        identities = kw["instrument_ids"].split(",")
        requested_batches.append(identities)
        return {"snapshots": [snapshot(identity) for identity in identities]}, {}

    monkeypatch.setattr(TwmdClient, "get_response", get_response)
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="twmd")]}))
    pool = md.taiwan_discovery_pool(limit=1, max_universe_size=100_000)
    assert pool.eligible_catalog_count == 2001
    assert pool.price_universe_selected_count == 2000
    assert pool.scanned_instrument_count == 2000
    assert len(requested_batches) == 20
    assert pool.partial_scan is True


def _intraday_coverage_payload(**extra):
    return {
        "instrument_id": "TWSE:2330",
        "session": "regular",
        "start_date": "2026-10-08",
        "end_date": "2026-10-09",
        "schema_ready": True,
        "coverage_complete": False,
        "coverage": [
            {"trade_date": "2026-10-08", "session": "regular", "status": "available",
             "calendar_status": "observed_open", "expected_minutes": 270, "observed_minutes": 270,
             "missing_minutes": 0, "pending_minutes": 0, "revision": 481,
             "source_contract": "shioaji-1.7.7/KBars/regular", "error": None},
            {"trade_date": "2026-10-09", "session": "regular", "status": "incomplete",
             "calendar_status": "unknown", "expected_minutes": 270, "observed_minutes": 269,
             "missing_minutes": 1, "pending_minutes": 0, "revision": 538,
             "source_contract": "shioaji-1.7.7/KBar/regular", "error": None},
        ],
        "served_at": "2026-10-10T02:17:52.035889+08:00",
        "response_marker": "preserved-upstream-metadata",
        **extra,
    }


def test_intraday_coverage_read_is_typed_bounded_and_preserves_metadata(monkeypatch):
    calls = []
    payload = _intraday_coverage_payload()

    def get_response(self, path, **kwargs):
        calls.append((path, kwargs))
        return payload, {"x-twmd-version": "test"}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", get_response)
    read = twmd.TwmdClient({"timeout_sec": 8}).bars_coverage(
        Symbol.parse("TWSE:2330"), "2026-10-08", "2026-10-09", timeout_sec=2.5,
    )

    assert calls == [("bars/coverage", {
        "timeout_sec": 2.5, "retries": 0, "instrument_id": "TWSE:2330",
        "start_date": "2026-10-08", "end_date": "2026-10-09", "session": "regular",
    })]
    assert read.instrument_id == "TWSE:2330" and read.schema_ready is True
    assert [row.status for row in read.coverage] == ["available", "incomplete"]
    assert read.coverage[1].calendar_status == "unknown"
    assert read.coverage[1].raw["source_contract"] == "shioaji-1.7.7/KBar/regular"
    assert read.raw["response_marker"] == "preserved-upstream-metadata"


@pytest.mark.parametrize("mutation", [
    lambda payload: payload.update(instrument_id="TPEX:2330"),
    lambda payload: payload["coverage"][0].update(instrument_id="TPEX:2330"),
    lambda payload: payload.update(start_date="2026-10-07"),
    lambda payload: payload["coverage"].append(payload["coverage"][0]),
    lambda payload: payload["coverage"][0].update(calendar_status="guessed_weekday"),
    lambda payload: payload["coverage"][0].update(observed_minutes=True),
])
def test_intraday_coverage_read_rejects_conflicting_or_unknown_evidence(monkeypatch, mutation):
    from marketdata.errors import TwmdReadError

    payload = _intraday_coverage_payload()
    mutation(payload)
    monkeypatch.setattr(twmd.TwmdClient, "get_response", lambda *_args, **_kwargs: (payload, {}))

    with pytest.raises(TwmdReadError) as error:
        twmd.TwmdClient({}).bars_coverage(Symbol.parse("TWSE:2330"), "2026-10-08", "2026-10-09")
    assert error.value.reason_code == "invalid_response"


def test_intraday_coverage_read_rejects_ranges_longer_than_30_days():
    with pytest.raises(ValueError, match="30 calendar days"):
        twmd.TwmdClient({}).bars_coverage(
            Symbol.parse("TWSE:2330"), "2026-09-10", "2026-10-10",
        )
