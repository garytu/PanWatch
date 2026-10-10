from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.modules.administration.api.auth import get_current_user
from src.modules.research.api import taiwan


def _app():
    app = FastAPI()
    app.include_router(
        taiwan.router,
        prefix="/api/research",
        dependencies=[Depends(get_current_user)],
    )
    return app


def _period_index_read():
    from marketdata.types import (
        TwmdFinancialStatementPeriodAuthority,
        TwmdFinancialStatementPeriodIndexCoverage,
        TwmdFinancialStatementPeriodIndexEntry,
        TwmdFinancialStatementPeriodQualification,
        TwmdFinancialStatementPeriodStatementCoverage,
        TwmdFinancialStatementPeriodsRead,
    )

    authority = TwmdFinancialStatementPeriodAuthority(
        capture_id="capture-2024q4", document_id="document-2024q4",
        semantic_revision_id="revision-2024q4", revision_number=2,
        source_contract="mops.financial-statements/v1", parser_contract="mops.parser/v1",
        original_received_at_utc="2025-03-15T01:00:00Z",
        document_first_observed_at_utc="2025-03-15T01:00:00Z",
        semantic_revision_first_observed_at_utc="2025-03-15T01:00:00Z",
        latest_observed_at_utc="2025-03-16T01:00:00Z",
    )
    readable = TwmdFinancialStatementPeriodIndexEntry(
        2024, 4, "consolidated", "present_readable", "validated_retained_report",
        {
            name: TwmdFinancialStatementPeriodStatementCoverage("present_readable", count)
            for name, count in {
                "balance_sheet": 154, "comprehensive_income": 86, "cash_flows": 154,
            }.items()
        },
        authority,
    )
    missing = TwmdFinancialStatementPeriodIndexEntry(
        2026, 3, "consolidated", "missing", "no_retained_report", {}, None,
    )
    return TwmdFinancialStatementPeriodsRead(
        contract_version="twmd.financial-statement-periods/v1",
        instrument_id="TWSE:2330", venue="TWSE", source="mops_financial_statements",
        report_scope="consolidated", statement=None, limit=1,
        supported_scope={
            "venues": ["TWSE"], "source": "mops_financial_statements",
            "source_contract": "mops.financial-statements/v1", "industry_codes": ["24"],
            "security_types": ["EQUITY"], "report_scopes": ["consolidated"],
            "statements": ["balance_sheet", "comprehensive_income", "cash_flows"],
        },
        window_start=(2024, 1), window_end=(2026, 3),
        qualification=TwmdFinancialStatementPeriodQualification(
            "qualified", "twse_equity_industry_24", "24",
            {"instrument_id": "TWSE:2330", "venue": "TWSE", "security_type": "EQUITY", "is_active": True},
            {"instrument_id": "TWSE:2330", "industry_code": "24", "listed_on": "1994-09-05"},
        ),
        coverage=TwmdFinancialStatementPeriodIndexCoverage("complete", "retained_metadata_complete"),
        periods=(missing,), next_cursor="opaque-next", has_more=True,
        latest_retained_period=readable, latest_readable_period=readable,
        served_at_utc="2026-10-10T02:00:00Z",
    )


def test_taiwan_research_api_is_protected_and_forwards_bounded_selectors(monkeypatch):
    calls = []
    payload = {
        "instrument_id": "TPEX:5347",
        "instrument": {"venue": "TPEX", "symbol": "5347", "security_type": "EQUITY"},
        "selectors": {
            "start_date": "2026-10-02", "end_date": "2026-10-06",
            "start_month": "2026-07", "end_month": "2026-08",
        },
        "blocks": {"valuation": {"data": None, "status": "missing", "reason": "coverage_missing", "evidence": {}}},
    }

    class FakeService:
        def collect(self, instrument_id, **selectors):
            calls.append((instrument_id, selectors))
            return payload

    monkeypatch.setattr(taiwan, "is_market_enabled", lambda market: market == "TW")
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", FakeService)
    app = _app()
    with TestClient(app) as client:
        assert client.get("/api/research/taiwan?instrument_id=TPEX%3A5347").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {"id": 1}
        response = client.get(
            "/api/research/taiwan",
            params={
                "instrument_id": "TPEX:5347",
                "start_date": "2026-10-02",
                "end_date": "2026-10-06",
                "start_month": "2026-07",
                "end_month": "2026-08",
                "fiscal_year": 2024,
                "fiscal_quarter": 4,
                "statement": "cash_flows",
            },
        )

    assert response.status_code == 200
    assert response.json() == payload
    assert calls == [(
        "TPEX:5347",
        {
            "start_date": "2026-10-02", "end_date": "2026-10-06",
            "start_month": "2026-07", "end_month": "2026-08",
            "fiscal_year": 2024, "fiscal_quarter": 4, "statement": "cash_flows",
        },
    )]


