from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from marketdata.errors import TwmdReadError
from marketdata.vendors.twmd import TwmdClient


def _quantity(provider: str, trade_date: str, *, branch_code: str = "0001", buy: int = 10, sell: int = 4):
    if provider == "capital":
        return {
            "provider": "capital", "dataset": "broker_flow", "instrument_id": "TWSE:2330", "symbol": "2330",
            "trade_date": trade_date, "source_branch_key": "capital:4:1234" + branch_code,
            "branch_code": branch_code, "branch_name": "Capital branch", "native_unit": "lots",
            "precision_shares": 1000, "buy_native": buy, "sell_native": sell, "net_native": buy - sell,
            "buy_vwap": None, "sell_vwap": None, "revision_id": "capital-operation-1",
        }
    return {
        "provider": "twse", "dataset": "broker_flow", "instrument_id": "TWSE:2330", "symbol": "2330",
        "trade_date": trade_date, "source_branch_key": f"twse:{branch_code}",
        "branch_code": branch_code, "branch_name": "TWSE branch", "native_unit": "shares",
        "precision_shares": 1, "buy_native": buy, "sell_native": sell, "net_native": buy - sell,
        "buy_vwap": Decimal("100.000000000000000001"), "sell_vwap": Decimal("99.75"),
        "revision_id": "a" * 64,
    }


def _coverage(provider: str, trade_date: str, status: str = "AVAILABLE", count: int = 1):
    return {
        "provider": provider, "dataset": "broker_flow", "instrument_id": "TWSE:2330",
        "trade_date": trade_date, "status": status, "record_count": count,
        "revision_id": "capital-operation-1" if provider == "capital" else "a" * 64,
        "failure_reason": "archive parse failed" if status == "FAILED" else None,
    }


def test_broker_flow_reads_keep_source_identity_units_revisions_and_decimal_vwap(monkeypatch):
    calls = []

    def response(self, path, **params):
        calls.append((path, params))
        if path == "broker-flow/quantities":
            return [
                _quantity("capital", "2026-07-23"),
                _quantity("twse", "2026-07-24"),
            ], {}
        if path == "broker-flow/coverage":
            return [
                _coverage("capital", "2026-07-23", "MISSING", 0),
                _coverage("twse", "2026-07-24"),
            ], {}
        if path == "broker-flow/price-levels":
            assert params["date"] == "2026-07-24"
            return [{
                "provider": "twse", "dataset": "broker_flow", "instrument_id": "TWSE:2330", "symbol": "2330",
                "trade_date": "2026-07-24", "source_branch_key": "twse:0001", "branch_code": "0001",
                "branch_name": "TWSE branch", "price": Decimal("100.125000000000000001"),
                "buy_native": 5, "sell_native": 2, "native_unit": "shares", "precision_shares": 1,
                "revision_id": "a" * 64,
            }], {}
        raise AssertionError(path)

    monkeypatch.setattr(TwmdClient, "get_response", response)
    client = TwmdClient({})
    quantities = client.broker_flow_quantities(
        "TWSE:2330", "2026-07-23", "2026-07-24", today_taipei=date(2026, 10, 7)
    )
    coverage = client.broker_flow_coverage(
        "TWSE:2330", "2026-07-23", "2026-07-24", today_taipei=date(2026, 10, 7)
    )
    prices = client.broker_flow_price_levels(
        "TWSE:2330", "2026-07-24", today_taipei=date(2026, 10, 7)
    )

    assert [(row.provider, row.source_branch_key, row.native_unit, row.precision_shares) for row in quantities.data] == [
        ("capital", "capital:4:12340001", "lots", 1000),
        ("twse", "twse:0001", "shares", 1),
    ]
    assert quantities.data[1].buy_vwap == Decimal("100.000000000000000001")
    assert quantities.data[1].revision_id == "a" * 64
    assert [row.status for row in coverage.data] == ["MISSING", "AVAILABLE"]
    assert prices.data[0].price == Decimal("100.125000000000000001")
    assert calls[2][1]["date"] == "2026-07-24"


def test_broker_flow_range_limits_are_inclusive_and_checked_before_reads(monkeypatch):
    calls = []

    def response(self, path, **params):
        calls.append((path, params))
        if path == "broker-flow/quantities":
            return [], {}
        if path == "broker-flow/coverage":
            start = date.fromisoformat(params["start"])
            end = date.fromisoformat(params["end"])
            return [
                _coverage("capital" if day < date(2026, 7, 24) else "twse", day.isoformat(), "MISSING", 0)
                for day in (start + timedelta(days=index) for index in range((end - start).days + 1))
            ], {}
        raise AssertionError(path)

    monkeypatch.setattr(TwmdClient, "get_response", response)
    client = TwmdClient({})
    client.broker_flow_quantities("TWSE:2330", "2026-09-01", "2026-10-01", today_taipei=date(2026, 10, 7))
    assert len(calls) == 1
    with pytest.raises(ValueError, match="31 calendar days"):
        client.broker_flow_quantities("TWSE:2330", "2026-09-01", "2026-10-02", today_taipei=date(2026, 10, 7))
    assert len(calls) == 1

    client.broker_flow_coverage("TWSE:2330", "2025-10-06", "2026-10-06", today_taipei=date(2026, 10, 7))
    assert len(calls) == 2
    with pytest.raises(ValueError, match="366 calendar days"):
        client.broker_flow_coverage("TWSE:2330", "2025-10-05", "2026-10-06", today_taipei=date(2026, 10, 7))
    assert len(calls) == 2


