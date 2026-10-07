from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from marketdata.errors import TwmdReadError
from marketdata.financial_statements import decode_financial_statement_response
from marketdata.vendors import twmd


FIXTURE = Path(__file__).parent / "fixtures" / "twmd" / "captured" / "financial-statements-twse-2330-2024q4.json"


@pytest.fixture
def response():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(autouse=True)
def clear_financial_cache():
    twmd._financial_statements_cache.clear()
    yield
    twmd._financial_statements_cache.clear()


def decode(payload: dict, *, limit: int = 500):
    return decode_financial_statement_response(
        payload,
        instrument_id="TWSE:2330",
        fiscal_year=2024,
        fiscal_quarter=4,
        report_scope="consolidated",
        statement=None,
        limit=limit,
    )


def test_authentic_captured_2024q4_preserves_comparative_ytd_eps_and_scaled_values(response):
    read = decode(response)

    assert read.status == "available"
    assert read.instrument_id == "TWSE:2330"
    assert read.total_fact_count == read.returned_fact_count == 394
    assert read.report.raw_sha256 == "1deba772079ed08cef1ccaf0430f40b4d36819ae5e5405073e793e6dc1932677"

    assets = [fact for fact in read.facts if fact.concept_qname.endswith("}Assets")]
    assert [(fact.context.period.instant, fact.value, fact.lexical_value, fact.scale) for fact in assets] == [
        ("2024-12-31", "6691938000000", "6,691,938,000", 3),
        ("2023-12-31", "5532371215000", "5,532,371,215", 3),
    ]

    eps = next(fact for fact in read.facts if fact.concept_qname.endswith("}BasicEarningsLossPerShare"))
    assert eps.value == "45.25"
    assert eps.lexical_value == "45.25" and eps.scale == 0
    assert eps.unit.numerator == ("{http://www.xbrl.org/2003/iso4217}TWD",)
    assert eps.unit.denominator == ("{http://www.xbrl.org/2003/instance}shares",)

    revenue = next(fact for fact in read.facts if fact.concept_qname.endswith("}Revenue"))
    assert revenue.context.period.start_date == "2024-01-01"
    assert revenue.context.period.end_date == "2024-12-31"
    assert revenue.value == "2894307699000"


def test_retained_report_and_latest_discovery_are_independent(response):
    response["coverage"].update(
        status="AVAILABLE",
        reason="report_retained",
        latest_discovery_presence="not_advertised",
        capture_id="a" * 64,
        original_received_at_utc="2026-10-06T06:00:00Z",
    )

    read = decode(response)

    assert read.report is not None
    assert read.coverage.latest_discovery_presence == "not_advertised"
    assert read.status == "available"


def test_missing_no_report_and_unsupported_are_not_fabricated(response):
    missing = copy.deepcopy(response)
    missing.update(report=None, facts=[], total_fact_count=0, returned_fact_count=0, truncated=False)
    missing["coverage"].update(
        status="MISSING", reason="report_not_advertised",
        latest_discovery_presence="not_advertised",
    )
    assert decode(missing).status == "missing"

    unsupported = copy.deepcopy(missing)
    unsupported["qualification"].update(status="unsupported", reason="industry_not_admitted", industry_code="20")
    unsupported["qualification"]["profile_evidence"]["industry_code"] = "20"
    unsupported["coverage"].update(reason="industry_not_admitted", latest_discovery_presence="missing", capture_id=None, original_received_at_utc=None)
    result = decode(unsupported)
    assert result.status == "unsupported" and result.facts == () and result.report is None


def test_truncated_response_is_partial_and_counts_remain_explicit(response):
    response["facts"] = response["facts"][:1]
    response["returned_fact_count"] = 1
    response["truncated"] = True

    read = decode(response, limit=1)

    assert read.status == "partial" and read.truncated is True
    assert read.returned_fact_count == 1 and read.total_fact_count == 394