def test_taiwan_research_api_rejects_bad_bounds_and_disabled_market(monkeypatch):
    calls = []

    class FakeService:
        def collect(self, instrument_id, **selectors):
            calls.append((instrument_id, selectors))
            raise ValueError("end_date must be before current date")

    enabled = {"value": True}
    monkeypatch.setattr(taiwan, "is_market_enabled", lambda _market: enabled["value"])
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", FakeService)
    app = _app()
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}

    with TestClient(app) as client:
        invalid = client.get(
            "/api/research/taiwan",
            params={"instrument_id": "TWSE:2330", "end_date": "2026-10-07"},
        )
        enabled["value"] = False
        disabled = client.get("/api/research/taiwan?instrument_id=TWSE%3A2330")

    assert invalid.status_code == 422
    assert len(calls) == 1
    assert disabled.status_code == 404


def test_taiwan_research_api_accepts_repeated_allowlisted_block_selectors(monkeypatch):
    calls = []

    class FakeService:
        def collect(self, instrument_id, **selectors):
            calls.append((instrument_id, selectors))
            requested = list(selectors["blocks"])
            return {
                "instrument_id": instrument_id,
                "requested_blocks": requested,
                "blocks": {name: {"data": None, "status": "error", "reason": "timeout", "evidence": {}} for name in requested},
            }

    monkeypatch.setattr(taiwan, "is_market_enabled", lambda _market: True)
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", FakeService)
    app = _app()
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    with TestClient(app) as client:
        response = client.get(
            "/api/research/taiwan",
            params=[
                ("instrument_id", "TWSE:2330"),
                ("blocks", "valuation"),
                ("blocks", "monthly_revenues"),
            ],
        )
        invalid = client.get(
            "/api/research/taiwan",
            params=[("instrument_id", "TWSE:2330"), ("blocks", "valuation"), ("blocks", "private_block")],
        )

    assert response.status_code == 200
    assert response.json()["requested_blocks"] == ["valuation", "monthly_revenues"]
    assert set(response.json()["blocks"]) == {"valuation", "monthly_revenues"}
    assert calls == [("TWSE:2330", {
        "start_date": None, "end_date": None, "start_month": None, "end_month": None,
        "fiscal_year": None, "fiscal_quarter": None, "statement": None,
        "blocks": ("valuation", "monthly_revenues"),
    })]
    assert invalid.status_code == 422


