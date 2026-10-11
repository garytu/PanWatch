from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from marketdata.benchmarks import decode_benchmark_bars, decode_benchmark_definitions, decode_daily_bars
from marketdata.errors import TwmdReadError
from marketdata.vendors.twmd import TwmdClient


TWSE_URL = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_index"
START = date(2026, 9, 1)
END = date(2026, 10, 6)


def _business_days():
    days = []
    current = START
    while current <= END:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    # The live contract has 24 retained dates and additional MISSING calendar rows.
    return days[:24]


def _benchmark_payload(*, limit=20, benchmark_id="TAIEX"):
    venue, provider, alias, base_url = (
        ("TWSE", "TWSE", "MI_5MINS_HIST", TWSE_URL)
        if benchmark_id == "TAIEX"
        else ("TPEX", "TPEx", "tpex_index", TPEX_URL)
    )
    available = _business_days()
    all_dates = []
    current = START
    while current <= END:
        all_dates.append(current.isoformat())
        current += timedelta(days=1)
    available_set = set(available)
    capture = {
        "2026-09": ("capture-sept", f"{base_url}?date=20260901&response=json" if benchmark_id == "TAIEX" else base_url,
                    "2026-09", "a" * 64),
        "2026-10": ("capture-oct", f"{base_url}?date=20261001&response=json" if benchmark_id == "TAIEX" else base_url,
                    "2026-10", "b" * 64),
    }
    bar_dates = available[-limit:]
    rows = []
    for index, trade_date in enumerate(bar_dates):
        capture_id, source_url, scope, digest = capture[trade_date[:7]]
        close = Decimal("46000.125") + Decimal(index)
        rows.append({
            "benchmark_id": benchmark_id,
            "trade_date": trade_date,
            "open": str(close - 1), "high": str(close + 2), "low": str(close - 2), "close": str(close),
            "unit": "index_points", "basis": "raw_price_index", "revision": 2,
            "capture_id": capture_id, "captured_at": "2026-10-07T08:00:00Z",
            "source_contract": f"{provider} {alias} daily OHLC", "source_alias": alias,
            "source_url": source_url, "request_scope": scope, "payload_sha256": digest,
        })
    provenance = []
    for month, (capture_id, source_url, scope, digest) in capture.items():
        count = sum(item["trade_date"].startswith(month) for item in rows)
        if count:
            provenance.append({
                "capture_id": capture_id, "benchmark_id": benchmark_id,
                "captured_at": "2026-10-07T08:00:00Z", "acquisition_date": "2026-10-07",
                "source_contract": f"{provider} {alias} daily OHLC", "source_alias": alias,
                "source_url": source_url, "request_scope": scope,
                "publication_start": f"{month}-01", "publication_end": f"{month}-30" if month.endswith("09") else "2026-10-06",
                "payload_sha256": digest, "record_count": count,
            })
    return {
        "benchmark_id": benchmark_id, "name": "Index", "name_zh": "指數",
        "venue": venue, "provider": provider, "source_alias": alias,
        "source_contract": f"{provider} {alias} daily OHLC", "source_url": base_url,
        "timeframe": "day", "price_kind": "benchmark_index", "adjustment_mode": "raw_price_index",
        "unit": "index_points", "basis": "raw_price_index", "limit": limit,
        "returned_count": len(rows), "total_count": len(available), "partial": True,
        "truncated": len(available) > len(rows),
        "requested_range": {"start_date": START.isoformat(), "end_date": END.isoformat(), "inclusive": True},
        "coverage_window": {"start_date": START.isoformat(), "end_date": END.isoformat(), "complete": False,
                            "schema_ready": True, "evidence_truncated": False,
                            "available_count": len(available), "missing_count": len(all_dates) - len(available)},
        "coverage": [{"trade_date": item, "status": "AVAILABLE" if item in available_set else "MISSING"} for item in all_dates],
        "gaps": [item for item in all_dates if item not in available_set],
        "bars": rows, "provenance": provenance, "provenance_total_count": len(provenance),
        "provenance_truncated": False, "served_at": "2026-10-07T08:01:00Z",
    }