@pytest.mark.parametrize("mutation", [
    lambda data: data["facts"][0].update(concept_qname="{urn:unknown}CashAndCashEquivalents"),
    lambda data: data["facts"][0].update(precision="3"),
    lambda data: data["facts"][0].update(lexical_value="21,27627,043"),
    lambda data: data["facts"][0].update(format_qname=None),
    lambda data: data.update(facts=data["facts"][:1], total_fact_count=1, returned_fact_count=1),
    lambda data: data.update(facts=data["facts"][1:], total_fact_count=393, returned_fact_count=393),
    lambda data: data["report"].update(source_url=data["report"]["source_url"] + "&invalid"),
    lambda data: data["report"].update(document_first_observed_at_utc="2027-01-01T00:00:00Z"),
    lambda data: data["qualification"]["profile_evidence"].update(listed_on="2025-01-01"),
    lambda data: data.update(total_fact_count=395, truncated=True),
])
def test_coordinator_rejects_malformed_contract_instead_of_claiming_complete(response, mutation):
    mutation(response)
    with pytest.raises(TwmdReadError) as exc:
        decode(response)
    assert exc.value.reason_code == "invalid_response"


def test_exact_large_decimal_does_not_use_ambient_precision(response):
    response["facts"][0].update(
        lexical_value="1234567890123456789012345678901234567.890", scale=3,
        value="1234567890123456789012345678901234567890",
    )
    assert decode(response).facts[0].value == "1234567890123456789012345678901234567890"


def test_unsupported_etf_retains_the_catalog_evidence_that_explains_rejection(response):
    response.update(report=None, facts=[], total_fact_count=0, returned_fact_count=0)
    response["qualification"].update(
        status="unsupported", reason="catalog_security_type_not_equity",
        industry_code=None, profile_evidence=None,
    )
    response["qualification"]["catalog_evidence"]["security_type"] = "ETF"
    response["coverage"].update(
        status="MISSING", reason="catalog_security_type_not_equity",
        latest_discovery_presence="missing", capture_id=None, original_received_at_utc=None,
    )
    result = decode(response)
    assert result.status == "unsupported"
    assert result.qualification.catalog_evidence["security_type"] == "ETF"


def test_statement_filter_preserves_comparatives_and_occurrences(response):
    response["statement"] = "comprehensive_income"
    response["facts"] = [fact for fact in response["facts"] if fact["statement"] == "comprehensive_income"]
    response["returned_fact_count"] = response["total_fact_count"] = 86
    read = decode_financial_statement_response(response, instrument_id="TWSE:2330", fiscal_year=2024,
        fiscal_quarter=4, report_scope="consolidated", statement="comprehensive_income", limit=1000)
    assert len(read.facts) == 86 and read.facts[-1].occurrence_ordinal == 85
    assert {fact.context.period.end_date for fact in read.facts} == {"2024-12-31", "2023-12-31"}


def test_nil_zero_and_explicit_dimensions_remain_source_facts(response):
    # Labelled synthetic edge cases on non-mandatory facts in the captured envelope.
    response["facts"][0].update(is_nil=True, value=None, lexical_value="")
    response["facts"][1].update(value="0", lexical_value="0", scale=None, decimals=None)
    response["facts"][1]["context"]["dimensions"] = [{
        "axis_qname": "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}ComponentsOfEquityAxis",
        "member_qname": "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}OrdinaryShareMember",
    }]
    read = decode(response)
    assert read.facts[0].value is None and read.facts[0].is_nil
    assert read.facts[1].value == "0" and read.facts[1].scale is None
    assert read.facts[1].context.dimensions[0].member_qname.endswith("}OrdinaryShareMember")


def test_non_industry_24_qualification_returns_unsupported(response):
    response.update(report=None, facts=[], total_fact_count=0, returned_fact_count=0)
    response["qualification"].update(status="unsupported", reason="profile_industry_not_24", industry_code="26")
    response["qualification"]["profile_evidence"]["industry_code"] = "26"
    response["coverage"].update(status="MISSING", reason="profile_industry_not_24",
        latest_discovery_presence="missing", capture_id=None, original_received_at_utc=None)
    read = decode(response)
    assert read.status == "unsupported" and read.reason == "profile_industry_not_24"


