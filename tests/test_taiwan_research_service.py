from __future__ import annotations

import copy
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from marketdata.errors import TwmdReadError
from marketdata.vendors import twmd
from src.modules.research.taiwan_research import (
    TaiwanResearchService,
    _empty_evidence,
    _status_error,
    _scope,
    clear_taiwan_research_cache,
)

FIXTURES = Path(__file__).parent.parent / "packages/marketdata/tests/fixtures/twmd"


def _captured(label: str) -> dict:
    fixture = json.loads((FIXTURES / "captured/2026-10-06.json").read_text())
    return copy.deepcopy(next(item for item in fixture["cases"] if item["label"] == label))


def _profile(instrument_id: str) -> dict:
    fixture = json.loads((FIXTURES / "captured/2026-10-06.json").read_text())
    return copy.deepcopy(next(
        item["response"]
        for item in fixture["company_profile_attempts"]["successful_extended_reads"]["results"]
        if item["instrument_id"] == instrument_id
    ))


@pytest.fixture(autouse=True)
def clear_research_caches():
    clear_taiwan_research_cache()
    twmd._company_profile_cache.clear()
    twmd._monthly_revenue_cache.clear()
    twmd._financial_statements_cache.clear()
    yield
    clear_taiwan_research_cache()
    twmd._company_profile_cache.clear()
    twmd._monthly_revenue_cache.clear()
    twmd._financial_statements_cache.clear()


def _catalog(instrument_id="TWSE:2330", security_type="EQUITY"):
    venue, symbol = instrument_id.split(":", 1)
    return [{
        "instrument_id": instrument_id,
        "symbol": symbol,
        "venue": venue,
        "security_type": security_type,
        "is_active": True,
        "name": "台積電" if symbol == "2330" else "ETF",
    }]


def test_four_research_blocks_keep_exact_values_dates_units_and_period_evidence(monkeypatch):
    catalog = _catalog() + [
        {"instrument_id": "TWSE:00999", "venue": "TWSE", "symbol": "00999", "security_type": "ETN", "is_active": True, "name": "測試 ETN"},
        {"instrument_id": "TPEX:00000", "venue": "TPEX", "symbol": "00000", "security_type": "OTHER", "is_active": False, "name": "測試其他商品"},
    ]
    valuation = _captured("TWSE:2330 valuation 2026-10-02")
    flow = _captured("TWSE:2330 institutional flow 2026-10-02 available")
    revenue = _captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")
    profile = _profile("TWSE:2330")
    financial = json.loads((FIXTURES / "captured/financial-statements-twse-2330-2024q4.json").read_text())
    calls = []

    def response(_self, path, **params):
        calls.append((path, params))
        if path == "instruments":
            return catalog, {}
        if path == "valuations":
            return valuation["response"], valuation["response_headers"]
        if path == "institutional-flows":
            return flow["response"], flow["response_headers"]
        if path == "company-profiles":
            return profile, {}
        if path == "financial-statements":
            assert params["instrument_id"] == "TWSE:2330"
            assert params["fiscal_year"] == 2024 and params["fiscal_quarter"] == 4
            assert params["report_scope"] == "consolidated" and params["limit"] == 1000
            return financial, {}
        if path == "monthly-revenues":
            return revenue["response"], {}
        if path == "broker-flow/quantities":
            return [], {}
        if path == "broker-flow/coverage":
            return [{
                "provider": "twse", "dataset": "broker_flow", "instrument_id": "TWSE:2330",
                "trade_date": params["start"], "status": "MISSING", "record_count": 0,
                "revision_id": None, "failure_reason": None,
            }], {}
        if path == "broker-flow/price-levels":
            assert params["date"] == "2026-10-02"
            return [], {}
        if path in {"margin-short-sale", "shareholder-distribution", "coverage"}:
            return [], {}
        raise AssertionError(path)

    monkeypatch.setattr(twmd.TwmdClient, "get_response", response)
    service = TaiwanResearchService(config={"base_url": "http://fixture", "timeout_sec": 5})
    frozen_now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    payload = service.collect(
        "2330", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-07", end_month="2026-08", today_taipei=date(2026, 10, 7),
        fiscal_year=2024, fiscal_quarter=4, now_utc=frozen_now,
    )

    blocks = payload["blocks"]
    assert payload["instrument_id"] == "TWSE:2330"
    assert payload["instrument"]["security_type"] == "EQUITY"
    assert payload["limitations"]["financial_statements"]["status"] == "limited_scope"
    assert "duration facts may be YTD" in payload["limitations"]["financial_statements"]["message"]
    assert payload["selectors"]["fiscal_year"] == 2024 and payload["selectors"]["fiscal_quarter"] == 4
    assert blocks["valuation"]["data"]["observations"][0]["pe_ratio"] == "28.98"
    assert blocks["valuation"]["evidence"]["period"]["trade_dates"] == ["2026-10-02"]
    assert blocks["institutional_flows"]["data"]["observations"][0]["native_unit"] == "shares"
    assert blocks["institutional_flows"]["evidence"]["per_period_coverage"][0]["trade_date"] == "2026-10-02"
    assert blocks["company_profile"]["data"]["profile"]["paid_in_capital"] == "259323700670"
    assert blocks["monthly_revenues"]["status"] == "partial"
    assert blocks["monthly_revenues"]["data"]["months"][0]["presence"] == "missing"
    assert blocks["monthly_revenues"]["data"]["months"][1]["row"]["monthly_revenue"] == "514805337"
    assert blocks["monthly_revenues"]["evidence"]["publication_time"] is None
    assert blocks["financial_statements"]["status"] == "available"
    assert blocks["financial_statements"]["data"]["facts"][0]["value"] == "2127627043000"
    assert blocks["financial_statements"]["evidence"]["selectors"]["fiscal_quarter"] == 4
    assert all(
        block["evidence"]["freshness"]["evaluated_at_utc"] == "2026-10-07T00:00:00Z"
        for block in blocks.values()
    )
    profile_freshness = blocks["company_profile"]["evidence"]["freshness"]
    assert profile_freshness["data_period"] == "2026-10-03"
    assert profile_freshness["source_received_at_utc"] == "2026-10-04T13:07:03.960501Z"
    assert profile_freshness["latest_snapshot"]["report_date"] == "2026-10-03"
    assert profile_freshness["latest_snapshot"]["source_received_at_utc"] == "2026-10-04T13:07:03.960501Z"
    revenue_freshness = blocks["monthly_revenues"]["evidence"]["freshness"]
    assert revenue_freshness["data_period"] == "2026-08"
    assert revenue_freshness["report_date"] == "2026-10-04"
    assert revenue_freshness["data_period_age_days"] == 37
    assert revenue_freshness["source_received_at_utc"] == "2026-10-04T13:09:22.189587Z"
    assert revenue_freshness["source_served_at"] == "2026-10-06T15:53:57.964571Z"
    assert blocks["monthly_revenues"]["evidence"]["served_at"] == "2026-10-06T15:53:57.964571Z"
    assert revenue_freshness["coverage"]["month_presence_counts"] == {"missing": 1, "present": 1}

    later = service.collect(
        "2330", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-07", end_month="2026-08", today_taipei=date(2026, 10, 7),
        fiscal_year=2024, fiscal_quarter=4, now_utc=frozen_now + timedelta(hours=1),
    )
    assert len(calls) == 14
    assert later["blocks"]["monthly_revenues"]["evidence"]["freshness"]["source_receipt_age_seconds"] == (
        revenue_freshness["source_receipt_age_seconds"] + 3600
    )
    assert later["blocks"]["monthly_revenues"]["evidence"]["freshness"]["evaluated_at_utc"] == "2026-10-07T01:00:00Z"
    assert later["blocks"]["monthly_revenues"]["evidence"]["served_at"] == "2026-10-06T15:53:57.964571Z"


