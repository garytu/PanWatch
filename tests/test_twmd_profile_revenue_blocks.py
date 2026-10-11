from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from marketdata import MarketData, SourceConfig
from marketdata.defaults import StaticConfigProvider
from marketdata.errors import TwmdReadError
from marketdata.vendors import twmd
from src.modules.research.twmd_profile_revenue import TwmdProfileRevenueResearch


FIXTURES = Path(__file__).parent.parent / "packages" / "marketdata" / "tests" / "fixtures" / "twmd"


def captured(label: str) -> dict:
    fixture = json.loads((FIXTURES / "captured" / "2026-10-06.json").read_text())
    return copy.deepcopy(next(case for case in fixture["cases"] if case["label"] == label))


def profile_capture(instrument_id: str) -> dict:
    fixture = json.loads((FIXTURES / "captured" / "2026-10-06.json").read_text())
    return copy.deepcopy(next(
        result["response"]
        for result in fixture["company_profile_attempts"]["successful_extended_reads"]["results"]
        if result["instrument_id"] == instrument_id
    ))


def market_data():
    return MarketData(StaticConfigProvider({
        "company_profile": [SourceConfig(vendor="twmd", config={"base_url": "http://fixture"})],
        "monthly_revenue": [SourceConfig(vendor="twmd", config={"base_url": "http://fixture"})],
    }))


@pytest.fixture(autouse=True)
def clear_read_caches():
    twmd._company_profile_cache.clear()
    twmd._monthly_revenue_cache.clear()
    yield
    twmd._company_profile_cache.clear()
    twmd._monthly_revenue_cache.clear()


def test_marketdata_exposes_separate_twmd_profile_and_monthly_reads(monkeypatch):
    profile = profile_capture("TWSE:2330")
    revenue = captured("TWSE:2330 monthly revenue 2026-07..08 partial coverage")["response"]

    def get_response(self, path, **params):
        return (profile if path == "company-profiles" else revenue), {}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", get_response)
    client = market_data()

    profile_read = client.company_profile("TWSE:2330")
    revenue_read = client.monthly_revenues(
        "TWSE:2330", "2026-07", "2026-08", today_taipei=date(2026, 10, 7)
    )

    assert profile_read.profile is not None
    assert revenue_read.months[1].row is not None
    assert profile_read.profile.report_date != revenue_read.months[1].row.data_month


def test_profile_research_block_keeps_absent_snapshot_and_retained_profile_provenance(monkeypatch):
    fixture = json.loads((FIXTURES / "synthetic" / "offline_edge_cases.json").read_text())
    response = copy.deepcopy(next(
        item["response"] for item in fixture["api_responses"]
        if item["id"] == "profile_absent_from_new_snapshot_retains_older_profile"
    ))
    monkeypatch.setattr(twmd.TwmdClient, "get_response", lambda self, path, **params: (response, {}))

    block = TwmdProfileRevenueResearch(market_data()).company_profile("TWSE:2330")

    assert block.status == "partial"
    assert block.reason == "issuer_absent_from_latest_snapshot_retained_profile"
    assert block.data is not None and block.data["profile"] is not None
    assert block.data["latest_snapshot_presence"] == "absent"
    assert block.evidence["publication_time"] is None
    assert block.evidence["retained_profile"]["report_date"] == "2026-09-30"
    assert block.evidence["latest_snapshot"]["report_date"] == "2026-10-03"
    assert block.evidence["retained_profile"]["received_at_utc"] != block.evidence["latest_snapshot"]["received_at_utc"]


def test_revenue_research_block_keeps_month_gaps_and_receipts_per_month(monkeypatch):
    response = captured("TPEX:5347 monthly revenue 2026-07..08 partial coverage")["response"]
    monkeypatch.setattr(twmd.TwmdClient, "get_response", lambda self, path, **params: (response, {}))

    block = TwmdProfileRevenueResearch(market_data()).adjacent_monthly_revenues(
        "TPEX:5347", "2026-08", today_taipei=date(2026, 10, 7)
    )

    assert block.status == "partial"
    assert block.data is not None
    assert block.data["months"][0]["presence"] == "missing"
    assert block.data["months"][0]["row"] is None
    assert block.data["months"][1]["row"]["monthly_revenue"] == "5092599"
    assert block.evidence["publication_time"] is None
    assert block.evidence["selected_instrument_presence"] == "mixed"
    assert block.evidence["per_month_presence_and_provenance"][1]["retained_row"]["received_at_utc"]
    assert block.evidence["per_month_presence_and_provenance"][0]["retained_row"] is None


def test_research_block_reports_profile_timeout_as_error_without_empty_data(monkeypatch):
    def fail(self, path, **params):
        raise TwmdReadError("profile timed out", reason_code="timeout")

    monkeypatch.setattr(twmd.TwmdClient, "get_response", fail)

    block = TwmdProfileRevenueResearch(market_data()).company_profile("TWSE:2330")

    assert block.status == "error" and block.reason == "timeout"
    assert block.data is None
    assert block.evidence["dataset_coverage"] is None
    assert block.evidence["source_received_at_utc"] is None
