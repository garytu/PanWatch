from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from marketdata.errors import TwmdReadError
from marketdata.http import MarketHttpResponse
from marketdata import MarketData, SourceConfig
from marketdata.defaults import StaticConfigProvider
from marketdata.types import TwmdCompanyProfileRead, TwmdMonthlyRevenueRead
from marketdata.vendors import twmd


FIXTURES = Path(__file__).parent / "fixtures" / "twmd"


def captured(label: str) -> dict:
    fixture = json.loads((FIXTURES / "captured" / "2026-10-06.json").read_text())
    return copy.deepcopy(next(case for case in fixture["cases"] if case["label"] == label))


def synthetic(case_id: str) -> dict:
    fixture = json.loads((FIXTURES / "synthetic" / "offline_edge_cases.json").read_text())
    return copy.deepcopy(next(case for case in fixture["api_responses"] if case["id"] == case_id))


def profile_capture(instrument_id: str) -> dict:
    fixture = json.loads((FIXTURES / "captured" / "2026-10-06.json").read_text())
    return copy.deepcopy(next(
        result["response"]
        for result in fixture["company_profile_attempts"]["successful_extended_reads"]["results"]
        if result["instrument_id"] == instrument_id
    ))


@pytest.fixture(autouse=True)
def clear_read_caches():
    twmd._company_profile_cache.clear()
    twmd._monthly_revenue_cache.clear()
    yield
    twmd._company_profile_cache.clear()
    twmd._monthly_revenue_cache.clear()