def _daily_payload(instrument_id="TWSE:2330"):
    venue, symbol = instrument_id.split(":", 1)
    return {
        "instrument_id": instrument_id, "symbol": symbol, "venue": venue, "name": "Issuer",
        "timeframe": "day", "price_kind": "eod", "adjustment_mode": "raw", "provider": venue,
        "units": {"currency": "TWD", "price": "TWD", "volume": "shares", "value": "TWD", "transactions": "trades"},
        "limit": 1000, "returned_count": 2, "partial": True,
        "bars": [
            {"instrument_id": instrument_id, "symbol": symbol, "trade_date": "2026-10-01", "close": "100.25",
             "observation_status": "traded", "coverage": {"dataset": f"{venue.lower()}_daily_price", "partition_key": "2026-10-01", "status": "AVAILABLE", "record_count": 3}},
            {"instrument_id": instrument_id, "symbol": symbol, "trade_date": "2026-10-02", "close": None,
             "observation_status": "no_close", "coverage": {"dataset": f"{venue.lower()}_daily_price", "partition_key": "2026-10-02", "status": "EMPTY", "record_count": 0}},
        ],
        "availability": {"latest_dataset_trade_date": "2026-10-02"},
    }


def test_definitions_are_fixed_official_separate_benchmark_identities():
    definitions = decode_benchmark_definitions({"benchmarks": [
        {"benchmark_id": "TAIEX", "venue": "TWSE", "provider": "TWSE", "source_alias": "MI_5MINS_HIST",
         "price_kind": "benchmark_index", "unit": "index_points", "basis": "raw_price_index", "stock_venue_default": "TAIEX",
         "source_url": TWSE_URL, "source_contract": "twse", "name": "TAIEX", "name_zh": "加權"},
        {"benchmark_id": "TPEX", "venue": "TPEX", "provider": "TPEx", "source_alias": "tpex_index",
         "price_kind": "benchmark_index", "unit": "index_points", "basis": "raw_price_index", "stock_venue_default": "TPEX",
         "source_url": TPEX_URL, "source_contract": "tpex", "name": "TPEX", "name_zh": "櫃買"},
    ]})
    assert [(item.benchmark_id, item.venue, item.unit) for item in definitions] == [
        ("TAIEX", "TWSE", "index_points"), ("TPEX", "TPEX", "index_points")]


def test_taiex_limit_is_a_selection_not_a_coverage_total_and_keeps_month_capture_urls():
    raw = _benchmark_payload(limit=20)
    read = decode_benchmark_bars(raw, benchmark_id="TAIEX", requested_start=START.isoformat(), requested_end=END.isoformat(), limit=20)
    assert read.returned_count == 20 and read.total_count == 24
    assert read.coverage_window["available_count"] == 24
    assert read.truncated and read.partial and len(read.gaps) == 12
    assert read.bars[0].source_url == f"{TWSE_URL}?date=20260901&response=json"
    assert read.bars[-1].source_url == f"{TWSE_URL}?date=20261001&response=json"
    assert read.bars[0].close == Decimal("46000.125")


def test_zero_bars_is_valid_and_remains_empty():
    raw = _benchmark_payload(limit=20)
    raw.update(returned_count=0, total_count=0, partial=True, truncated=False, bars=[], provenance=[], provenance_total_count=0,
               coverage=[{"trade_date": item["trade_date"], "status": "MISSING"} for item in raw["coverage"]],
               gaps=[item["trade_date"] for item in raw["coverage"]])
    raw["coverage_window"].update(available_count=0, missing_count=len(raw["coverage"]))
    read = decode_benchmark_bars(raw, benchmark_id="TAIEX", requested_start=START.isoformat(), requested_end=END.isoformat(), limit=20)
    assert read.bars == () and read.returned_count == 0


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(venue="TPEX"),
    lambda x: x.update(unit="TWD"),
    lambda x: x.update(returned_count=19),
    lambda x: x["bars"][0].update(close="0"),
    lambda x: x["bars"][0].update(close="NaN"),
    lambda x: x["bars"][0].update(close=1.5),
    lambda x: x["bars"][0].update(high="40000"),
    lambda x: x["bars"][0].update(source_url="https://attacker.invalid/index"),
    lambda x: x["coverage_window"].update(available_count=23),
    lambda x: x["bars"][0].update(capture_id="unknown-capture"),
])
def test_malformed_or_inconsistent_benchmark_response_is_rejected(mutate):
    raw = deepcopy(_benchmark_payload(limit=20))
    mutate(raw)
    with pytest.raises(TwmdReadError):
        decode_benchmark_bars(raw, benchmark_id="TAIEX", requested_start=START.isoformat(), requested_end=END.isoformat(), limit=20)