def test_client_does_not_cache_errors_or_share_another_credentials_response(monkeypatch, response):
    calls = []
    def get_response(self, *_args, **_kwargs):
        calls.append(self.config["token"])
        if len(calls) == 1:
            raise TwmdReadError("offline", reason_code="timeout")
        return copy.deepcopy(response), {}
    monkeypatch.setattr(twmd.TwmdClient, "get_response", get_response)
    first = twmd.TwmdClient({"base_url": "http://one", "token": "first"})
    with pytest.raises(TwmdReadError):
        first.financial_statements("TWSE:2330", 2024, 4, today_taipei=date(2026, 10, 7))
    returned = first.financial_statements("TWSE:2330", 2024, 4, today_taipei=date(2026, 10, 7))
    returned.qualification.catalog_evidence["security_type"] = "mutated"
    assert first.financial_statements("TWSE:2330", 2024, 4, today_taipei=date(2026, 10, 7)).qualification.catalog_evidence["security_type"] == "EQUITY"
    second = twmd.TwmdClient({"base_url": "http://one", "token": "second"})
    second.financial_statements("TWSE:2330", 2024, 4, today_taipei=date(2026, 10, 7))
    assert calls == ["first", "first", "second"]


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(instrument_id="TWSE:2317"),
    lambda data: data["facts"][0]["context"].update(entity_identifier="2317"),
    lambda data: data["facts"][0]["context"]["period"].update(instant="2025-03-31"),
    lambda data: data["facts"][0]["unit"].update(numerator=["{urn:invalid}Money"]),
    lambda data: data["facts"][0].update(value="1"),
])
def test_rejects_cross_issuer_scope_period_unit_and_numeric_mismatch(response, mutation):
    mutation(response)
    with pytest.raises(TwmdReadError) as exc:
        decode(response)
    assert exc.value.reason_code == "invalid_response"


def test_client_sends_one_bounded_explicit_read_and_isolates_cache_keys(monkeypatch, response):
    twmd._financial_statements_cache.clear()
    calls = []

    def get_response(self, path, *, timeout_sec=None, retries=None, **params):
        calls.append((self.base_url, self.config.get("token"), path, params))
        return copy.deepcopy(response), {"x-request-id": "safe"}

    monkeypatch.setattr(twmd.TwmdClient, "get_response", get_response)
    first = twmd.TwmdClient({"base_url": "https://one.invalid", "token": "secret-one"})
    second = twmd.TwmdClient({"base_url": "https://two.invalid", "token": "secret-two"})

    first.financial_statements("TWSE:2330", 2024, 4, limit=500, today_taipei=date(2026, 10, 7))
    first.financial_statements("TWSE:2330", 2024, 4, limit=500, today_taipei=date(2026, 10, 7))
    first.financial_statements("TWSE:2330", 2024, 4, limit=400, today_taipei=date(2026, 10, 7))
    second.financial_statements("TWSE:2330", 2024, 4, limit=500, today_taipei=date(2026, 10, 7))

    assert len(calls) == 3
    assert calls[0][2] == "financial-statements"
    assert calls[0][3] == {
        "instrument_id": "TWSE:2330", "fiscal_year": 2024,
        "fiscal_quarter": 4, "report_scope": "consolidated", "limit": 500,
    }
    assert calls[1][3]["limit"] == 400
    assert calls[2][0:2] == ("https://two.invalid", "secret-two")
    assert "secret-one" not in repr(calls[0][3])


def test_tpex_instrument_rejected_before_http(monkeypatch):
    client = twmd.TwmdClient({})
    monkeypatch.setattr(client, "get_response", lambda *args, **kwargs: pytest.fail("unsupported issuer queried"))

    with pytest.raises(ValueError):
        client.financial_statements("TPEX:5347", 2024, 4, today_taipei=date(2026, 10, 7))


def test_incomplete_quarter_rejected_before_http(monkeypatch):
    client = twmd.TwmdClient({})
    monkeypatch.setattr(client, "get_response", lambda *args, **kwargs: pytest.fail("future quarter queried"))

    with pytest.raises(ValueError):
        client.financial_statements("TWSE:2330", 2026, 4, today_taipei=date(2026, 10, 7))
