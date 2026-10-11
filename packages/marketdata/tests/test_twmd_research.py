from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from marketdata.errors import TwmdReadError
from marketdata import MarketData, SourceConfig, VendorError
from marketdata.defaults import StaticConfigProvider
from marketdata.symbol import Symbol
from marketdata.types import InstitutionalFlowRead, TwmdValuationRead
from marketdata.vendors import twmd


FIXTURES = Path(__file__).parent / "fixtures" / "twmd"


def captured(label: str) -> dict:
    fixture = json.loads((FIXTURES / "captured" / "2026-10-06.json").read_text())
    return next(case for case in fixture["cases"] if case["label"] == label)


def synthetic(case_id: str) -> dict:
    fixture = json.loads((FIXTURES / "synthetic" / "offline_edge_cases.json").read_text())
    return next(case for case in fixture["api_responses"] if case["id"] == case_id)


def install_response(monkeypatch, response, headers=None):
    def get_response(self, path, **params):
        return response, headers or {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", get_response)


def test_twse_official_flow_keeps_native_categories_and_distinct_receipts(monkeypatch):
    case = captured("TWSE:2330 institutional flow 2026-10-02 available")
    response = case["response"]
    response["coverage"][0]["acquired_at"] = "2026-10-03T01:00:00Z"
    response["data"][0]["first_observed_at"] = "2026-10-03T02:00:00Z"
    install_response(monkeypatch, response)

    result = twmd.TwmdClient({}).institutional_flows(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )

    assert isinstance(result, InstitutionalFlowRead)
    assert result.instrument_id == "TWSE:2330" and result.status == "available"
    assert result.coverage[0].acquired_at != result.data[0].first_observed_at
    assert result.coverage[0].selected_instrument_presence == "present"
    assert result.data[0].native_values["foreign_dealer_net_shares"] == 0
    assert result.data[0].native_values["total_institutional_net_shares"] == -5_343_414


def test_twse_available_report_without_selected_row_is_absent_not_zero(monkeypatch):
    case = captured("TWSE:2330 institutional flow 2026-10-02 available")
    response = {**case["response"], "data": []}
    install_response(monkeypatch, response)

    result = twmd.TwmdClient({}).institutional_flows(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )

    assert result.status == "absent"
    assert result.coverage[0].selected_instrument_presence == "absent"
    assert result.data == []


def test_twse_source_empty_and_missing_coverage_remain_distinct(monkeypatch):
    available = captured("TWSE:2330 institutional flow 2026-10-02 available")["response"]
    empty_response = {
        **available,
        "coverage": [{
            **available["coverage"][0], "status": "EMPTY", "record_count": 0,
            "acquired_at": "2026-10-03T01:00:00Z", "sha256": "empty-hash",
        }],
        "data": [],
    }
    install_response(monkeypatch, empty_response)
    empty = twmd.TwmdClient({}).institutional_flows(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )

    missing_case = captured("TWSE:2330 institutional flow 2026-10-05 missing")
    install_response(monkeypatch, missing_case["response"])
    missing = twmd.TwmdClient({}).institutional_flows(
        "TWSE:2330", "2026-10-05", "2026-10-05", today_taipei=date(2026, 10, 6)
    )

    assert empty.status == "empty" and empty.coverage[0].status == "EMPTY"
    assert missing.status == "missing" and missing.coverage[0].status == "MISSING"


def test_tpex_flow_preserves_all_components_without_double_counting_foreign_dealer(monkeypatch):
    case = synthetic("flow_nonzero_foreign_dealer_components")
    install_response(monkeypatch, case["response"])

    result = twmd.TwmdClient({}).institutional_flows(
        "TPEX:5347", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )
    row = result.data[0]

    mapped_total = (
        row.native_values["foreign_ex_dealer_net_shares"]
        + row.native_values["investment_trust_net_shares"]
        + row.native_values["combined_dealer_net_shares"]
    )
    assert row.native_values["foreign_dealer_net_shares"] == 25
    assert mapped_total == row.native_values["total_institutional_net_shares"] == 125
    assert mapped_total != (
        row.native_values["combined_foreign_net_shares"]
        + row.native_values["investment_trust_net_shares"]
        + row.native_values["combined_dealer_net_shares"]
    )


def test_official_flow_compatibility_maps_native_share_totals_only(monkeypatch):
    case = synthetic("flow_nonzero_foreign_dealer_components")
    install_response(monkeypatch, case["response"])
    config = {
        "start_date": "2026-10-02", "end_date": "2026-10-02",
        "today_taipei": "2026-10-06",
    }

    flow = twmd.TwmdCapitalFlowVendor().fetch([Symbol.parse("TPEX:5347")], config)[0]

    assert flow.symbol == "TPEX:5347"
    assert (flow.foreign_net_shares, flow.trust_net_shares, flow.dealer_net_shares) == (100, 5, 20)
    assert flow.institutional_net_shares == 125
    assert flow.native_components["foreign_dealer_net_shares"] == 25
    assert flow.institutional_net_5d_shares is None
    assert flow.main_net_inflow is None and flow.super_net_inflow is None
    assert flow.evidence["coverage"][0]["trade_date"] == "2026-10-02"


def test_official_fundamentals_keeps_generic_pe_and_source_period_evidence(monkeypatch):
    case = captured("TWSE:2330 valuation 2026-10-02")
    install_response(monkeypatch, case["response"])
    config = {
        "start_date": "2026-10-02", "end_date": "2026-10-02",
        "today_taipei": "2026-10-06",
    }

    fundamentals = twmd.TwmdFundamentalsVendor().fetch(
        [Symbol.parse("TWSE:2330")], config
    )[0]

    assert fundamentals.symbol == "TWSE:2330"
    assert fundamentals.pe_ratio == 28.98
    assert fundamentals.pe_ttm is None and fundamentals.pe_static is None
    assert fundamentals.pb == 10.08 and fundamentals.dividend_yield == 0.88
    assert fundamentals.valuation_trade_date == "2026-10-02"
    assert fundamentals.report_date == ""
    row = fundamentals.valuation_evidence["rows"][0]
    assert row["pe_ratio"] == "28.98"
    assert row["financial_reference_year"] == 2026
    assert fundamentals.valuation_evidence["units"]["pe_ratio"].endswith("unspecified")


def test_tpex_valuation_preserves_response_headers_and_exact_original_values(monkeypatch):
    case = captured("TPEX:5347 valuation 2026-10-02 available")
    headers = {key.lower(): value for key, value in case["response_headers"].items()}
    install_response(monkeypatch, case["response"], headers)

    result = twmd.TwmdClient({}).valuation_history(
        "TPEX:5347", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )

    assert isinstance(result, TwmdValuationRead)
    assert result.response_headers == headers
    assert result.coverage_header == "available=1;missing=0;selected=present"
    assert result.data[0].pe_ratio == "40.48"
    assert result.data[0].dividend_per_share == "4.47377334"
    assert result.data[0].close_price is None


def test_valuation_compatibility_retains_history_and_selects_latest_row(monkeypatch):
    case = captured("TWSE:2330 valuation 2026-10-02")
    first, latest = case["response"][0], dict(case["response"][0])
    first["trade_date"] = "2026-10-01"
    case["response"].append(latest)
    install_response(monkeypatch, case["response"])
    config = {
        "start_date": "2026-10-01", "end_date": "2026-10-02",
        "today_taipei": "2026-10-06",
    }

    result = twmd.TwmdFundamentalsVendor().fetch([Symbol.parse("TWSE:2330")], config)[0]

    assert result.valuation_trade_date == "2026-10-02"
    assert [row["trade_date"] for row in result.valuation_evidence["rows"]] == [
        "2026-10-01", "2026-10-02"
    ]


def test_twse_empty_valuation_stays_unknown_and_null_pe_is_not_zero(monkeypatch):
    case = synthetic("valuation_null_pe_zero_pb")
    install_response(monkeypatch, case["response"], {
        "x-twmd-schema-ready": "true",
        "x-twmd-coverage": "available=1;missing=0;selected=present",
    })

    result = twmd.TwmdClient({}).valuation_history(
        "TPEX:5347", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )
    empty = twmd.TwmdClient({})
    empty.get_response = lambda path, **params: ([], {})
    empty_result = empty.valuation_history(
        "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
    )

    assert result.data[0].pe_ratio is None and result.data[0].pb_ratio == "0.00"
    assert result.status == "available"
    assert empty_result.status == "unknown" and empty_result.selected_instrument_presence == "unknown"


def test_tpex_missing_valuation_schema_and_presence_are_retained(monkeypatch):
    case = captured("TPEX:5347 valuation 2026-10-05 missing")
    headers = {key.lower(): value for key, value in case["response_headers"].items()}
    headers["x-twmd-schema-ready"] = "false"
    install_response(monkeypatch, case["response"], headers)

    result = twmd.TwmdClient({}).valuation_history(
        "TPEX:5347", "2026-10-05", "2026-10-05", today_taipei=date(2026, 10, 6)
    )

    assert result.schema_ready is False
    assert result.status == "missing"
    assert result.selected_instrument_presence == "missing"
    assert result.response_headers == headers


def test_official_flow_compatibility_retains_multiple_observation_rows(monkeypatch):
    case = captured("TWSE:2330 institutional flow 2026-10-02 available")
    response = case["response"]
    previous_coverage = dict(response["coverage"][0], trade_date="2026-10-01")
    previous_row = dict(response["data"][0], trade_date="2026-10-01")
    response["coverage"].append(previous_coverage)
    response["data"].append(previous_row)
    install_response(monkeypatch, response)
    config = {
        "start_date": "2026-10-01", "end_date": "2026-10-02",
        "today_taipei": "2026-10-06",
    }

    flow = twmd.TwmdCapitalFlowVendor().fetch([Symbol.parse("TWSE:2330")], config)[0]

    assert flow.trade_date == "2026-10-02"
    assert [row["trade_date"] for row in flow.evidence["rows"]] == [
        "2026-10-02", "2026-10-01"
    ]


@pytest.mark.parametrize(
    "method,args",
    [
        ("institutional_flows", ("TWSE:2330", "2026-10-06", "2026-10-06")),
        ("institutional_flows", ("TWSE:2330", "2023-12-31", "2024-01-01")),
        ("valuation_history", ("TPEX:5347", "2023-12-31", "2024-01-01")),
        ("valuation_history", ("TPEX:5347", "2024-01-01", "2025-01-01")),
    ],
)
def test_completed_date_and_tpex_product_bounds_reject_without_read(monkeypatch, method, args):
    client = twmd.TwmdClient({})
    calls = []
    client.get_response = lambda *a, **kw: calls.append((a, kw))

    with pytest.raises(ValueError):
        getattr(client, method)(*args, today_taipei=date(2026, 10, 6))
    assert calls == []


def test_http_errors_remain_errors_and_keep_status(monkeypatch):
    from marketdata.http import MarketHttpResponse

    monkeypatch.setattr(
        twmd,
        "market_get",
        lambda *args, **kwargs: MarketHttpResponse(400, {}, {"detail": "invalid range"}),
    )
    with pytest.raises(TwmdReadError) as error:
        twmd.TwmdClient({}).get_response("valuations", instrument_id="TPEX:5347")
    assert error.value.status_code == 400
    assert "HTTP 400" in str(error.value)


@pytest.mark.parametrize("datatype,invoke", [
    ("fundamentals", lambda md: md.fundamentals(["TWSE:2330"], market="TW")),
    ("capital_flow", lambda md: md.capital_flow("TWSE:2330", market="TW")),
])
def test_official_http_errors_do_not_collapse_into_empty_success(monkeypatch, datatype, invoke):
    config = StaticConfigProvider({datatype: [SourceConfig(vendor="twmd")]})

    def fail(self, path, **params):
        raise TwmdReadError("twmd HTTP 503", status_code=503)

    monkeypatch.setattr(twmd.TwmdClient, "get_response", fail)
    with pytest.raises(VendorError, match="HTTP 503"):
        invoke(MarketData(config))


@pytest.mark.parametrize("known_status", ["EMPTY", "AVAILABLE"])
def test_mixed_known_and_missing_flow_dates_are_partial(monkeypatch, known_status):
    response = captured("TWSE:2330 institutional flow 2026-10-02 available")["response"]
    response["data"] = []
    first = response["coverage"][0]
    first.update(status=known_status, record_count=0 if known_status == "EMPTY" else 18610)
    response["coverage"].append({
        "trade_date": "2026-10-03", "status": "MISSING", "record_count": 0,
        "acquired_at": None, "sha256": None,
    })
    install_response(monkeypatch, response)
    result = twmd.TwmdClient({}).institutional_flows(
        "TWSE:2330", "2026-10-02", "2026-10-03", today_taipei=date(2026, 10, 6)
    )
    assert result.status == "partial" and result.data == []
    assert [row.status for row in result.coverage] == [known_status, "MISSING"]
    assert result.coverage[1].selected_instrument_presence == "missing"


@pytest.mark.parametrize("bad_value", ["", "not-a-number", "NaN", "Infinity"])
def test_invalid_valuation_numbers_are_errors_not_null(monkeypatch, bad_value):
    response = captured("TWSE:2330 valuation 2026-10-02")["response"]
    response[0]["pe_ratio"] = bad_value
    install_response(monkeypatch, response)
    with pytest.raises(TwmdReadError, match="pe_ratio"):
        twmd.TwmdClient({}).valuation_history(
            "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
        )


def test_missing_valuation_field_is_not_source_null(monkeypatch):
    response = captured("TWSE:2330 valuation 2026-10-02")["response"]
    del response[0]["pe_ratio"]
    install_response(monkeypatch, response)
    with pytest.raises(TwmdReadError, match="missing pe_ratio"):
        twmd.TwmdClient({}).valuation_history(
            "TWSE:2330", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
        )


@pytest.mark.parametrize("status", [400, 503])
def test_plaintext_http_errors_keep_their_status(monkeypatch, status):
    import httpx
    import marketdata.http as mh

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, params=None):
            return httpx.Response(status, text="gateway error", request=httpx.Request("GET", url))

    monkeypatch.setattr(mh.httpx, "Client", Client)
    monkeypatch.setattr(mh.time, "sleep", lambda *_: None)
    with pytest.raises(TwmdReadError) as error:
        twmd.TwmdClient({}).get_response("valuations")
    assert error.value.status_code == status