def test_freshness_selects_latest_daily_row_and_keeps_oct_05_flow_gap():
    from src.modules.research.taiwan_research import _freshness_metadata
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock

    frozen_now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    valuation = ResearchDataBlock(
        data={"observations": []}, status="partial", reason="some_requested_dates_missing",
        evidence={
            "selectors": {"start": "2026-10-02", "end": "2026-10-05"},
            "dataset_coverage": "available=2;missing=0;selected=available",
            "per_period_provenance": [
                {"trade_date": "2026-10-02", "received_at_utc": "2026-10-03T01:00:00Z"},
                {"trade_date": "2026-10-05", "received_at_utc": "2026-10-06T01:00:00Z"},
            ],
        },
    )
    flow = ResearchDataBlock(
        data={"observations": []}, status="partial", reason="some_requested_dates_missing",
        evidence={
            "selectors": {"start_date": "2026-10-02", "end_date": "2026-10-05"},
            "per_period_provenance": [
                {"trade_date": "2026-10-02", "acquired_at": "2026-10-03T02:00:00Z"},
            ],
            "per_period_coverage": [
                {"trade_date": "2026-10-02", "status": "AVAILABLE", "selected_instrument_presence": "present"},
                {"trade_date": "2026-10-05", "status": "MISSING", "selected_instrument_presence": "missing"},
            ],
        },
    )

    valuation_freshness = _freshness_metadata("valuation", valuation, frozen_now)
    flow_freshness = _freshness_metadata("institutional_flows", flow, frozen_now)

    assert valuation_freshness["data_period"] == "2026-10-05"
    assert valuation_freshness["source_received_at_utc"] == "2026-10-06T01:00:00Z"
    assert valuation_freshness["data_period_age_days"] == 2
    assert valuation_freshness["source_receipt_age_seconds"] == 82_800
    assert flow_freshness["data_period"] == "2026-10-02"
    assert flow_freshness["source_received_at_utc"] == "2026-10-03T02:00:00Z"
    assert flow_freshness["coverage"]["reported_status_counts"] == {"AVAILABLE": 1, "MISSING": 1}
    assert flow_freshness["coverage"]["interpretation"].find("no exchange-calendar inference") >= 0


def test_retained_profile_freshness_does_not_use_newer_absent_snapshot_receipt():
    from src.modules.research.taiwan_research import _freshness_metadata
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock

    block = ResearchDataBlock(
        data=None, status="partial", reason="retained_profile_snapshot_absent",
        evidence={
            "selectors": {"instrument_id": "TWSE:2330"},
            "dataset_coverage": "AVAILABLE",
            "selected_instrument_presence": "absent",
            "retained_profile": {
                "report_date": "2026-10-02",
                "received_at_utc": "2026-10-05T00:00:00Z",
            },
            "latest_snapshot": {
                "report_date": "2026-10-06",
                "received_at_utc": "2026-10-06T23:00:00Z",
            },
        },
    )

    freshness = _freshness_metadata(
        "company_profile", block, datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    )

    assert freshness["data_period"] == "2026-10-02"
    assert freshness["source_received_at_utc"] == "2026-10-05T00:00:00Z"
    assert freshness["source_receipt_age_seconds"] == 172_800
    assert freshness["latest_snapshot"]["source_received_at_utc"] == "2026-10-06T23:00:00Z"
    assert freshness["latest_snapshot"]["source_receipt_age_seconds"] == 3600


def test_request_clock_uses_taipei_date_across_utc_midnight():
    from marketdata.errors import TwmdReadError

    class FailingReads:
        def get_response(self, path, **_params):
            assert path == "instruments"
            return _catalog(), {}

        def valuation_history(self, *_args, **_kwargs):
            raise TwmdReadError("offline", status_code=503, reason_code="http_503")

        institutional_flows = company_profile = monthly_revenues = valuation_history

    payload = TaiwanResearchService(
        client=FailingReads(), config={"base_url": "http://fixture"}
    ).collect(
        "TWSE:2330", now_utc=datetime(2026, 10, 6, 16, 30, tzinfo=timezone.utc)
    )

    assert payload["selectors"]["end_date"] == "2026-10-06"
    assert payload["selectors"]["end_month"] == "2026-09"
    assert all(
        block["evidence"]["freshness"]["evaluated_at_utc"] == "2026-10-06T16:30:00Z"
        for block in payload["blocks"].values()
    )