def test_broker_flow_price_level_pre_cutover_tpex_and_409_do_not_invent_rows(monkeypatch):
    calls = []

    def response(self, path, **params):
        calls.append((path, params))
        raise TwmdReadError("not materialized", status_code=409, reason_code="http_409")

    monkeypatch.setattr(TwmdClient, "get_response", response)
    client = TwmdClient({})
    before = client.broker_flow_price_levels("TWSE:2330", "2026-07-23", today_taipei=date(2026, 10, 7))
    tpex = client.broker_flow_price_levels("TPEX:5347", "2026-10-02", today_taipei=date(2026, 10, 7))
    tpex_quantities = client.broker_flow_quantities("TPEX:006201", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 7))
    missing = client.broker_flow_price_levels("TWSE:2330", "2026-10-02", today_taipei=date(2026, 10, 7))

    assert before.status == "unsupported"
    assert before.reason == "unsupported_before_twse_bsr_cutover"
    assert tpex.status == "unsupported"
    assert tpex_quantities.status == "unsupported"
    assert missing.status == "not_materialized"
    assert missing.data == []
    assert calls == [("broker-flow/price-levels", {"instrument_id": "TWSE:2330", "date": "2026-10-02", "parse": "json_decimal", "timeout_sec": None})]


def test_empty_price_projection_is_unknown_because_empty_and_failed_are_indistinguishable(monkeypatch):
    monkeypatch.setattr(TwmdClient, "get_response", lambda *_args, **_kwargs: ([], {}))
    read = TwmdClient({}).broker_flow_price_levels(
        "TWSE:2330", "2026-10-02", today_taipei=date(2026, 10, 7)
    )
    assert read.status == "unknown"
    assert read.reason == "materialized_no_rows_status_unknown"


def test_broker_bounds_allow_current_taipei_day_and_reject_floor_future_and_datetime(monkeypatch):
    today = date(2026, 10, 7)
    calls = []
    monkeypatch.setattr(TwmdClient, 'get_response', lambda _self, path, **kw: (calls.append((path, kw)) or [], {}))
    client = TwmdClient({})
    assert client.broker_flow_quantities('TWSE:2330', today, today, today_taipei=today).status == 'unknown'
    assert client.broker_flow_price_levels('TWSE:2330', today, today_taipei=today).status == 'unknown'
    for start, end in [('2023-12-31', '2024-01-01'), ('2026-10-07', '2026-10-08'), ('2026-10-07', '2026-10-06')]:
        with pytest.raises(ValueError):
            client.broker_flow_quantities('TWSE:2330', start, end, today_taipei=today)
        with pytest.raises(ValueError):
            client.broker_flow_coverage('TWSE:2330', start, end, today_taipei=today)
    from datetime import datetime
    with pytest.raises(TypeError):
        client.broker_flow_quantities('TWSE:2330', datetime(2026, 10, 7), today, today_taipei=today)
    assert len(calls) == 2


@pytest.mark.parametrize('changed', [
    {'buy_vwap': '-0.1'}, {'buy_native': True}, {'net_native': 999},
    {'source_branch_key': 'capital:4:12340001'}, {'native_unit': 'lots'},
    {'trade_date': '2026-07-23'}, {'instrument_id': 'TPEX:2330'},
])
def test_invalid_broker_rows_remain_provider_errors(monkeypatch, changed):
    row = {**_quantity('twse', '2026-10-02'), **changed}
    monkeypatch.setattr(TwmdClient, 'get_response', lambda *_a, **_k: ([row], {}))
    with pytest.raises(TwmdReadError) as caught:
        TwmdClient({}).broker_flow_quantities('TWSE:2330', '2026-10-02', '2026-10-02', today_taipei=date(2026, 10, 7))
    assert caught.value.reason_code == 'invalid_response'


def test_duplicate_price_rows_are_rejected_before_they_can_double_count(monkeypatch):
    row = {**_quantity('twse', '2026-10-02'), 'price': '100.00'}
    monkeypatch.setattr(TwmdClient, 'get_response', lambda *_a, **_k: ([row, row], {}))
    with pytest.raises(TwmdReadError) as caught:
        TwmdClient({}).broker_flow_price_levels('TWSE:2330', '2026-10-02', today_taipei=date(2026, 10, 7))
    assert caught.value.reason_code == 'invalid_response'
