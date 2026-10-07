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