def test_daily_stock_bars_keep_twd_unit_raw_basis_and_null_closes():
    read = decode_daily_bars(_daily_payload(), instrument_id="TWSE:2330", limit=1000)
    assert read.adjustment_mode == "raw" and read.price_kind == "eod" and read.provider == "TWSE"
    assert read.bars[0].close == Decimal("100.25")
    assert read.bars[1].close is None and read.bars[1].observation_status == "no_close"


@pytest.mark.parametrize("mutate", [
    lambda x: x["units"].update(price="index_points"),
    lambda x: x.update(adjustment_mode="split_adjusted"),
    lambda x: x["bars"][0].update(instrument_id="TPEX:2330"),
    lambda x: x["bars"][0]["coverage"].update(partition_key="2026-10-02"),
    lambda x: x["bars"][0]["coverage"].update(status="UNKNOWN"),
    lambda x: x["bars"][0].update(close="0"),
])
def test_daily_stock_contract_rejects_bad_unit_identity_coverage_or_price(mutate):
    raw = deepcopy(_daily_payload())
    mutate(raw)
    with pytest.raises(TwmdReadError):
        decode_daily_bars(raw, instrument_id="TWSE:2330", limit=1000)


def test_client_uses_paired_benchmark_range_and_latest_only_stock_bars(monkeypatch):
    client = TwmdClient({"base_url": "http://fixture", "timeout_sec": 5})
    calls = []
    def fake(path, **kwargs):
        calls.append((path, kwargs))
        if path == "benchmarks/TAIEX/bars":
            return _benchmark_payload(limit=20), {}
        return _daily_payload(), {}
    monkeypatch.setattr(client, "get_response", fake)
    client.benchmark_bars("TAIEX", start_date=START, end_date=END, limit=20)
    client.daily_bars("TWSE:2330", limit=1000)
    assert calls[0][0] == "benchmarks/TAIEX/bars"
    assert calls[0][1]["start_date"] == START.isoformat() and calls[0][1]["end_date"] == END.isoformat()
    assert calls[1][0] == "bars" and calls[1][1]["parse"] == "json_decimal"
    assert "start_date" not in calls[1][1] and "end_date" not in calls[1][1]


@pytest.mark.parametrize('mutate', [
    lambda x: x.update(total_count=25),
    lambda x: x.update(partial=False),
    lambda x: x['coverage_window'].update(complete=True),
    lambda x: x['coverage_window'].update(evidence_truncated=True),
    lambda x: x['coverage_window'].update(schema_ready=False),
    lambda x: x.update(bars=[], returned_count=0, total_count=0, truncated=False,
                       provenance=[], provenance_total_count=0),
])
def test_benchmark_root_flags_must_agree_with_full_calendar_coverage(mutate):
    raw = _benchmark_payload()
    mutate(raw)
    with pytest.raises(TwmdReadError):
        decode_benchmark_bars(raw, benchmark_id='TAIEX', requested_start=START.isoformat(),
                              requested_end=END.isoformat(), limit=20)


@pytest.mark.parametrize('identity', ['TWSE:TAIEX', 'TPEX:TPEX', 'TWSE:1', '2330', 'TWSE:2330?x'])
def test_daily_rejects_non_stock_identity_before_network_reads(monkeypatch, identity):
    client = TwmdClient({'base_url': 'http://fixture'})
    calls = []
    monkeypatch.setattr(client, 'get_response', lambda *args, **kwargs: calls.append(args))
    with pytest.raises((ValueError, TwmdReadError)):
        client.daily_bars(identity)
    with pytest.raises(TwmdReadError):
        decode_daily_bars({}, instrument_id=identity, limit=1000)
    assert calls == []


def test_daily_retains_timezone_aware_acquisition_receipt_and_checksum():
    raw = _daily_payload('TWSE:00632R')
    raw['bars'][0]['coverage'].update(acquired_at='2026-10-07T08:00:00Z', checksum='source-checksum')
    row = decode_daily_bars(raw, instrument_id='TWSE:00632R', limit=1000).bars[0]
    assert row.coverage_dataset == 'twse_daily_price'
    assert row.coverage_record_count == 3
    assert row.coverage_acquired_at == '2026-10-07T08:00:00Z'
    assert row.coverage_checksum == 'source-checksum'
    raw['bars'][0]['coverage']['acquired_at'] = '2026-10-07T08:00:00'
    with pytest.raises(TwmdReadError):
        decode_daily_bars(raw, instrument_id='TWSE:00632R', limit=1000)
