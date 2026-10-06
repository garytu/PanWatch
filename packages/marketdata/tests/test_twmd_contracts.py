"""Offline checks for the reviewed twmd query contract and dated samples."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


FIXTURES = Path(__file__).parent / "fixtures" / "twmd"
CAPTURED = FIXTURES / "captured" / "2026-10-06.json"
SYNTHETIC = FIXTURES / "synthetic" / "offline_edge_cases.json"

PROFILE_FIELDS = {
    "instrument_id", "company_name", "industry_code", "established_on", "listed_on",
    "par_value_raw", "par_value_amount", "par_value_currency", "par_value_meaning",
    "paid_in_capital", "issued_share_count", "private_share_count", "preferred_share_count",
    "financial_report_type_code", "source_content_hash", "report_date", "original_received_at_utc",
    "source_contract", "payload_sha256", "revision", "share_semantics", "qualification",
    "qualification_reason", "latest_snapshot_report_date", "latest_snapshot_received_at_utc",
    "latest_snapshot_presence", "snapshot_capture_id",
}
PROFILE_REQUIRED_FIELDS = PROFILE_FIELDS - {"private_share_count", "preferred_share_count"}
SNAPSHOT_FIELDS = {
    "capture_id", "report_date", "received_at_utc", "source_contract", "payload_sha256",
    "row_count", "coverage_status",
}
REVENUE_ROW_FIELDS = {
    "symbol", "data_month", "company_name", "industry", "monthly_revenue",
    "previous_month_revenue", "year_ago_monthly_revenue", "month_over_month_pct",
    "year_over_year_pct", "cumulative_revenue", "year_ago_cumulative_revenue",
    "cumulative_yoy_pct", "notes", "content_hash", "revision", "capture_id", "source",
    "source_contract", "request_scope", "source_url", "acquisition_date", "received_at_utc",
    "report_date", "payload_sha256",
}
REVENUE_NUMERIC_FIELDS = {
    "monthly_revenue", "previous_month_revenue", "year_ago_monthly_revenue",
    "month_over_month_pct", "year_over_year_pct", "cumulative_revenue",
    "year_ago_cumulative_revenue", "cumulative_yoy_pct",
}
TWSE_FLOW_SHARE_FIELDS = {
    "foreign_non_dealer_buy_shares", "foreign_non_dealer_sell_shares",
    "foreign_non_dealer_net_shares", "foreign_dealer_buy_shares", "foreign_dealer_sell_shares",
    "foreign_dealer_net_shares", "investment_trust_buy_shares", "investment_trust_sell_shares",
    "investment_trust_net_shares", "dealer_reported_net_shares", "dealer_proprietary_buy_shares",
    "dealer_proprietary_sell_shares", "dealer_proprietary_net_shares", "dealer_hedging_buy_shares",
    "dealer_hedging_sell_shares", "dealer_hedging_net_shares", "total_institutional_net_shares",
}
TPEX_FLOW_SHARE_FIELDS = {
    f"{group}_{side}_shares"
    for group in (
        "foreign_ex_dealer", "foreign_dealer", "combined_foreign", "investment_trust",
        "dealer_own", "dealer_hedging", "combined_dealer",
    )
    for side in ("buy", "sell", "net")
} | {"total_institutional_net_shares"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def case_by_label(document: dict, label: str) -> dict:
    return next(item for item in document["cases"] if item["label"] == label)


def synthetic_case(document: dict, case_id: str) -> dict:
    return next(item for item in document["api_responses"] if item["id"] == case_id)


def assert_company_response(response: dict) -> None:
    assert {
        "instrument_id", "source_contract", "schema_ready", "coverage_status",
        "latest_snapshot_presence", "qualification", "qualification_reason", "units",
        "latest_snapshot", "profile",
    } <= response.keys()
    assert type(response["schema_ready"]) is bool
    assert response["coverage_status"] in {"AVAILABLE", "MISSING"}
    assert response["latest_snapshot_presence"] in {"present", "absent", "missing"}
    if response["latest_snapshot"] is not None:
        assert SNAPSHOT_FIELDS <= response["latest_snapshot"].keys()
        assert type(response["latest_snapshot"]["row_count"]) is int
    if response["profile"] is not None:
        profile = response["profile"]
        assert PROFILE_REQUIRED_FIELDS <= profile.keys()
        assert isinstance(profile["paid_in_capital"], str)
        assert type(profile["issued_share_count"]) is int
        assert profile["par_value_amount"] is None or isinstance(profile["par_value_amount"], str)
        for optional_count in ("private_share_count", "preferred_share_count"):
            assert optional_count not in profile or profile[optional_count] is None or type(profile[optional_count]) is int
        assert type(profile["revision"]) is int
        assert Decimal(profile["paid_in_capital"]).is_finite()
        date.fromisoformat(profile["report_date"])
        assert datetime.fromisoformat(profile["original_received_at_utc"]).utcoffset() is not None


def assert_revenue_response(response: dict) -> None:
    assert {
        "instrument_id", "dataset", "start_month", "end_month", "schema_ready",
        "coverage_status", "qualification", "qualification_reason", "current_catalog_evidence",
        "units", "coverage", "months", "served_at",
    } <= response.keys()
    assert response["coverage_status"] in {"AVAILABLE", "MISSING"}
    assert type(response["schema_ready"]) is bool
    assert datetime.fromisoformat(response["served_at"]).utcoffset() is not None
    assert isinstance(response["months"], list)
    for month in response["months"]:
        assert {"data_month", "presence", "row"} <= month.keys()
        assert month["presence"] in {"present", "not_in_captured_report", "missing"}
        if month["row"] is not None:
            row = month["row"]
            assert REVENUE_ROW_FIELDS <= row.keys()
            assert all(row[name] is None or isinstance(row[name], str) for name in REVENUE_NUMERIC_FIELDS)
            assert type(row["revision"]) is int
            assert row["data_month"] == month["data_month"]
            assert "served_at" not in row


def assert_flow_response(response: dict) -> None:
    assert {"instrument_id", "source_contract", "native_unit", "schema_ready", "coverage", "data"} <= response.keys()
    assert response["native_unit"] == "shares"
    assert isinstance(response["coverage"], list) and isinstance(response["data"], list)
    is_tpex = response["instrument_id"].startswith("TPEX:")
    if is_tpex:
        assert "request_scope" in response
        for coverage in response["coverage"]:
            assert {"trade_date", "status", "record_count", "selected_instrument_presence"} <= coverage.keys()
            assert type(coverage["record_count"]) is int
        fields = TPEX_FLOW_SHARE_FIELDS
    else:
        for coverage in response["coverage"]:
            assert {"trade_date", "status", "record_count", "acquired_at", "sha256"} <= coverage.keys()
            assert type(coverage["record_count"]) is int
        fields = TWSE_FLOW_SHARE_FIELDS
    for row in response["data"]:
        assert fields <= row.keys()
        assert all(type(row[key]) is int or (not is_tpex and row[key] is None) for key in fields)
        assert row["instrument_id"] == response["instrument_id"]


def test_live_fixture_is_dated_http_evidence_and_keeps_build_identity_separate():
    evidence = load(CAPTURED)
    assert evidence["fixture_kind"] == "captured_http"
    captured_day = datetime.fromisoformat(evidence["captured_at_taipei"]).date()
    assert captured_day == date(2026, 10, 6)
    assert evidence["upstream_checkout"]["commit"] == "3acd67ffd98bbcf1713f9484d8a7b77871db6ade"
    assert evidence["deployed_observation"]["served_commit"] is None or evidence["deployed_observation"]["served_commit"].startswith("not exposed")
    openapi = evidence["deployed_observation"]["openapi"]
    assert openapi["http_status"] == 200
    assert openapi["response"]["info"] == {
        "title": "Taiwan Market Data Query API",
        "description": "Local-first query service reading strictly from local storage",
        "version": "0.1.0",
    }
    for item in evidence["cases"]:
        assert item["method"] == "GET"
        assert urlparse(item["url"]).scheme == "http"
        assert item["http_status"] in {200, 400, 503}
        assert type(item["latency_ms"]) in (int, float) and item["latency_ms"] >= 0
        assert type(item["response_bytes"]) is int and item["response_bytes"] >= 0
        assert isinstance(item["response_headers"], dict)
    assert evidence["deployed_observation"]["readiness"]["http_status"] == 200
    assert evidence["deployed_observation"]["readiness"]["latency_ms"] > 9000


def test_live_catalog_keeps_venue_and_security_type_explicit():
    evidence = load(CAPTURED)
    expected = {
        "active TWSE equity": ("TWSE:2330", "TWSE", "EQUITY"),
        "active TPEX equity": ("TPEX:5347", "TPEX", "EQUITY"),
        "active TWSE ETF": ("TWSE:00878", "TWSE", "ETF"),
        "active TPEX ETF": ("TPEX:006201", "TPEX", "ETF"),
        "active TPEX warrant": ("TPEX:700019", "TPEX", "WARRANT"),
    }
    ids = set()
    for label, (identity, venue, security_type) in expected.items():
        item = case_by_label(evidence, label)
        row = item["response"]
        assert item["http_status"] == 200
        assert {"instrument_id", "symbol", "venue", "security_type", "is_active"} <= row.keys()
        assert (row["instrument_id"], row["venue"], row["security_type"], row["is_active"]) == (
            identity, venue, security_type, True
        )
        ids.add(row["instrument_id"])
    assert len(ids) == len(expected)
    unsupported = case_by_label(evidence, "TPEX:006201 revenue is unsupported for an ETF")["response"]
    assert unsupported["qualification"] == "unsupported_etf"
    assert unsupported["current_catalog_evidence"]["security_type"] == "ETF"
    assert unsupported["months"][0]["row"] is None


def test_live_captured_api_bodies_keep_contract_types_and_coverage():
    evidence = load(CAPTURED)
    by_label = {item["label"]: item for item in evidence["cases"]}
    for key, item in by_label.items():
        path = urlparse(item["url"]).path
        if path == "/api/v1/institutional-flows":
            if item["http_status"] == 200:
                assert_flow_response(item["response"])
            else:
                assert item["http_status"] == 400
        elif path == "/api/v1/monthly-revenues":
            if item["http_status"] == 200:
                assert_revenue_response(item["response"])
            else:
                assert item["http_status"] == 400
        elif path == "/api/v1/company-profiles":
            assert item["http_status"] == 200
            assert_company_response(item["response"])
        elif path == "/api/v1/valuations":
            if item["http_status"] == 200:
                assert isinstance(item["response"], list)
                for row in item["response"]:
                    for name in ("close_price", "pe_ratio", "pb_ratio", "dividend_yield_pct"):
                        if name in row:
                            assert row[name] is None or isinstance(row[name], str)
                    if row["instrument_id"].startswith("TPEX:"):
                        assert {"capture_id", "source_contract", "received_at_utc", "payload_sha256", "revision"} <= row.keys()
            else:
                assert item["http_status"] == 400
        else:
            assert item["http_status"] == 200

    twse = by_label["TWSE:2330 institutional flow 2026-10-02 available"]["response"]
    twse_row = twse["data"][0]
    assert type(twse_row["foreign_non_dealer_net_shares"]) is int
    assert twse_row["foreign_dealer_net_shares"] == 0
    assert twse_row["foreign_non_dealer_net_shares"] < 0
    assert twse["coverage"][0]["record_count"] > len(twse["data"])

    tpex = by_label["TPEX:5347 valuation 2026-10-02 available"]
    tpex_row = tpex["response"][0]
    assert tpex_row["close_price"] is None
    assert Decimal(tpex_row["pe_ratio"]) == Decimal("40.48")
    assert tpex["response_headers"]["x-twmd-schema-ready"] == "true"
    assert "available=1" in tpex["response_headers"]["x-twmd-coverage"]

    for venue in ("TWSE", "TPEX"):
        revenue = by_label[f"{venue}:" + ("2330" if venue == "TWSE" else "5347") + " monthly revenue 2026-07..08 partial coverage"]["response"]
        assert revenue["coverage_status"] == "AVAILABLE"
        assert [m["presence"] for m in revenue["months"]] == ["missing", "present"]
        assert revenue["months"][0]["row"] is None
        row = revenue["months"][1]["row"]
        assert isinstance(row["monthly_revenue"], str)
        assert Decimal(row["monthly_revenue"]) > 0
        assert isinstance(revenue["served_at"], str)
        assert "not provided" in revenue["units"]["publisher_report_time"]


def test_captured_company_profiles_include_success_and_bounded_timeout_history():
    evidence = load(CAPTURED)["company_profile_attempts"]
    fast = evidence["fast_bounded_attempts"]
    assert fast["timeout_per_attempt_sec"] == 10
    assert fast["retry_policy"] == "one explicit retry after the initial timeout"
    for result in fast["results"]:
        assert len(result["attempts"]) == 2
        assert [attempt["attempt"] for attempt in result["attempts"]] == [1, 2]
        assert all(attempt["status"] is None for attempt in result["attempts"])
        assert all(attempt["error_type"] == "TimeoutError" for attempt in result["attempts"])
        assert all(9900 <= attempt["latency_ms"] <= 10100 for attempt in result["attempts"])

    successful = evidence["successful_extended_reads"]
    assert successful["timeout_per_request_sec"] == 60
    by_id = {entry["instrument_id"]: entry for entry in successful["results"]}
    for identity in ("TWSE:2330", "TPEX:5347"):
        item = by_id[identity]
        assert item["http_status"] == 200
        assert item["latency_ms"] > 15000
        assert_company_response(item["response"])
        assert item["response"]["latest_snapshot_presence"] == "present"
        assert item["response"]["qualification"] == "qualified_issuer"
        profile = item["response"]["profile"]
        assert profile["instrument_id"] == identity
        date.fromisoformat(profile["report_date"])
        datetime.fromisoformat(profile["original_received_at_utc"].replace("Z", "+00:00"))
        assert isinstance(profile["paid_in_capital"], str)
        assert type(profile["issued_share_count"]) is int


def test_fixed_taipei_cutoffs_and_http_failures_are_not_empty_successes():
    evidence = load(CAPTURED)
    captured_day = datetime.fromisoformat(evidence["captured_at_taipei"]).date().isoformat()
    for label in (
        "TWSE flow end_date equals Taipei today: HTTP 400",
        "TPEX flow end_date equals Taipei today: HTTP 400",
        "TPEX valuation end equals Taipei today: HTTP 400",
    ):
        item = case_by_label(evidence, label)
        query = parse_qs(urlparse(item["url"]).query)
        end_key = "end_date" if "end_date" in query else "end"
        assert query[end_key] == [captured_day]
        assert item["http_status"] == 400
        assert "Asia/Taipei" in item["response"]["detail"]
    future_revenue = case_by_label(evidence, "future revenue month: HTTP 400")
    assert future_revenue["http_status"] == 400
    assert "future month" in future_revenue["response"]["detail"]

    flow_missing = case_by_label(evidence, "TWSE:2330 institutional flow 2026-10-05 missing")["response"]
    assert flow_missing["coverage"][0]["status"] == "MISSING"
    assert flow_missing["coverage"][0]["acquired_at"] is None
    assert flow_missing["data"] == []
    tpex_valuation_missing = case_by_label(evidence, "TPEX:5347 valuation 2026-10-05 missing")
    assert tpex_valuation_missing["http_status"] == 200
    assert tpex_valuation_missing["response"] == []
    assert tpex_valuation_missing["response_headers"]["x-twmd-coverage"] == "available=0;missing=1;selected=missing"


def test_synthetic_api_examples_and_research_states_are_marked_and_typed():
    synthetic = load(SYNTHETIC)
    assert synthetic["fixture_kind"] == "synthetic_offline_edge_cases"
    assert "not an HTTP capture" in synthetic["notice"]
    api = {item["id"]: item for item in synthetic["api_responses"]}
    for name in ("optional_profile_schema_absent", "profile_unsupported_etf", "profile_unsupported_warrant",
                 "profile_absent_from_new_snapshot_retains_older_profile"):
        assert_company_response(api[name]["response"])
    missing_schema = api["optional_profile_schema_absent"]["response"]
    assert api["optional_profile_schema_absent"]["http_status"] == 200
    assert missing_schema["schema_ready"] is False and missing_schema["coverage_status"] == "MISSING"
    for name, qualification in (("profile_unsupported_etf", "unsupported_etf"),
                                ("profile_unsupported_warrant", "unsupported_warrant")):
        assert api[name]["response"]["qualification"] == qualification
        assert api[name]["response"]["profile"] is None
    absent_profile = api["profile_absent_from_new_snapshot_retains_older_profile"]["response"]
    assert absent_profile["latest_snapshot_presence"] == "absent"
    assert absent_profile["profile"] is not None
    assert absent_profile["profile"]["report_date"] < absent_profile["latest_snapshot"]["report_date"]

    flow = api["available_flow_but_selected_issuer_absent"]["response"]
    assert_flow_response(flow)
    assert flow["coverage"][0]["status"] == "AVAILABLE"
    assert flow["coverage"][0]["selected_instrument_presence"] == "absent"
    assert flow["data"] == []
    revenue = api["monthly_partial_coverage_and_exact_values"]["response"]
    assert_revenue_response(revenue)
    assert revenue["coverage_status"] == "AVAILABLE"
    assert [month["presence"] for month in revenue["months"]] == ["missing", "present"]
    assert revenue["months"][1]["row"]["monthly_revenue"] == "514805337"
    assert revenue["months"][1]["row"]["month_over_month_pct"] == "10.099818994181083"
    assert revenue["served_at"] != revenue["months"][1]["row"]["received_at_utc"]
    assert revenue["months"][1]["row"]["revision"] == 2
    assert api["http_storage_error"]["http_status"] == 503
    assert api["http_storage_error"]["response"] != {"data": []}

    envelopes = {item["id"]: item for item in synthetic["research_envelopes"]}
    allowed = {"available", "partial", "absent", "empty", "missing", "stale", "unsupported", "error", "unknown"}
    for envelope in envelopes.values():
        assert envelope["status"] in allowed
        assert envelope["reason"]
        evidence = envelope["evidence"]
        assert {"instrument_id", "endpoint", "selectors", "source_contract", "period", "source_report_date",
                "publication_time", "source_received_at_utc", "served_at", "units", "dataset_coverage",
                "selected_instrument_presence", "capture_id", "revision", "payload_sha256"} <= evidence.keys()
    assert envelopes["retained_selected_valuation"]["status"] == "available"
    assert envelopes["no_retained_flow_partition"]["status"] == "missing"
    assert envelopes["recognized_twse_authoritative_empty"]["status"] == "empty"
    assert envelopes["available_tpex_report_selected_issuer_absent"]["status"] == "absent"
    assert envelopes["twse_valuation_empty_list_without_coverage"]["status"] == "unknown"
    assert envelopes["some_revenue_months_missing"]["status"] == "partial"
    assert envelopes["stale_observation"]["status"] == "stale"
    assert envelopes["active_warrant_unsupported_for_equity_valuation"]["status"] == "unsupported"
    assert envelopes["active_warrant_unsupported_for_equity_valuation"]["evidence"]["endpoint_called"] is False
    assert envelopes["provider_http_error"]["status"] == "error"


def test_fixtures_contain_no_credentials_or_private_settings():
    forbidden = {"authorization", "token", "password", "api_key", "secret", "account"}

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key.casefold() not in forbidden
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    for path in FIXTURES.rglob("*.json"):
        assert list(walk(load(path))) == []


def test_nonzero_flow_components_are_not_added_twice():
    fixture = load(SYNTHETIC)
    flow = synthetic_case(fixture, "flow_nonzero_foreign_dealer_components")["response"]
    assert_flow_response(flow)
    row = flow["data"][0]
    assert row["foreign_dealer_net_shares"] != 0
    total = (row["foreign_ex_dealer_net_shares"] + row["investment_trust_net_shares"]
             + row["combined_dealer_net_shares"])
    assert total == row["total_institutional_net_shares"]
    assert total != (row["combined_foreign_net_shares"] + row["investment_trust_net_shares"]
                     + row["combined_dealer_net_shares"])
    assert row["combined_dealer_net_shares"] == row["dealer_own_net_shares"] + row["dealer_hedging_net_shares"]
    # The same invariant also holds for the two real venue samples.
    for case in load(CAPTURED)["cases"]:
        if "institutional flow 2026-10-02 available" not in case["label"]:
            continue
        source_row = case["response"]["data"][0]
        foreign = "foreign_ex_dealer_net_shares" if case["response"]["instrument_id"].startswith("TPEX:") else "foreign_non_dealer_net_shares"
        dealer = "combined_dealer_net_shares" if foreign.startswith("foreign_ex") else "dealer_reported_net_shares"
        assert source_row["total_institutional_net_shares"] == (
            source_row[foreign] + source_row["investment_trust_net_shares"] + source_row[dealer]
        )


def test_exact_numeric_null_zero_and_signed_values_remain_distinct():
    fixture = load(SYNTHETIC)
    row = synthetic_case(fixture, "valuation_null_pe_zero_pb")["response"][0]
    assert row["pe_ratio"] is None and row["pb_ratio"] == "0.00"
    assert Decimal(row["pb_ratio"]) == 0 and Decimal(row["dividend_yield_pct"]) == 0
    revenue = synthetic_case(fixture, "revenue_null_zero_signed_scale")["response"]
    assert_revenue_response(revenue)
    row = revenue["months"][1]["row"]
    assert row["monthly_revenue"] == "0.000"
    assert row["previous_month_revenue"] is None and row["cumulative_yoy_pct"] is None
    assert row["year_over_year_pct"] == "-12.500"
    assert Decimal(row["year_over_year_pct"]) < 0
    for case in fixture["api_responses"]:
        response = case["response"]
        if isinstance(response, dict) and "months" in response:
            for month in response["months"]:
                if month["row"] is not None:
                    assert all(Decimal(month["row"][key]).is_finite()
                               for key in REVENUE_NUMERIC_FIELDS if month["row"][key] is not None)


def test_fixed_clock_contract_witnesses_use_taipei_at_utc_date_boundary():
    examples = load(SYNTHETIC)["fixed_clock_examples"]
    fixed_clock = datetime.fromisoformat(examples["now_utc"])
    today = fixed_clock.astimezone(ZoneInfo("Asia/Taipei")).date()
    assert today.isoformat() == examples["today_taipei"]
    assert today != fixed_clock.date()
    assert len(examples["daily_products"]) == 3
    for case in examples["daily_bounds"]:
        start, end = date.fromisoformat(case["start"]), date.fromisoformat(case["end"])
        within_contract = date(2024, 1, 1) <= start <= end < today and (end - start).days < 366
        assert within_contract is case["allowed"]
    for case in examples["monthly_bounds"]:
        start, end = case["start"], case["end"]
        month_clock = datetime.fromisoformat(case.get("now_utc", examples["now_utc"]))
        current_month = month_clock.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m")
        start_year, start_month = map(int, start.split("-"))
        end_year, end_month = map(int, end.split("-"))
        months = (end_year - start_year) * 12 + end_month - start_month + 1
        within_contract = "2024-01" <= start <= end <= current_month and months <= 120
        assert within_contract is case["allowed"]


def test_minute_capability_support_is_separate_from_configuration_and_delivery():
    capture = load(FIXTURES / "captured" / "bars-capabilities-2026-10-07.json")
    assert capture["fixture_kind"] == "captured_http_projection"
    assert capture["http_status"] == 200 and capture["method"] == "GET"
    assert datetime.fromisoformat(capture["captured_at_taipei"]).date() == date(2026, 10, 7)
    assert capture["response"] == {
        "live_collection_supported": True, "live_collection_configured": False, "live_collection": False,
    }