def install_response(monkeypatch, payload, headers=None):
    def get_response(self, path, **params):
        return copy.deepcopy(payload), headers or {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", get_response)


@pytest.mark.parametrize("instrument_id", ["TWSE:2330", "TPEX:5347"])
def test_company_profile_maps_venue_specific_exact_values_and_qualification(monkeypatch, instrument_id):
    response = profile_capture(instrument_id)
    install_response(monkeypatch, response)

    result = twmd.TwmdClient({}).company_profile(instrument_id)

    assert isinstance(result, TwmdCompanyProfileRead)
    assert result.status == "available" and result.reason == "selected_profile_present"
    assert result.latest_snapshot_presence == "present"
    assert result.profile is not None
    assert result.profile.instrument_id == instrument_id
    assert result.profile.industry_code == "24"
    expected_capital = "259323700670" if instrument_id.startswith("TWSE:") else "18785021920"
    assert result.profile.paid_in_capital == expected_capital
    assert result.profile.issued_share_count > 0
    assert result.profile.par_value_raw == "新台幣                 10.0000元"
    assert result.profile.par_value_amount == "10.0000"
    assert result.profile.par_value_currency == "TWD"
    assert result.profile.source_contract == result.source_contract
    assert result.profile.original_received_at_utc.endswith("Z")
    if instrument_id.startswith("TPEX:"):
        assert result.profile.private_share_count == 0
        assert result.profile.preferred_share_count == 0


def test_profile_absence_keeps_retained_issuer_evidence_and_snapshot_separate(monkeypatch):
    response = synthetic("profile_absent_from_new_snapshot_retains_older_profile")["response"]
    install_response(monkeypatch, response)

    result = twmd.TwmdClient({}).company_profile("TWSE:2330")

    assert result.status == "partial"
    assert result.reason == "issuer_absent_from_latest_snapshot_retained_profile"
    assert result.latest_snapshot_presence == "absent"
    assert result.profile is not None
    assert result.profile.report_date == "2026-09-30"
    assert result.profile.latest_snapshot_report_date == "2026-10-03"
    assert result.profile.latest_snapshot_presence == "absent"
    assert result.latest_snapshot is not None
    assert result.profile.original_received_at_utc != result.latest_snapshot.received_at_utc


def test_profile_missing_schema_and_unsupported_etf_are_not_fabricated(monkeypatch):
    install_response(monkeypatch, synthetic("optional_profile_schema_absent")["response"])
    missing = twmd.TwmdClient({}).company_profile("TWSE:2330")
    assert missing.status == "missing" and missing.profile is None
    assert missing.coverage_status == "MISSING" and missing.schema_ready is False

    install_response(monkeypatch, synthetic("profile_unsupported_etf")["response"])
    unsupported = twmd.TwmdClient({}).company_profile("TWSE:00878")
    assert unsupported.status == "unsupported"
    assert unsupported.qualification == "unsupported_etf"
    assert unsupported.profile is None


def test_profile_request_uses_its_explicit_bounded_timeout_once(monkeypatch):
    response = profile_capture("TWSE:2330")
    calls = []

    def market_get(url, **kwargs):
        calls.append((url, kwargs))
        return MarketHttpResponse(200, {}, response)

    monkeypatch.setattr(twmd, "market_get", market_get)
    result = twmd.TwmdClient({"profile_timeout_sec": 24.5, "timeout_sec": 5}).company_profile("TWSE:2330")

    assert result.status == "available"
    assert len(calls) == 1
    assert calls[0][0].endswith("/api/v1/company-profiles")
    assert calls[0][1]["params"] == {"instrument_id": "TWSE:2330"}
    assert calls[0][1]["timeout"] == 24.5
    assert calls[0][1]["retries"] == 0
    twmd._company_profile_cache.clear()
    twmd.TwmdClient({"base_url": "http://default", "timeout_sec": 5}).company_profile("TWSE:2330")
    assert calls[1][1]["timeout"] == 20
    assert calls[1][1]["retries"] == 0
    with pytest.raises(TypeError):
        twmd.TwmdClient({}).company_profile("TWSE:2330", as_of="2026-10-01")


def test_profile_timeout_is_a_transport_error_with_timeout_reason(monkeypatch):
    def failed_get(*args, **kwargs):
        raise twmd.MarketHttpError("timeout") from httpx.ReadTimeout("slow profile")

    monkeypatch.setattr(twmd, "market_get", failed_get)
    with pytest.raises(TwmdReadError) as error:
        twmd.TwmdClient({"profile_timeout_sec": 20}).company_profile("TWSE:2330")
    assert error.value.reason_code == "timeout"
    assert error.value.status_code is None


def test_profile_duplicate_and_unsupported_selector_http_400_are_not_empty_reads(monkeypatch):
    requests = []

    def market_get(url, **kwargs):
        requests.append(kwargs["params"])
        return MarketHttpResponse(400, {}, {"detail": "unsupported or duplicate selector"})

    monkeypatch.setattr(twmd, "market_get", market_get)
    client = twmd.TwmdClient({})
    for params in (
        {"instrument_id": ["TWSE:2330", "TWSE:2330"]},
        {"instrument_id": "TWSE:2330", "as_of": "2026-10-01"},
    ):
        with pytest.raises(TwmdReadError) as error:
            client.get_response("company-profiles", **params)
        assert error.value.status_code == 400
        assert error.value.reason_code == "http_400"
    assert requests == [
        {"instrument_id": ["TWSE:2330", "TWSE:2330"]},
        {"instrument_id": "TWSE:2330", "as_of": "2026-10-01"},
    ]


def test_marketdata_uses_the_explicit_profile_and_revenue_sources(monkeypatch):
    response = profile_capture("TWSE:2330")
    monkeypatch.setattr(
        twmd.TwmdClient, "get_response", lambda self, path, **params: (response, {})
    )
    md = MarketData(StaticConfigProvider({
        "company_profile": [SourceConfig(vendor="twmd", config={"base_url": "http://fixture"})]
    }))

    assert md.company_profile("TWSE:2330").profile is not None
    wrong_source = MarketData(StaticConfigProvider({
        "company_profile": [SourceConfig(vendor="finmind", config={})]
    }))
    with pytest.raises(ValueError, match="explicitly configured twmd"):
        wrong_source.company_profile("TWSE:2330")


@pytest.mark.parametrize(
    ("instrument_id", "label"),
    [
        ("TWSE:2330", "TWSE:2330 monthly revenue 2026-07..08 partial coverage"),
        ("TPEX:5347", "TPEX:5347 monthly revenue 2026-07..08 partial coverage"),
    ],
)
def test_monthly_revenue_preserves_adjacent_month_gaps_and_source_rows(monkeypatch, instrument_id, label):
    response = captured(label)["response"]
    install_response(monkeypatch, response)

    result = twmd.TwmdClient({}).monthly_revenues(
        instrument_id, "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    )

    assert isinstance(result, TwmdMonthlyRevenueRead)
    assert result.status == "partial"
    assert result.coverage_status == "AVAILABLE"
    assert [(month.data_month, month.presence) for month in result.months] == [
        ("2026-07-01", "missing"), ("2026-08-01", "present")
    ]
    assert result.months[0].row is None
    row = result.months[1].row
    assert row is not None and row.data_month == "2026-08-01"
    assert row.monthly_revenue == ("514805337" if instrument_id.startswith("TWSE:") else "5092599")
    assert row.year_over_year_pct is not None
    assert row.cumulative_yoy_pct is not None
    assert row.received_at_utc == result.coverage[0].received_at_utc
    assert "inferred" in result.units["revenue"]
    assert "not provided" in result.units["publisher_report_time"]


def test_monthly_revenue_preserves_zero_signed_null_ratios_and_retained_row_on_absence(monkeypatch):
    response = synthetic("revenue_null_zero_signed_scale")["response"]
    original_row = copy.deepcopy(response["months"][1]["row"])
    response["coverage"][0]["selected_issuer_present"] = False
    response["months"][1]["presence"] = "not_in_captured_report"
    # This is a shaped edge response: an older retained issuer row can outlive a
    # newer report that omits that issuer for the same period.
    install_response(monkeypatch, response)

    result = twmd.TwmdClient({}).monthly_revenues(
        "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    )

    month = result.months[1]
    assert result.status == "partial"
    assert month.presence == "not_in_captured_report"
    assert month.row is not None
    assert month.row.monthly_revenue == "0.000"
    assert month.row.previous_month_revenue is None
    assert month.row.year_over_year_pct == "-12.500"
    assert month.row.cumulative_yoy_pct is None
    assert month.row.notes == original_row["notes"]

    one_month = copy.deepcopy(response)
    one_month["start_month"] = one_month["end_month"] = "2026-08-01"
    one_month["months"] = [one_month["months"][1]]
    install_response(monkeypatch, one_month)
    isolated = twmd.TwmdClient({}).monthly_revenues(
        "TWSE:2330", "2026-08", "2026-08", today_taipei=date(2026, 10, 7)
    )
    assert isolated.status == "partial"
    assert isolated.months[0].presence == "not_in_captured_report"
    assert isolated.months[0].row is not None


def test_malformed_monthly_row_is_an_error_and_does_not_enter_cache(monkeypatch):
    response = captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"]
    del response["months"][1]["row"]["month_over_month_pct"]
    install_response(monkeypatch, response)
    client = twmd.TwmdClient({})
    with pytest.raises(TwmdReadError) as error:
        client.monthly_revenues("TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7))
    assert error.value.reason_code == "invalid_response"

    valid = captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"]
    install_response(monkeypatch, valid)
    assert client.monthly_revenues(
        "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    ).months[1].row is not None


def test_monthly_revenue_etf_and_http_errors_are_not_empty_successes(monkeypatch):
    response = captured("TPEX:5347 monthly revenue 2026-07..08 partial coverage")["response"]
    response["instrument_id"] = "TPEX:006201"
    response["qualification"] = "unsupported_etf"
    response["qualification_reason"] = "current_catalog_security_type_etf"
    response["current_catalog_evidence"] = {
        "status": "available", "name": "ETF", "security_type": "ETF",
        "is_active": True, "listing_date": None, "industry_category": None,
    }
    for item in response["months"]:
        item["row"] = None
    response["coverage"] = [dict(item, selected_issuer_present=False) for item in response["coverage"]]
    response["coverage"] = [item for item in response["coverage"] if item["data_month"] == "2026-08-01"]
    response["months"][0]["presence"] = "missing"
    response["months"][1]["presence"] = "not_in_captured_report"
    install_response(monkeypatch, response)

    unsupported = twmd.TwmdClient({}).monthly_revenues(
        "TPEX:006201", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    )
    assert unsupported.status == "unsupported" and unsupported.months[1].row is None

    client = twmd.TwmdClient({})
    client.get_response = lambda path, **params: (_ for _ in ()).throw(
        TwmdReadError("HTTP 503", status_code=503, reason_code="http_503")
    )
    with pytest.raises(TwmdReadError) as error:
        client.monthly_revenues("TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7))
    assert error.value.status_code == 503
    assert error.value.reason_code == "http_503"


def test_monthly_bounds_adjacent_year_floor_future_current_month_and_range_limit(monkeypatch):
    requests = []

    def missing_response(self, path, **params):
        requests.append(params)
        months = []
        start = date.fromisoformat(params["start_month"] + "-01")
        end = date.fromisoformat(params["end_month"] + "-01")
        while start <= end:
            months.append({"data_month": start.isoformat(), "presence": "missing", "row": None})
            start = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
        return {
            "instrument_id": params["instrument_id"],
            "dataset": "monthly_revenue" if params["instrument_id"].startswith("TWSE:") else "tpex_monthly_revenue_latest",
            "start_month": date.fromisoformat(params["start_month"] + "-01").isoformat(),
            "end_month": date.fromisoformat(params["end_month"] + "-01").isoformat(),
            "schema_ready": True, "coverage_status": "MISSING", "qualification": "qualified_issuer",
            "qualification_reason": "test", "current_catalog_evidence": {"status": "missing"},
            "units": {"revenue": "TWD thousands (inferred)"}, "coverage": [], "months": months,
            "served_at": "2026-01-31T12:00:00Z",
        }, {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", missing_response)
    client = twmd.TwmdClient({})
    result = client.adjacent_monthly_revenues(
        "TWSE:2330", "2026-01", today_taipei=date(2026, 1, 31)
    )
    assert result.status == "missing"
    assert (result.start_month, result.end_month) == ("2025-12-01", "2026-01-01")
    assert [(item["start_month"], item["end_month"]) for item in requests] == [("2025-12", "2026-01")]

    for start, end, today in (
        ("2023-12", "2024-01", date(2024, 1, 31)),
        ("2026-01", "2026-02", date(2026, 1, 31)),
        ("2024-01", "2034-01", date(2034, 1, 31)),
    ):
        with pytest.raises(ValueError):
            client.monthly_revenues(
                "TWSE:2330", start, end, today_taipei=today
            )
    assert len(requests) == 1
    maximum = client.monthly_revenues(
        "TWSE:2330", "2024-01", "2033-12", today_taipei=date(2033, 12, 31)
    )
    assert len(maximum.months) == 120
    assert (maximum.months[0].data_month, maximum.months[-1].data_month) == (
        "2024-01-01", "2033-12-01"
    )


def test_profile_and_revenue_caches_are_scoped_and_return_defensive_copies(monkeypatch):
    response = profile_capture("TWSE:2330")
    calls = []

    def profile_response(self, path, **params):
        calls.append((self.base_url, path, params))
        return copy.deepcopy(response), {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", profile_response)
    first = twmd.TwmdClient({"base_url": "http://one"}).company_profile("TWSE:2330")
    first.units["paid_in_capital"] = "mutated"
    second = twmd.TwmdClient({"base_url": "http://one"}).company_profile("TWSE:2330")
    assert second.units["paid_in_capital"] == "TWD"
    assert len(calls) == 1
    tpex_response = profile_capture("TPEX:5347")

    def profile_by_id(self, path, **params):
        calls.append((self.base_url, path, params))
        return copy.deepcopy(tpex_response if params["instrument_id"] == "TPEX:5347" else response), {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", profile_by_id)
    twmd.TwmdClient({"base_url": "http://one"}).company_profile("TPEX:5347")
    assert len(calls) == 2
    twmd.TwmdClient({"base_url": "http://two"}).company_profile("TWSE:2330")
    assert len(calls) == 3

    revenue = captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"]

    def revenue_response(self, path, **params):
        calls.append((self.base_url, path, params))
        result = copy.deepcopy(revenue)
        result["start_month"] = date.fromisoformat(params["start_month"] + "-01").isoformat()
        result["end_month"] = date.fromisoformat(params["end_month"] + "-01").isoformat()
        if params["start_month"] == params["end_month"]:
            month = result["months"][1]
            result["months"] = [month]
            result["coverage"] = [entry for entry in result["coverage"] if entry["data_month"] == month["data_month"]]
        return result, {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", revenue_response)
    client = twmd.TwmdClient({"base_url": "http://one"})
    first_range = client.monthly_revenues("TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7))
    first_range.months.clear()
    second_range = client.monthly_revenues("TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7))
    assert len(second_range.months) == 2
    client.monthly_revenues("TWSE:2330", "2026-08", "2026-08", today_taipei=date(2026, 10, 7))
    twmd.TwmdClient({"base_url": "http://two"}).monthly_revenues(
        "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    )
    revenue_calls = [call for call in calls if call[1] == "monthly-revenues"]
    assert len(revenue_calls) == 3


@pytest.mark.parametrize("target", ["profile", "snapshot", "envelope"])
def test_profile_rejects_other_venue_source_evidence(monkeypatch, target):
    response = profile_capture("TWSE:2330")
    contract = "tpex.openapi.mopsfin_t187ap03_O/v1.0.0"
    value = response if target == "envelope" else response[
        "latest_snapshot" if target == "snapshot" else "profile"
    ]
    value["source_contract"] = contract
    install_response(monkeypatch, response)
    with pytest.raises(TwmdReadError, match="source contract"):
        twmd.TwmdClient({}).company_profile("TWSE:2330")


@pytest.mark.parametrize("target", ["row", "coverage"])
def test_revenue_rejects_other_venue_source_evidence(monkeypatch, target):
    response = captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"]
    value = response["months"][1]["row"] if target == "row" else response["coverage"][0]
    value["source"] = "tpex"
    value["source_contract"] = "tpex.openapi.mopsfin_t187ap05_O/v1.0.0"
    install_response(monkeypatch, response)
    with pytest.raises(TwmdReadError, match="source contract"):
        twmd.TwmdClient({}).monthly_revenues(
            "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
        )


def test_revenue_missing_partition_cannot_carry_a_fabricated_row(monkeypatch):
    response = captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"]
    response["months"][0]["row"] = copy.deepcopy(response["months"][1]["row"])
    response["months"][0]["row"]["data_month"] = "2026-07-01"
    install_response(monkeypatch, response)
    with pytest.raises(TwmdReadError, match="missing"):
        twmd.TwmdClient({}).monthly_revenues(
            "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
        )


@pytest.mark.parametrize("dataset", ["profile", "revenue"])
def test_research_cache_does_not_bypass_different_credentials(monkeypatch, dataset):
    response = (profile_capture("TWSE:2330") if dataset == "profile" else
                captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"])
    calls = []

    def credential_response(self, path, **params):
        calls.append(self.config.get("token"))
        if self.config.get("token") != "fixture-authorized":
            raise TwmdReadError("unauthorized", status_code=401)
        return copy.deepcopy(response), {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", credential_response)

    def read(client):
        if dataset == "profile":
            return client.company_profile("TWSE:2330")
        return client.monthly_revenues(
            "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
        )

    read(twmd.TwmdClient({"token": "fixture-authorized"}))
    with pytest.raises(TwmdReadError) as error:
        read(twmd.TwmdClient({"token": "fixture-rejected"}))
    assert error.value.status_code == 401
    assert calls == ["fixture-authorized", "fixture-rejected"]


def test_revenue_keeps_signed_amount_and_publisher_percentages_independent(monkeypatch):
    response = captured("TPEX:5347 monthly revenue 2026-07..08 partial coverage")["response"]
    row = response["months"][1]["row"]
    row.update(monthly_revenue="-12.000", month_over_month_pct="0.000",
               year_over_year_pct="-1.250", cumulative_yoy_pct="8.500", notes="")
    install_response(monkeypatch, response)
    result = twmd.TwmdClient({}).monthly_revenues(
        "TPEX:5347", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    )
    mapped = result.months[1].row
    assert mapped is not None
    assert (mapped.monthly_revenue, mapped.month_over_month_pct,
            mapped.year_over_year_pct, mapped.cumulative_yoy_pct, mapped.notes) == (
        "-12.000", "0.000", "-1.250", "8.500", ""
    )


@pytest.mark.parametrize("kwargs", [
    {"as_of": "2026-08-01"},
    {"start_month": ["2026-07", "2026-08"]},
    {"end_month": ["2026-08", "2026-08"]},
])
def test_revenue_rejects_extra_and_duplicate_selectors_before_http(monkeypatch, kwargs):
    def unexpected_request(*args, **params):
        pytest.fail("invalid selectors must not make a request")

    monkeypatch.setattr(twmd.TwmdClient, "get_response", unexpected_request)
    selectors = {"start_month": "2026-07", "end_month": "2026-08", **kwargs}
    with pytest.raises((TypeError, ValueError)):
        twmd.TwmdClient({}).monthly_revenues("TWSE:2330", **selectors)


@pytest.mark.parametrize("dataset", ["profile", "revenue"])
def test_same_numeric_issuer_in_two_venues_has_independent_cache(monkeypatch, dataset):
    calls = []

    def response_by_venue(self, path, **params):
        identity = params["instrument_id"]
        calls.append(identity)
        venue = identity.split(":")[0]
        fixture_id = "TWSE:2330" if venue == "TWSE" else "TPEX:5347"
        response = (profile_capture(fixture_id) if dataset == "profile" else
                    captured(f"{fixture_id} monthly revenue 2026-07..08 partial coverage")["response"])
        # Explicitly shaped same-code scenario; source contracts stay venue-local.
        response["instrument_id"] = identity
        if dataset == "profile":
            response["profile"]["instrument_id"] = identity
        else:
            response["months"][1]["row"]["symbol"] = "2330"
        return response, {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", response_by_venue)
    client = twmd.TwmdClient({})
    results = []
    for identity in ("TWSE:2330", "TPEX:2330", "TWSE:2330"):
        results.append(client.company_profile(identity) if dataset == "profile" else
                       client.monthly_revenues(identity, "2026-07", "2026-08",
                                               today_taipei=date(2026, 10, 7)))
    assert calls == ["TWSE:2330", "TPEX:2330"]
    assert [result.instrument_id for result in results] == ["TWSE:2330", "TPEX:2330", "TWSE:2330"]
