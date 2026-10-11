from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from marketdata.errors import TwmdReadError
from marketdata import MarketData, SourceConfig
from marketdata.defaults import StaticConfigProvider
from marketdata.http import market_get
from marketdata.vendors.twmd import TwmdClient


def _margin_row(instrument_id="TWSE:2330", trade_date="2026-10-02"):
    venue, symbol = instrument_id.split(":", 1)
    return {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "trade_date": trade_date,
        "margin_balance_previous": 1000,
        "margin_purchase": 20,
        "margin_sale": 10,
        "margin_cash_redemption": 2,
        "margin_balance": 1008,
        "margin_securities_finance_balance": None,
        "margin_utilization_rate": None,
        "margin_quota": 500,
        "short_sale_balance_previous": 100,
        "short_sale": 7,
        "short_cover": 3,
        "short_stock_redemption": 1,
        "short_sale_balance": 103,
        "short_sale_securities_finance_balance": None,
        "short_sale_utilization_rate": None,
        "short_sale_quota": 80,
        "offsetting": 1,
        "note": None,
    }


def _tdcc_rows(variant="bulk_current", report_date="2026-10-02"):
    total_level = 17 if variant == "bulk_current" else 16
    provider = "tdcc_open_data_1_5" if variant == "bulk_current" else "tdcc_qry_stock"
    buckets = []
    for level in range(1, 16):
        label = None
        if variant == "historical_html":
            labels = {
                1: "1-999", 2: "1,000-5,000", 3: "5,001-10,000", 4: "10,001-15,000",
                5: "15,001-20,000", 6: "20,001-30,000", 7: "30,001-40,000", 8: "40,001-50,000",
                9: "50,001-100,000", 10: "100,001-200,000", 11: "200,001-400,000",
                12: "400,001-600,000", 13: "600,001-800,000", 14: "800,001-1,000,000",
                15: "1,000,001以上",
            }
            label = labels[level]
        buckets.append({
            "report_date": report_date,
            "instrument_id": "TWSE:2330",
            "symbol": "2330",
            "report_variant": variant,
            "row_kind": "bucket",
            "source_level": level,
            "source_tier_label": label,
            "holder_count": 1,
            "share_count": 1000 if level < 12 else level * 100000,
            "share_percentage_points": Decimal("1.0"),
            "provider": provider,
            "native_unit": "shares",
        })
    bucket_holders = sum(row["holder_count"] for row in buckets)
    bucket_shares = sum(row["share_count"] for row in buckets)
    if variant == "bulk_current":
        buckets.append({
            "report_date": report_date, "instrument_id": "TWSE:2330", "symbol": "2330",
            "report_variant": variant, "row_kind": "adjustment", "source_level": 16,
            "source_tier_label": None, "holder_count": 0, "share_count": 100,
            "share_percentage_points": Decimal("0"), "provider": provider, "native_unit": "shares",
        })
        total_shares = bucket_shares - 100
    else:
        total_shares = bucket_shares
    buckets.append({
        "report_date": report_date, "instrument_id": "TWSE:2330", "symbol": "2330",
        "report_variant": variant, "row_kind": "total", "source_level": total_level,
        "source_tier_label": "合計", "holder_count": bucket_holders,
        "share_count": total_shares, "share_percentage_points": Decimal("100.00"),
        "provider": provider, "native_unit": "shares",
    })
    return buckets