def test_one_provider_failure_does_not_erase_other_blocks(monkeypatch):
    catalog = _catalog()
    valuation = _captured("TWSE:2330 valuation 2026-10-02")
    flow = _captured("TWSE:2330 institutional flow 2026-10-02 available")
    revenue = _captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")
    profile = _profile("TWSE:2330")

    def response(_self, path, **params):
        if path == "instruments": return catalog, {}
        if path == "valuations": return valuation["response"], valuation["response_headers"]
        if path == "institutional-flows": raise TwmdReadError("private URL/token details", status_code=503, reason_code="http_503")
        if path == "company-profiles": return profile, {}
        if path == "monthly-revenues": return revenue["response"], {}
        if path == "broker-flow/quantities": return [], {}
        if path == "broker-flow/coverage": return [{
            "provider": "twse", "dataset": "broker_flow", "instrument_id": "TWSE:2330",
            "trade_date": params["start"], "status": "MISSING", "record_count": 0,
            "revision_id": None, "failure_reason": None,
        }], {}
        if path == "broker-flow/price-levels": return [], {}
        if path in {"margin-short-sale", "shareholder-distribution", "coverage"}: return [], {}
        raise AssertionError(path)

    monkeypatch.setattr(twmd.TwmdClient, "get_response", response)
    payload = TaiwanResearchService(config={"base_url": "http://fixture"}).collect(
        "TWSE:2330", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-07", end_month="2026-08", today_taipei=date(2026, 10, 7),
    )

    assert payload["blocks"]["institutional_flows"]["status"] == "error"
    assert payload["blocks"]["institutional_flows"]["reason"] == "http_503"
    assert payload["blocks"]["institutional_flows"]["evidence"]["freshness"]["age_status"] == "age_unknown"
    assert payload["blocks"]["valuation"]["evidence"]["freshness"]["age_status"] == "age_unknown"
    assert "private URL" not in json.dumps(payload)
    assert payload["blocks"]["valuation"]["data"] is not None
    assert payload["blocks"]["company_profile"]["data"] is not None
    assert payload["blocks"]["monthly_revenues"]["data"] is not None