def test_tpex_valuation_absence_and_missing_dates_are_not_full_absence(monkeypatch):
    install_response(monkeypatch, [], {
        "x-twmd-schema-ready": "true",
        "x-twmd-coverage": "available=1;missing=1;selected=absent",
    })
    result = twmd.TwmdClient({}).valuation_history(
        "TPEX:5347", "2026-10-02", "2026-10-03", today_taipei=date(2026, 10, 6)
    )
    assert result.status == "partial" and result.selected_instrument_presence == "absent"


@pytest.mark.parametrize("headers", [
    {"x-twmd-schema-ready": "false", "x-twmd-coverage": "available=1;missing=0;selected=present"},
    {"x-twmd-schema-ready": "true", "x-twmd-coverage": "available=1;missing=0;selected=absent"},
    {"x-twmd-schema-ready": "true", "x-twmd-coverage": "available=1;missing=1;selected=present"},
])
def test_tpex_valuation_rejects_contradictory_coverage(monkeypatch, headers):
    install_response(monkeypatch, captured("TPEX:5347 valuation 2026-10-02 available")["response"], headers)
    with pytest.raises(TwmdReadError):
        twmd.TwmdClient({}).valuation_history(
            "TPEX:5347", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
        )


def test_tpex_flow_presence_without_row_is_a_provider_error(monkeypatch):
    response = captured("TPEX:5347 institutional flow 2026-10-02 available")["response"]
    response["data"] = []
    install_response(monkeypatch, response)
    with pytest.raises(TwmdReadError, match="no matching observation"):
        twmd.TwmdClient({}).institutional_flows(
            "TPEX:5347", "2026-10-02", "2026-10-02", today_taipei=date(2026, 10, 6)
        )