def test_margin_read_keeps_integer_trading_units_and_coverage_without_cash(monkeypatch):
    calls = []

    def response(self, path, **params):
        calls.append((path, params))
        if path == "margin-short-sale":
            row = _margin_row()
            row["margin_utilization_rate"] = Decimal("1.234567890123456789")
            return [row], {}
        assert path == "coverage"
        assert params["dataset"] == "twse_margin_short_sale"
        return [{
            "dataset": params["dataset"], "partition_key": "2026-10-02",
            "status": "AVAILABLE", "record_count": 1000,
            "acquired_at": "2026-10-03T01:00:00Z", "checksum": "coverage-hash",
        }], {}

    monkeypatch.setattr(TwmdClient, "get_response", response)
    read = TwmdClient({}).margin_short_sale(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 7)
    )

    assert read.status == "available"
    assert read.data[0].margin_balance == 1008
    assert type(read.data[0].margin_balance) is int
    assert read.data[0].margin_utilization_rate == Decimal("1.234567890123456789")
    assert read.data[0].native_unit == "trading_units"
    assert read.coverage[0].acquired_at == "2026-10-03T01:00:00Z"
    assert [call[0] for call in calls] == ["margin-short-sale", "coverage"]
    from src.modules.research.twmd_margin_shareholders import margin_short_sale_block
    public_block = margin_short_sale_block(read)
    assert public_block.data["latest"]["margin_utilization_rate"] == "1.234567890123456789"
    assert type(public_block.data["latest"]["margin_balance"]) is int
    from src.modules.research.taiwan_research import _freshness_metadata
    from datetime import datetime, timezone
    freshness = _freshness_metadata("margin_short_sale", public_block, datetime(2026, 10, 7, tzinfo=timezone.utc))
    assert freshness["data_period"] == "2026-10-02"
    assert freshness["data_period_age_days"] == 5
    assert freshness["source_received_at_utc"] is None


def test_empty_margin_is_unknown_without_coverage_and_missing_with_coverage(monkeypatch):
    def no_coverage(self, path, **params):
        if path == "margin-short-sale":
            return [], {}
        return [], {}

    monkeypatch.setattr(TwmdClient, "get_response", no_coverage)
    unknown = TwmdClient({}).margin_short_sale(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 7)
    )
    assert unknown.status == "unknown"
    assert unknown.reason == "selected_presence_unreported"
    assert unknown.data == []

    def missing(self, path, **params):
        if path == "margin-short-sale":
            return [], {}
        return [{
            "dataset": params["dataset"], "partition_key": "2026-10-02",
            "status": "MISSING", "record_count": 0,
        }], {}

    monkeypatch.setattr(TwmdClient, "get_response", missing)
    absent_partition = TwmdClient({}).margin_short_sale(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 7)
    )
    assert absent_partition.status == "missing"
    assert absent_partition.reason == "coverage_missing"


def test_margin_empty_status_requires_complete_explicit_empty_coverage(monkeypatch):
    def response(self, path, **params):
        if path == "margin-short-sale":
            return [], {}
        return [{
            "dataset": params["dataset"], "partition_key": day,
            "status": state, "record_count": 0,
        } for day, state in (("2026-10-02", "EMPTY"), ("2026-10-03", "EMPTY"),
                            ("2026-10-04", "EMPTY"), ("2026-10-05", "EMPTY"))], {}

    monkeypatch.setattr(TwmdClient, "get_response", response)
    complete = TwmdClient({}).margin_short_sale(
        "TWSE:2330", "2026-10-02", "2026-10-05", today_taipei=date(2026, 10, 7)
    )
    assert complete.status == "empty"
    assert complete.reason == "source_report_explicitly_no_data"

    def incomplete(self, path, **params):
        if path == "margin-short-sale":
            return [], {}
        return [{
            "dataset": params["dataset"], "partition_key": "2026-10-02",
            "status": "EMPTY", "record_count": 0,
        }, {
            "dataset": params["dataset"], "partition_key": "2026-10-05",
            "status": "MISSING", "record_count": 0,
        }], {}

    monkeypatch.setattr(TwmdClient, "get_response", incomplete)
    mixed = TwmdClient({}).margin_short_sale(
        "TWSE:2330", "2026-10-02", "2026-10-05", today_taipei=date(2026, 10, 7)
    )
    assert mixed.status == "unknown"
    assert mixed.reason == "selected_presence_unreported"


def test_margin_http_error_does_not_become_empty(monkeypatch):
    def fail(self, path, **params):
        raise TwmdReadError("HTTP 503", status_code=503, reason_code="http_503")

    monkeypatch.setattr(TwmdClient, "get_response", fail)
    with pytest.raises(TwmdReadError):
        TwmdClient({}).margin_short_sale(
            "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 7)
        )