def test_ambiguous_bare_code_stops_before_dataset_reads(monkeypatch):
    rows = _catalog("TWSE:1111") + _catalog("TPEX:1111")
    calls = []

    def response(_self, path, **params):
        calls.append(path)
        return rows, {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", response)
    payload = TaiwanResearchService(config={"base_url": "http://fixture"}).collect(
        "1111", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-07", end_month="2026-08", today_taipei=date(2026, 10, 7),
    )

    assert calls == ["instruments"]
    assert all(block["reason"] == "ambiguous_instrument" for block in payload["blocks"].values())
    assert all(block["status"] == "error" for block in payload["blocks"].values())


def test_known_unsupported_catalog_type_does_not_invalidate_catalog_and_not_found_is_explicit(monkeypatch):
    rows = _catalog("TWSE:2330") + _catalog("TPEX:1111", "ETN")

    class FakeClient:
        def get_response(self, path, **params):
            assert path == "instruments"
            return rows, {}

        def financial_statements(self, instrument_id, fiscal_year, fiscal_quarter, **_params):
            from marketdata.financial_statements import decode_financial_statement_response
            return decode_financial_statement_response({
                "instrument_id": instrument_id, "fiscal_year": fiscal_year, "fiscal_quarter": fiscal_quarter,
                "report_scope": "consolidated", "statement": None,
                "qualification": {"status": "pending", "reason": "catalog_evidence_missing", "industry_code": None, "catalog_evidence": None, "profile_evidence": None},
                "coverage": {"status": "MISSING", "reason": "qualification_pending", "latest_discovery_presence": "missing", "capture_id": None, "original_received_at_utc": None},
                "report": None, "facts": [], "total_fact_count": 0, "returned_fact_count": 0, "truncated": False,
            }, instrument_id=instrument_id, fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter,
               report_scope="consolidated", statement=None, limit=1000)

    service = TaiwanResearchService(client=FakeClient(), config={"base_url": "http://fixture"})
    unsupported = service.collect(
        "TPEX:1111", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-08", end_month="2026-08", today_taipei=date(2026, 10, 7),
    )
    missing = service.collect(
        "TWSE:9999", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-08", end_month="2026-08", today_taipei=date(2026, 10, 7),
    )

    assert all(block["status"] == "unsupported" for block in unsupported["blocks"].values())
    assert all(block["reason"] == "unsupported_security_type" for block in unsupported["blocks"].values())
    assert all(block["reason"] == "instrument_not_found" for name, block in missing["blocks"].items() if name != "financial_statements")
    assert missing["blocks"]["financial_statements"]["status"] == "missing"
    assert missing["blocks"]["financial_statements"]["reason"] == "qualification_pending"


@pytest.mark.parametrize("catalog_state", ["inactive", "omitted"])
def test_retained_financial_report_survives_later_catalog_changes(catalog_state):
    from marketdata.financial_statements import decode_financial_statement_response
    response = json.loads((FIXTURES / "captured/financial-statements-twse-2330-2024q4.json").read_text())
    response["coverage"].update(latest_discovery_presence="not_advertised", capture_id="later-discovery",
                                original_received_at_utc="2026-10-06T00:00:00Z")
    rows = _catalog()
    rows[0]["is_active"] = False
    calls = []

    class Client:
        def get_response(self, path, **_params):
            assert path == "instruments"
            return (rows if catalog_state == "inactive" else []), {}

        def financial_statements(self, instrument_id, fiscal_year, fiscal_quarter, **params):
            calls.append(instrument_id)
            return decode_financial_statement_response(response, instrument_id=instrument_id,
                fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter, report_scope="consolidated",
                statement=params.get("statement"), limit=1000)

    result = TaiwanResearchService(client=Client(), config={"base_url": f"http://{catalog_state}"}).collect(
        "TWSE:2330", fiscal_year=2024, fiscal_quarter=4, today_taipei=date(2026, 10, 7))
    block = result["blocks"]["financial_statements"]
    assert block["status"] == "available" and len(block["data"]["facts"]) == 394
    assert block["evidence"]["latest_discovery"]["latest_discovery_presence"] == "not_advertised"
    assert block["data"]["qualification"]["catalog_evidence"]["is_active"] is True
    assert result["blocks"]["valuation"]["status"] == "unsupported"
    assert calls == ["TWSE:2330"]


def test_etf_preflight_keeps_available_blocks_and_marks_profile_revenue_unsupported(monkeypatch):
    from marketdata.types import TwmdMonthlyRevenueMonth

    catalog = _catalog("TWSE:00878", "ETF")

    class FakeClient:
        def get_response(self, path, **params):
            assert path == "instruments"
            return catalog, {}

        def valuation_history(self, *_args, **_kwargs):
            return SimpleNamespace(
                instrument_id="TWSE:00878", endpoint="/api/v1/valuations",
                start_date="2026-10-02", end_date="2026-10-02", data=[],
                status="unknown", reason="coverage_not_returned", coverage_header=None,
                selected_instrument_presence="unknown", response_headers={},
            )

        def institutional_flows(self, *_args, **_kwargs):
            return SimpleNamespace(
                instrument_id="TWSE:00878", endpoint="/api/v1/institutional-flows",
                start_date="2026-10-02", end_date="2026-10-02", data=[], coverage=[],
                source_contract="twse_institutional_flows/v1", native_unit="shares",
                status="missing", reason="coverage_missing",
            )

        def company_profile(self, *_args, **_kwargs):
            return SimpleNamespace(
                profile=None, latest_snapshot=None, instrument_id="TWSE:00878",
                qualification="unsupported_etf", qualification_reason="current_catalog_security_type_etf",
                latest_snapshot_presence="absent", units={}, endpoint="/api/v1/company-profiles",
                source_contract="twse_profile/v1", coverage_status="AVAILABLE",
                status="unsupported", reason="unsupported_etf",
            )

        def monthly_revenues(self, *_args, **_kwargs):
            return SimpleNamespace(
                instrument_id="TWSE:00878", endpoint="/api/v1/monthly-revenues",
                dataset="twse_monthly_revenue", start_month="2026-08-01", end_month="2026-08-01",
                schema_ready=True, coverage_status="AVAILABLE", qualification="unsupported_etf",
                qualification_reason="current_catalog_security_type_etf", current_catalog_evidence={},
                units={"monthly_revenue": "TWD thousands (inferred)"}, coverage=[],
                months=[TwmdMonthlyRevenueMonth("2026-08-01", "not_in_captured_report", None)],
                served_at="2026-10-06T10:00:00Z", status="unsupported", reason="unsupported_etf",
            )

        def financial_statements(self, *_args, **_kwargs):
            pytest.fail("ETF must not invoke financial-statements API")

    payload = TaiwanResearchService(client=FakeClient(), config={"base_url": "http://fixture"}).collect(
        "TWSE:00878", start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-08", end_month="2026-08", today_taipei=date(2026, 10, 7),
    )

    assert payload["instrument"]["security_type"] == "ETF"
    assert payload["blocks"]["valuation"]["status"] == "unknown"
    assert payload["blocks"]["company_profile"]["status"] == "unsupported"
    assert payload["blocks"]["monthly_revenues"]["reason"] == "unsupported_etf"
    assert payload["blocks"]["financial_statements"]["status"] == "unsupported"
    assert payload["blocks"]["financial_statements"]["reason"] == "financial_statements_security_type_not_supported"


def test_tpex_financial_statements_are_unsupported_without_provider_read():
    catalog = _catalog("TPEX:5347", "EQUITY")

    class FakeClient:
        def get_response(self, path, **_params):
            return (catalog, {}) if path == "instruments" else ([], {})

        def financial_statements(self, *_args, **_kwargs):
            pytest.fail("TPEX must not invoke financial-statements API")

    payload = TaiwanResearchService(
        client=FakeClient(), config={"base_url": "http://financial-scope-test"}
    ).collect("TPEX:5347", fiscal_year=2024, fiscal_quarter=4, today_taipei=date(2026, 10, 7))

    block = payload["blocks"]["financial_statements"]
    assert payload["selectors"]["fiscal_year"] == 2024
    assert payload["selectors"]["fiscal_quarter"] == 4
    assert block["status"] == "unsupported"
    assert block["reason"] == "financial_statements_twse_only"


def test_tpex_six_digit_etf_skips_unsupported_valuation_selector_and_keeps_flows():
    from marketdata.types import (
        InstitutionalFlowCoverage,
        InstitutionalFlowObservation,
        TwmdMonthlyRevenueMonth,
    )

    instrument_id = "TPEX:006201"
    catalog = _catalog(instrument_id, "ETF")
    flow_row = InstitutionalFlowObservation(
        instrument_id=instrument_id,
        symbol="006201",
        name="ETF",
        trade_date="2026-10-02",
        native_unit="shares",
        native_values={"total_institutional_net_shares": 1000},
        source_contract="tpex_institutional_flows/v1",
    )

    class FakeClient:
        calls = []

        def get_response(self, path, **params):
            assert path == "instruments"
            return catalog, {}

        def valuation_history(self, *_args, **_kwargs):
            raise AssertionError("the incompatible TPEx selector must be preflighted")

        def institutional_flows(self, *_args, **_kwargs):
            self.calls.append("flows")
            return SimpleNamespace(
                instrument_id=instrument_id, endpoint="/api/v1/institutional-flows",
                start_date="2026-10-02", end_date="2026-10-02",
                source_contract="tpex_institutional_flows/v1", native_unit="shares",
                schema_ready=True, coverage=[InstitutionalFlowCoverage(
                    trade_date="2026-10-02", status="AVAILABLE", record_count=1
                )], data=[flow_row], status="available", reason="selected_record_present",
                response_headers={},
            )

        def company_profile(self, *_args, **_kwargs):
            return SimpleNamespace(
                profile=None, latest_snapshot=None, instrument_id=instrument_id,
                qualification="unsupported_etf", qualification_reason="current_catalog_security_type_etf",
                latest_snapshot_presence="absent", units={}, endpoint="/api/v1/company-profiles",
                source_contract="tpex_profile/v1", coverage_status="AVAILABLE",
                status="unsupported", reason="unsupported_etf",
            )

        def monthly_revenues(self, *_args, **_kwargs):
            return SimpleNamespace(
                instrument_id=instrument_id, endpoint="/api/v1/monthly-revenues",
                dataset="tpex_monthly_revenue_latest", start_month="2026-08-01", end_month="2026-08-01",
                schema_ready=True, coverage_status="AVAILABLE", qualification="unsupported_etf",
                qualification_reason="current_catalog_security_type_etf", current_catalog_evidence={},
                units={"revenue": "TWD thousands (inferred)"}, coverage=[],
                months=[TwmdMonthlyRevenueMonth("2026-08-01", "not_in_captured_report", None)],
                served_at="2026-10-06T10:00:00Z", status="unsupported", reason="unsupported_etf",
            )

    client = FakeClient()
    payload = TaiwanResearchService(client=client, config={"base_url": "http://fixture"}).collect(
        instrument_id, start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-08", end_month="2026-08", today_taipei=date(2026, 10, 7),
    )

    assert client.calls == ["flows"]
    assert payload["blocks"]["valuation"]["status"] == "unsupported"
    assert payload["blocks"]["valuation"]["reason"] == "unsupported_valuation_selector"
    assert payload["blocks"]["institutional_flows"]["status"] == "available"
    assert payload["blocks"]["institutional_flows"]["data"]["observations"][0]["native_values"]["total_institutional_net_shares"] == 1000
    assert payload["blocks"]["monthly_revenues"]["status"] == "unsupported"


@pytest.mark.parametrize("kwargs", [
    {"start_date": "2026-10-06", "end_date": "2026-10-07"},
    {"start_date": "2023-12-31", "end_date": "2024-01-01"},
    {"start_month": "2026-09", "end_month": "2026-11"},
    {"start_month": "2023-12", "end_month": "2024-01"},
])
def test_api_rejects_future_dates_months_and_ranges_outside_contract(kwargs):
    with pytest.raises(ValueError):
        TaiwanResearchService(config={"base_url": "http://fixture"}).collect(
            "TWSE:2330", today_taipei=date(2026, 10, 7), **kwargs
        )


def test_cache_scope_hashes_credentials_without_storing_token_text():
    one = _scope({"base_url": "http://fixture", "token": "secret-one"})
    two = _scope({"base_url": "http://fixture", "token": "secret-two"})
    assert one != two
    assert "secret-one" not in repr(one)
    assert "secret-two" not in repr(two)


def test_unknown_provider_reason_codes_are_sanitized():
    reason, status = _status_error(
        TwmdReadError("private token path", status_code=503, reason_code="secret-token-value")
    )
    assert (reason, status) == ("http_503", 503)
    assert _status_error(TwmdReadError("secret", reason_code="private_value")) == (
        "provider_error", None
    )


def test_public_evidence_scope_omits_url_credentials_path_and_query():
    evidence = _empty_evidence(
        "TWSE:2330", "/api/v1/valuations", {},
        {
            "base_url": "https://user:password@query.example:8443/private/path?api_key=url-secret#frag",
            "token": "bearer-secret",
        },
    )
    assert evidence["provider_scope"] == "https://query.example:8443"
    assert "password" not in json.dumps(evidence)
    assert "url-secret" not in json.dumps(evidence)
    assert "bearer-secret" not in json.dumps(evidence)


def test_catalog_deadline_keeps_actual_read_permits_until_workers_finish(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import src.modules.research.taiwan_research as research

    released = threading.Event()
    pool = ThreadPoolExecutor(max_workers=4)
    calls = []

    class StalledCatalog:
        def get_response(self, path, **params):
            calls.append(path)
            assert released.wait(2)
            return _catalog(), {}

    monkeypatch.setattr(research, "_READ_POOL", pool)
    monkeypatch.setattr(research, "_READ_SLOTS", threading.BoundedSemaphore(4))
    monkeypatch.setattr(research, "_REQUEST_DEADLINE_SECONDS", 0.02)
    frozen_now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    try:
        service = TaiwanResearchService(client=StalledCatalog(), config={"base_url": "http://fixture"})
        began = time.monotonic()
        results = [service.collect(
            "TWSE:2330", today_taipei=date(2026, 10, 7), now_utc=frozen_now
        ) for _ in range(5)]
        assert time.monotonic() - began < 0.5
        assert len(calls) == 4
        assert all(block["reason"] == "timeout" for result in results[:4] for block in result["blocks"].values())
        assert all(
            block["evidence"]["freshness"]["evaluated_at_utc"] == "2026-10-07T00:00:00Z"
            and block["evidence"]["freshness"]["age_status"] == "age_unknown"
            for result in results[:4] for block in result["blocks"].values()
        )
        assert all(block["reason"] == "concurrency_limit" for block in results[4]["blocks"].values())
    finally:
        released.set()
        pool.shutdown(wait=True)


def test_valuation_evidence_exports_only_contract_headers():
    from src.modules.research.taiwan_research import _valuation_block

    read = SimpleNamespace(
        instrument_id="TPEX:5347", data=[], status="missing", reason="coverage_missing",
        endpoint="/api/v1/valuations", start_date="2026-10-02", end_date="2026-10-02",
        coverage_header="available=0;missing=1;selected=missing",
        selected_instrument_presence="missing", response_headers={
            "x-twmd-schema-ready": "true", "x-twmd-coverage": "available=0;missing=1;selected=missing",
            "authorization": "Bearer private-token", "set-cookie": "session=private-cookie",
        },
    )
    block = _valuation_block(read)
    assert set(block.evidence["response_headers"]) == {"x-twmd-schema-ready", "x-twmd-coverage"}
    assert "private" not in json.dumps(block.evidence)


@pytest.mark.parametrize("symbol,quote,expected", [
    ("TWSE:2330", "TPEX:2330", "TWSE:2330"),
    ("2330", "TPEX:2330", "TPEX:2330"),
    ("2330", "TWSE:5347", "2330"),
    ("2330", "bad", "2330"),
])
def test_quote_hint_cannot_override_requested_canonical_identity(symbol, quote, expected):
    from src.modules.research.taiwan_research import taiwan_research_identity
    assert taiwan_research_identity(symbol, quote) == expected


def test_service_cache_isolated_by_venue_credentials_service_and_range(monkeypatch):
    import src.modules.research.taiwan_research as research
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock

    monkeypatch.setattr(research, "_instrument_catalog", lambda *_: _catalog("TWSE:2330") + _catalog("TPEX:2330"))
    for adapter in (
        "_valuation_block", "_flow_block", "company_profile_block", "monthly_revenue_block",
        "margin_short_sale_block", "shareholder_distribution_block", "financial_statement_block",
    ):
        monkeypatch.setattr(research, adapter, lambda read: ResearchDataBlock(read, "available", "selected_record_present", {"instrument_id": read["identity"]}))
    calls = []

    class Client:
        def __init__(self, marker):
            self.marker = marker

        def read(self, identity, *args, **kwargs):
            calls.append((self.marker, identity))
            return {"identity": identity, "marker": self.marker}

        valuation_history = institutional_flows = company_profile = monthly_revenues = read
        margin_short_sale = shareholder_distribution = read

    def collect(marker, *, token="one", url="http://fixture", identity="TWSE:2330", month="2026-08"):
        return TaiwanResearchService(client=Client(marker), config={"base_url": url, "token": token}).collect(
            identity, start_date="2026-10-02", end_date="2026-10-02",
            start_month=month, end_month=month, today_taipei=date(2026, 10, 7),
        )

    first = collect(1)
    first["blocks"]["valuation"]["data"]["marker"] = "mutated"
    assert collect(9)["blocks"]["valuation"]["data"]["marker"] == 1
    assert len(calls) == 6
    assert collect(2, token="two")["blocks"]["valuation"]["data"]["marker"] == 2
    assert collect(3, url="http://other")["blocks"]["valuation"]["data"]["marker"] == 3
    assert collect(4, identity="TPEX:2330")["blocks"]["valuation"]["data"]["marker"] == 4
    changed_range = collect(5, month="2026-07")
    assert changed_range["blocks"]["valuation"]["data"]["marker"] == 1
    assert changed_range["blocks"]["monthly_revenues"]["data"]["marker"] == 5
    assert len(calls) == 24


def _tdcc_observations(variant: str, report_date: str):
    from decimal import Decimal
    from marketdata.types import TwmdShareholderDistributionObservation

    provider = "tdcc_open_data_1_5" if variant == "bulk_current" else "tdcc_qry_stock"
    rows = []
    for level in range(1, 16):
        label = {
            12: "400,001-600,000", 13: "600,001-800,000",
            14: "800,001-1,000,000", 15: "1,000,001以上",
        }.get(level) if variant == "historical_html" else None
        shares = 1 if level < 12 else (level - 11) * 10000
        rows.append(TwmdShareholderDistributionObservation(
            report_date=report_date, instrument_id="TWSE:2330", symbol="2330",
            report_variant=variant, row_kind="bucket", source_level=level,
            source_tier_label=label, holder_count=level,
            share_count=shares, share_percentage_points=Decimal("1.0"),
            provider=provider, native_unit="shares",
        ))
    total_level = 17 if variant == "bulk_current" else 16
    adjustment = None
    bucket_shares = sum(row.share_count for row in rows)
    if variant == "bulk_current":
        adjustment = TwmdShareholderDistributionObservation(
            report_date=report_date, instrument_id="TWSE:2330", symbol="2330",
            report_variant=variant, row_kind="adjustment", source_level=16,
            source_tier_label=None, holder_count=0, share_count=100,
            share_percentage_points=Decimal("0"), provider=provider, native_unit="shares",
        )
    total = TwmdShareholderDistributionObservation(
        report_date=report_date, instrument_id="TWSE:2330", symbol="2330",
        report_variant=variant, row_kind="total", source_level=total_level,
        source_tier_label="合計", holder_count=sum(row.holder_count for row in rows),
        share_count=bucket_shares - (adjustment.share_count if adjustment else 0),
        share_percentage_points=Decimal("100"), provider=provider, native_unit="shares",
    )
    return rows + ([adjustment] if adjustment else []) + [total]


def test_tdcc_large_holding_uses_adjusted_official_total_and_exact_week_same_variant():
    from src.modules.research.twmd_margin_shareholders import shareholder_distribution_block
    from marketdata.types import TwmdShareholderDistributionRead

    rows = _tdcc_observations("bulk_current", "2026-10-02")
    rows += _tdcc_observations("bulk_current", "2026-09-18")  # 09-25 is missing
    rows += _tdcc_observations("historical_html", "2026-09-25")  # variant cannot be mixed
    read = TwmdShareholderDistributionRead(
        instrument_id="TWSE:2330", endpoint="/api/v1/shareholder-distribution",
        start_date="2026-09-01", end_date="2026-10-06", report_variant=None,
        data=rows, coverage=[], status="available", reason="selected_record_present",
    )

    block = shareholder_distribution_block(read)
    latest = block.data["latest"]
    large = latest["large_holding"]
    assert latest["report_variant"] == "bulk_current"
    assert latest["official_total"]["source_level"] == 17
    assert latest["adjustment"]["source_level"] == 16
    assert large["threshold"] == ">400,000 shares"
    assert large["minimum_shares"] == 400001
    assert large["share_count"] == sum(row.share_count for row in rows[:15] if row.source_level >= 12)
    assert large["denominator_share_count"] == latest["official_total"]["share_count"]
    assert block.data["previous"] is None
    assert block.data["changes"] is None
    assert block.data["comparison_reason"] == "previous_week_unavailable_or_variant_changed"


def test_tdcc_exact_previous_week_same_variant_computes_changes_and_prefers_current_on_tie():
    from dataclasses import replace
    from decimal import Decimal
    from src.modules.research.twmd_margin_shareholders import shareholder_distribution_block
    from marketdata.types import TwmdShareholderDistributionRead

    latest = _tdcc_observations("bulk_current", "2026-10-02")
    prior = []
    for row in _tdcc_observations("bulk_current", "2026-09-25"):
        if row.row_kind == "bucket" and 12 <= row.source_level <= 15:
            row = replace(row, share_count=row.share_count - 100)
        elif row.row_kind == "total":
            row = replace(row, share_count=row.share_count - 400)
        prior.append(row)
    same_day_history = _tdcc_observations("historical_html", "2026-10-02")
    read = TwmdShareholderDistributionRead(
        instrument_id="TWSE:2330", endpoint="/api/v1/shareholder-distribution",
        start_date="2026-09-01", end_date="2026-10-06", report_variant=None,
        data=prior + same_day_history + latest, coverage=[], status="available",
        reason="selected_record_present",
    )

    block = shareholder_distribution_block(read)
    assert block.data["latest"]["report_variant"] == "bulk_current"
    assert block.data["latest_report_selection_policy"] == "prefer_bulk_current_when_both_variants_share_latest_report_date"
    assert block.data["previous"]["report_date"] == "2026-09-25"
    assert block.data["previous"]["report_variant"] == "bulk_current"
    assert block.data["changes"]["total_share_count"] == 400
    assert block.data["changes"]["large_holding_share_count"] == 400
    assert block.data["changes"]["large_holding_percentage_points"] == str(
        Decimal(block.data["latest"]["large_holding"]["percentage_of_official_total"])
        - Decimal(block.data["previous"]["large_holding"]["percentage_of_official_total"])
    )
    assert block.data["comparison_reason"] is None


def test_tdcc_partial_buckets_do_not_create_a_large_holder_denominator():
    from src.modules.research.twmd_margin_shareholders import shareholder_distribution_block
    from marketdata.types import TwmdShareholderDistributionRead

    read = TwmdShareholderDistributionRead(
        instrument_id="TWSE:2330", endpoint="/api/v1/shareholder-distribution",
        start_date="2026-10-01", end_date="2026-10-06", report_variant="bulk_current",
        data=_tdcc_observations("bulk_current", "2026-10-02")[:-1],
        coverage=[], status="available", reason="selected_record_present",
    )
    with pytest.raises(ValueError, match="every required source level"):
        shareholder_distribution_block(read)


def test_tdcc_unreconciled_official_total_is_rejected_before_ratio_calculation():
    from dataclasses import replace
    from src.modules.research.twmd_margin_shareholders import shareholder_distribution_block
    from marketdata.types import TwmdShareholderDistributionRead

    rows = _tdcc_observations("bulk_current", "2026-10-02")
    rows[-1] = replace(rows[-1], share_count=rows[-1].share_count + 1)
    read = TwmdShareholderDistributionRead(
        instrument_id="TWSE:2330", endpoint="/api/v1/shareholder-distribution",
        start_date="2026-10-01", end_date="2026-10-06", report_variant="bulk_current",
        data=rows, coverage=[], status="available", reason="selected_record_present",
    )

    with pytest.raises(ValueError, match="does not reconcile with adjustment"):
        shareholder_distribution_block(read)


def test_research_reads_queue_all_six_blocks_with_four_active_workers(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import src.modules.research.taiwan_research as research
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock

    pool = ThreadPoolExecutor(max_workers=4)
    caller_pool = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(4)
    all_started = threading.Event()
    release_initial = threading.Event()
    guard = threading.Lock()
    started: list[str] = []
    active = 0
    max_active = 0

    def identity_adapter(read):
        return ResearchDataBlock(read, "available", "selected_record_present", {"provider": "fixture"})

    for adapter in (
        "_valuation_block", "_flow_block", "company_profile_block", "monthly_revenue_block",
        "margin_short_sale_block", "shareholder_distribution_block",
    ):
        monkeypatch.setattr(research, adapter, identity_adapter)
    monkeypatch.setattr(research, "_READ_POOL", pool)
    monkeypatch.setattr(research, "_READ_SLOTS", slots)

    class Client:
        def get_response(self, path, **params):
            assert path == "instruments"
            return _catalog(), {}

        def _read(self, name):
            nonlocal active, max_active
            with guard:
                started.append(name)
                active += 1
                max_active = max(max_active, active)
                if len(started) == 4:
                    all_started.set()
            if name in {"valuation", "institutional_flows", "company_profile", "monthly_revenues"}:
                assert release_initial.wait(2)
            with guard:
                active -= 1
            return {"identity": name}

        def valuation_history(self, *_args, **_kwargs): return self._read("valuation")
        def institutional_flows(self, *_args, **_kwargs): return self._read("institutional_flows")
        def company_profile(self, *_args, **_kwargs): return self._read("company_profile")
        def monthly_revenues(self, *_args, **_kwargs): return self._read("monthly_revenues")
        def margin_short_sale(self, *_args, **_kwargs): return self._read("margin_short_sale")
        def shareholder_distribution(self, *_args, **_kwargs): return self._read("shareholder_distribution")
        def financial_statements(self, *_args, **_kwargs): return self._read("financial_statements")

    future = caller_pool.submit(lambda: TaiwanResearchService(
        client=Client(), config={"base_url": "http://fixture"}
    ).collect("TWSE:2330", today_taipei=date(2026, 10, 7)))
    try:
        assert all_started.wait(2)
        assert set(started) == {"valuation", "institutional_flows", "company_profile", "monthly_revenues"}
        release_initial.set()
        payload = future.result(timeout=3)
        assert set(payload["blocks"]) == {
            "valuation", "institutional_flows", "company_profile", "monthly_revenues",
            "margin_short_sale", "shareholder_distribution", "broker_flow", "financial_statements",
        }
        assert set(started) == {
            "valuation", "institutional_flows", "company_profile", "monthly_revenues",
            "margin_short_sale", "shareholder_distribution", "financial_statements",
        }
        assert max_active <= 4
    finally:
        release_initial.set()
        pool.shutdown(wait=True)
        caller_pool.shutdown(wait=True)


def test_timed_out_reads_keep_permits_and_queued_blocks_are_explicit(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import src.modules.research.taiwan_research as research

    slots = threading.BoundedSemaphore(4)
    pool = ThreadPoolExecutor(max_workers=4)
    release = threading.Event()
    monkeypatch.setattr(research, "_READ_POOL", pool)
    monkeypatch.setattr(research, "_READ_SLOTS", slots)
    monkeypatch.setattr(research, "_REQUEST_DEADLINE_SECONDS", 0.02)
    catalog_key = (*_scope({"base_url": "http://fixture"}), "catalog", "TW")
    research._cache_set(catalog_key, _catalog(), 300)

    class Client:
        def _stall(self, name):
            assert release.wait(2)
            return {"name": name}
        valuation_history = lambda self, *_a, **_k: self._stall("valuation")
        institutional_flows = lambda self, *_a, **_k: self._stall("flows")
        company_profile = lambda self, *_a, **_k: self._stall("profile")
        monthly_revenues = lambda self, *_a, **_k: self._stall("revenue")
        margin_short_sale = lambda self, *_a, **_k: self._stall("margin")
        shareholder_distribution = lambda self, *_a, **_k: self._stall("tdcc")

    try:
        payload = TaiwanResearchService(client=Client(), config={"base_url": "http://fixture"}).collect(
            "TWSE:2330", today_taipei=date(2026, 10, 7)
        )
        assert len(payload["blocks"]) == 8
        assert {block["reason"] for block in payload["blocks"].values()} == {"timeout"}
        assert slots._value == 0
        again = TaiwanResearchService(client=Client(), config={"base_url": "http://fixture"}).collect(
            "TWSE:2330", today_taipei=date(2026, 10, 7)
        )
        assert {block["reason"] for block in again["blocks"].values()} == {"concurrency_limit"}
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_read_lease_keeps_running_permit_and_releases_cancelled_queued_read(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import src.modules.research.taiwan_research as research

    pool = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(2)
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(research, "_READ_POOL", pool)
    monkeypatch.setattr(research, "_READ_SLOTS", slots)

    def blocked_read():
        started.set()
        assert release.wait(2)
        return "finished"

    try:
        running = research._submit_read(blocked_read)
        assert started.wait(2)
        queued = research._submit_read(lambda: "must be cancelled")
        assert slots._value == 0
        assert queued.cancel()
        assert slots._value == 1
        later = research._submit_read(lambda: "finished after the first read")
        assert slots._value == 0
        with pytest.raises(RuntimeError, match="concurrency_limit"):
            research._submit_read(lambda: None)
        release.set()
        assert running.result(timeout=2) == "finished"
        assert later.result(timeout=2) == "finished after the first read"
        assert slots._value == 2
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_tdcc_zero_previous_denominator_preserves_counts_and_null_percentage_delta():
    from dataclasses import replace
    from marketdata.types import TwmdShareholderDistributionRead
    from src.modules.research.twmd_margin_shareholders import shareholder_distribution_block
    latest = _tdcc_observations('historical_html', '2026-10-02')
    previous = [replace(row, holder_count=0, share_count=0) for row in _tdcc_observations('historical_html', '2026-09-25')]
    read = TwmdShareholderDistributionRead('TWSE:2330', '/api/v1/shareholder-distribution',
        '2026-09-25', '2026-10-02', None, previous + latest, [], 'available', 'selected_record_present')
    block = shareholder_distribution_block(read)
    assert block.data['latest']['large_holding']['percentage_of_official_total'] is not None
    assert block.data['previous']['large_holding']['percentage_of_official_total'] is None
    assert block.data['changes']['large_holding_percentage_points'] is None
    assert block.data['changes']['large_holding_share_count'] == block.data['latest']['large_holding']['share_count']


def test_finished_batch_after_deadline_does_not_launch_remaining_reads(monkeypatch):
    import src.modules.research.taiwan_research as research
    from concurrent.futures import Future
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock
    clock = [0.0]
    monkeypatch.setattr(research.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(research, '_REQUEST_DEADLINE_SECONDS', 1)
    research._cache_set((*_scope({'base_url': 'http://deadline-fixture'}), 'catalog', 'TW'), _catalog(), 300)
    submitted = []
    def submit(function, name, key, builder):
        submitted.append(name)
        future = Future()
        future.set_result(ResearchDataBlock({}, 'available', 'selected_record_present', {}))
        return future
    def expired_wait(futures, **kwargs):
        clock[0] = 2.0
        return set(futures), set()
    monkeypatch.setattr(research, '_submit_read', submit)
    monkeypatch.setattr(research, 'wait', expired_wait)
    payload = TaiwanResearchService(client=object(), config={'base_url': 'http://deadline-fixture'}).collect('TWSE:2330', today_taipei=date(2026, 10, 7))
    assert submitted == ['valuation', 'institutional_flows', 'company_profile', 'monthly_revenues']
    assert payload['blocks']['margin_short_sale']['reason'] == 'timeout'
    assert payload['blocks']['shareholder_distribution']['reason'] == 'timeout'