def test_financial_period_index_api_protects_bounded_read_and_preserves_latest_outside_page(monkeypatch):
    calls = []

    class Service:
        def financial_statement_periods(self, instrument_id, **selectors):
            calls.append((instrument_id, selectors))
            return _period_index_read()

    monkeypatch.setattr(taiwan, "is_market_enabled", lambda _market: True)
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", Service)
    app = _app()
    with TestClient(app) as client:
        params = {"instrument_id": "TWSE:2330", "report_scope": "consolidated", "limit": 1}
        assert client.get("/api/research/taiwan/financial-periods", params=params).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {"id": 1}
        response = client.get("/api/research/taiwan/financial-periods", params=params)
        unsupported = client.get(
            "/api/research/taiwan/financial-periods",
            params={**params, "private_selector": "must-not-be-ignored"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["index_status"] == "available"
    assert payload["index"]["periods"][0]["fiscal_year"] == 2026
    assert payload["index"]["latest_readable_period"]["fiscal_year"] == 2024
    assert payload["index"]["latest_readable_period"]["authority"]["semantic_revision_id"] == "revision-2024q4"
    assert payload["selectors"] == {
        "instrument_id": "TWSE:2330", "report_scope": "consolidated",
        "statement": None, "limit": 1, "venue": "TWSE", "source": "mops_financial_statements",
    }
    assert calls == [("TWSE:2330", {
        "report_scope": "consolidated", "statement": None, "limit": 1, "cursor": None,
    })]
    assert unsupported.status_code == 422


def test_financial_period_index_old_endpoint_and_duplicate_selectors_are_distinct(monkeypatch):
    from marketdata.errors import TwmdReadError

    class Service:
        def financial_statement_periods(self, _instrument_id, **_selectors):
            raise TwmdReadError("endpoint missing", status_code=404, reason_code="http_404")

    monkeypatch.setattr(taiwan, "is_market_enabled", lambda _market: True)
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", Service)
    app = _app()
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    with TestClient(app) as client:
        old = client.get(
            "/api/research/taiwan/financial-periods?instrument_id=TWSE%3A2330&limit=1"
        )
        duplicate = client.get(
            "/api/research/taiwan/financial-periods?instrument_id=TWSE%3A2330&limit=1&limit=2"
        )

    assert old.status_code == 200
    assert old.json()["index_status"] == "unknown"
    assert old.json()["error"]["code"] == "endpoint_unsupported"
    assert duplicate.status_code == 422


def test_financial_period_index_classifies_known_out_of_scope_instrument(monkeypatch):
    from marketdata.errors import TwmdReadError

    class Service:
        def financial_statement_periods(self, _instrument_id, **_selectors):
            raise TwmdReadError("unsupported venue", status_code=422, reason_code="unsupported_venue")

    monkeypatch.setattr(taiwan, "is_market_enabled", lambda _market: True)
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", Service)
    app = _app()
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    with TestClient(app) as client:
        response = client.get(
            "/api/research/taiwan/financial-periods?instrument_id=TPEX%3A5347"
        )

    assert response.status_code == 200
    assert response.json()["index_status"] == "unsupported"
    assert response.json()["reason"] == "unsupported_venue"


def test_corporate_action_route_protects_reads_and_retains_unknown_coverage(monkeypatch):
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock
    calls = []
    mode = {"value": "success"}
    class Service:
        def corporate_actions(self, instrument_id, **selectors):
            calls.append((instrument_id, selectors))
            if mode["value"] == "bad_bounds":
                raise ValueError("invalid range")
            if mode["value"] == "error":
                raise RuntimeError("private credentials must never be returned")
            return ResearchDataBlock({"instrument_id": instrument_id, "known_event_dates": []}, "unknown", "coverage_not_returned", {"instrument_id": instrument_id, "dataset_coverage": "unknown"})
    monkeypatch.setattr(taiwan, "get_taiwan_research_service", Service)
    enabled = {"value": True}
    monkeypatch.setattr(taiwan, "is_market_enabled", lambda _market: enabled["value"])
    app = _app()
    params = {"instrument_id": "TWSE:123A", "start_date": "2024-06-25", "end_date": "2024-07-01"}
    with TestClient(app) as client:
        assert client.get("/api/research/taiwan/corporate-actions", params=params).status_code == 401
        assert not calls
        app.dependency_overrides[get_current_user] = lambda: {"id": 1}
        response = client.get("/api/research/taiwan/corporate-actions", params=params)
        assert response.status_code == 200
        assert response.json()["block"]["evidence"]["dataset_coverage"] == "unknown"
        assert calls == [("TWSE:123A", {"start_date": "2024-06-25", "end_date": "2024-07-01"})]
        mode["value"] = "bad_bounds"
        assert client.get("/api/research/taiwan/corporate-actions", params=params).status_code == 422
        mode["value"] = "error"
        error = client.get("/api/research/taiwan/corporate-actions", params=params)
        assert error.status_code == 503 and "private credentials" not in error.text
        enabled["value"] = False
        assert client.get("/api/research/taiwan/corporate-actions", params=params).status_code == 404