def test_margin_coverage_failure_preserves_selected_rows(monkeypatch):
    def response(self, path, **params):
        if path == "margin-short-sale":
            return [_margin_row()], {}
        raise TwmdReadError("coverage unavailable", status_code=503, reason_code="http_503")

    monkeypatch.setattr(TwmdClient, "get_response", response)
    read = TwmdClient({}).margin_short_sale(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 7)
    )
    assert read.status == "available"
    assert len(read.data) == 1
    assert read.coverage == []
    assert read.coverage_error_reason == "http_503"


def test_marketdata_margin_compatibility_routes_twmd_and_keeps_native_evidence(monkeypatch):
    def response(self, path, **params):
        if path == "margin-short-sale":
            return [_margin_row()], {}
        return [{
            "dataset": params["dataset"], "partition_key": "2026-10-02",
            "status": "AVAILABLE", "record_count": 10,
        }], {}

    monkeypatch.setattr(TwmdClient, "get_response", response)
    md = MarketData(StaticConfigProvider({
        "margin": [SourceConfig(vendor="twmd", config={"today_taipei": "2026-10-07"})],
    }))
    item = md.margin(["TWSE:2330"], market="TW")[0]

    assert item.quantity_unit == "trading_units"
    assert item.total_balance is None
    assert item.margin_balance_lots == 1008
    assert item.evidence["observation"]["margin_balance"] == 1008
    assert type(item.evidence["observation"]["margin_balance"]) is int


@pytest.mark.parametrize("variant,total_level", [("bulk_current", 17), ("historical_html", 16)])
def test_tdcc_preserves_variant_adjustment_total_and_decimal_percentages(monkeypatch, variant, total_level):
    rows = _tdcc_rows(variant)
    calls = []

    def response(self, path, **params):
        calls.append((path, params))
        if path == "shareholder-distribution":
            assert params["instrument_id"] == "TWSE:2330"
            return rows, {}
        dataset = params["dataset"]
        if dataset.endswith("_history"):
            assert params["start"] == "TWSE:2330|2026-10-02"
        return [], {}

    monkeypatch.setattr(TwmdClient, "get_response", response)
    read = TwmdClient({}).shareholder_distribution("TWSE:2330", "2026-10-02", "2026-10-02")

    assert read.status == "available"
    assert read.data[-1].row_kind == "total"
    assert read.data[-1].source_level == total_level
    assert read.data[-1].share_percentage_points == Decimal("100.00")
    from src.modules.research.twmd_margin_shareholders import shareholder_distribution_block
    public_block = shareholder_distribution_block(read)
    assert public_block.data["latest"]["official_total"]["share_percentage_points"] == "100.00"
    assert [path for path, _ in calls] == ["shareholder-distribution", "coverage", "coverage"]


def test_tdcc_tpex_is_explicitly_unsupported_without_read(monkeypatch):
    calls = []
    monkeypatch.setattr(TwmdClient, "get_response", lambda self, path, **params: calls.append(path))
    read = TwmdClient({}).shareholder_distribution("TPEX:5347", "2026-10-01", "2026-10-07")
    assert read.status == "unsupported"
    assert read.reason == "tdcc_contract_is_twse_four_digit_only"
    assert read.data == [] and calls == []


def test_tdcc_coverage_errors_do_not_erase_present_rows(monkeypatch):
    rows = _tdcc_rows()
    calls = []

    def response(self, path, **params):
        calls.append(path)
        if path == "shareholder-distribution":
            return rows, {}
        raise TwmdReadError("coverage unavailable", status_code=503, reason_code="http_503")

    monkeypatch.setattr(TwmdClient, "get_response", response)
    read = TwmdClient({}).shareholder_distribution("TWSE:2330", "2026-10-02", "2026-10-02")

    assert read.status == "available"
    assert len(read.data) == 17
    assert read.coverage == []
    assert read.coverage_error_reasons == {
        "tdcc_shareholder_distribution": "http_503",
        "tdcc_shareholder_distribution_history": "http_503",
    }
    assert calls == ["shareholder-distribution", "coverage", "coverage"]


@pytest.mark.parametrize(
    "current_status,history_status,expected_status",
    [("EMPTY", "EMPTY", "empty"), ("MISSING", "MISSING", "missing"), ("EMPTY", "MISSING", "unknown")],
)
def test_empty_tdcc_requires_complete_explicit_empty_coverage(
    monkeypatch, current_status, history_status, expected_status
):
    def response(self, path, **params):
        if path == "shareholder-distribution":
            return [], {}
        state = current_status if params["dataset"] == "tdcc_shareholder_distribution" else history_status
        partition = (
            "TWSE:2330|2026-10-02"
            if params["dataset"] == "tdcc_shareholder_distribution_history"
            else "2026-10-02"
        )
        return [{
            "dataset": params["dataset"], "partition_key": partition,
            "status": state, "record_count": 0,
        }], {}

    monkeypatch.setattr(TwmdClient, "get_response", response)
    read = TwmdClient({}).shareholder_distribution("TWSE:2330", "2026-10-02", "2026-10-02")
    assert read.status == expected_status


def test_json_decimal_transport_preserves_decimal_digits(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, params=None):
            return httpx.Response(
                200,
                content=b'{"share_percentage_points":0.1234567890123456789}',
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("marketdata.http.httpx.Client", Client)
    response = market_get("https://example.test", host_key="decimal-test", parse="json_decimal")
    assert response["share_percentage_points"] == Decimal("0.1234567890123456789")


@pytest.mark.parametrize('state', ['EMPTY', 'MISSING'])
def test_partial_coverage_cannot_classify_the_whole_margin_range(monkeypatch, state):
    def response(self, path, **params):
        if path == 'margin-short-sale':
            return [], {}
        return [{'dataset': params['dataset'], 'partition_key': '2026-10-02',
                 'status': state, 'record_count': 0}], {}
    monkeypatch.setattr(TwmdClient, 'get_response', response)
    read = TwmdClient({}).margin_short_sale('TWSE:2330', '2026-10-02', '2026-10-05', today_taipei=date(2026, 10, 7))
    assert read.status == 'unknown'


@pytest.mark.parametrize('variant,dataset', [
    ('bulk_current', 'tdcc_shareholder_distribution'),
    ('historical_html', 'tdcc_shareholder_distribution_history'),
])
def test_tdcc_variant_reads_only_its_own_coverage(monkeypatch, variant, dataset):
    calls = []
    def response(self, path, **params):
        calls.append((path, params))
        if path == 'shareholder-distribution':
            return [], {}
        assert params['dataset'] == dataset
        key = 'TWSE:2330|2026-10-02' if variant == 'historical_html' else '2026-10-02'
        return [{'dataset': dataset, 'partition_key': key, 'status': 'MISSING', 'record_count': 0}], {}
    monkeypatch.setattr(TwmdClient, 'get_response', response)
    read = TwmdClient({}).shareholder_distribution('TWSE:2330', '2026-10-02', '2026-10-02', report_variant=variant)
    assert read.status == 'missing'
    assert len(calls) == 2


def test_tdcc_failed_variant_coverage_cannot_be_reported_as_complete_empty(monkeypatch):
    def response(self, path, **params):
        if path == 'shareholder-distribution':
            return [], {}
        if params['dataset'].endswith('_history'):
            raise TwmdReadError('failed', reason_code='http_503', status_code=503)
        return [{'dataset': params['dataset'], 'partition_key': '2026-10-02',
                 'status': 'EMPTY', 'record_count': 0}], {}
    monkeypatch.setattr(TwmdClient, 'get_response', response)
    read = TwmdClient({}).shareholder_distribution('TWSE:2330', '2026-10-02', '2026-10-02')
    assert read.status == 'unknown'
    assert read.coverage_error_reasons == {'tdcc_shareholder_distribution_history': 'http_503'}
