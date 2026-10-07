from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from marketdata.vendors.twmd import TwmdClient
from src.modules.administration.api.auth import get_current_user
from src.modules.research import taiwan_research
from src.modules.research.api import taiwan as taiwan_api
from src.modules.research.twmd_profile_revenue import ResearchDataBlock


FIXTURE = Path(__file__).parent.parent / "packages/marketdata/tests/fixtures/twmd/synthetic/material_information.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _catalog():
    return [
        {"instrument_id": "TWSE:2330", "symbol": "2330", "venue": "TWSE", "security_type": "EQUITY", "is_active": True},
        {"instrument_id": "TWSE:2608", "symbol": "2608", "venue": "TWSE", "security_type": "EQUITY", "is_active": True},
        {"instrument_id": "TWSE:00878", "symbol": "00878", "venue": "TWSE", "security_type": "ETF", "is_active": True},
        {"instrument_id": "TPEX:5347", "symbol": "5347", "venue": "TPEX", "security_type": "EQUITY", "is_active": True},
    ]


class FixtureClient(TwmdClient):
    def __init__(self, responses: dict):
        super().__init__({"base_url": "http://offline-material-service"})
        self.responses = responses
        self.calls = []

    def get_response(self, path, **params):
        self.calls.append((path, params))
        if path == "instruments":
            return _catalog(), {}
        source = params["source"]
        return copy.deepcopy(self.responses[source]), {}


def test_research_service_keeps_current_and_history_statuses_distinct_and_bounded():
    responses = _fixture()
    current = copy.deepcopy(responses["current"])
    current["data"] = []
    current["retained_count"] = 0
    current["returned_count"] = 0
    current["coverage_status"] = "MISSING"
    responses["current"] = current
    history = copy.deepcopy(responses["history"])
    history["data"] = []
    history["retained_count"] = 0
    history["returned_count"] = 0
    history["limit"] = 10
    history["truncated"] = False
    history["coverage_status"] = "EMPTY"
    history["history_complete"] = True
    history["missing_dates"] = []
    responses["history"] = history
    client = FixtureClient(responses)
    service = taiwan_research.TaiwanResearchService(
        client=client, config={"base_url": "http://offline-material-service"}
    )
    taiwan_research.clear_taiwan_research_cache()

    current_block = service.material_information(
        "TWSE:2330", start_date="2026-10-07", end_date="2026-10-07",
        source="current", limit=50, today_taipei=date(2026, 10, 7),
    )
    history_block = service.material_information(
        "TWSE:2608", start_date="2024-01-01", end_date="2024-12-31",
        source="history", limit=10, today_taipei=date(2026, 10, 7),
    )

    assert current_block.status == "missing"
    assert current_block.reason == "no_retained_current_observation"
    assert current_block.status != "empty"
    assert history_block.status == "empty"
    assert history_block.evidence["history_complete"] is True
    assert history_block.evidence["query_date_filter"] == {
        "start_date": "2024-01-01", "end_date": "2024-12-31",
    }
    assert history_block.evidence["acquisitions"][0]["query_year"] == 2024
    assert history_block.evidence["acquisitions"][0]["coverage_through"].startswith("2024-12-31")
    assert [call[1]["source"] for call in client.calls if call[0] == "material-information"] == ["current", "history"]


def test_research_preflight_marks_tpex_and_etf_unsupported_without_http_event_reads():
    client = FixtureClient(_fixture())
    service = taiwan_research.TaiwanResearchService(
        client=client, config={"base_url": "http://offline-material-service"}
    )
    taiwan_research.clear_taiwan_research_cache()
    for instrument_id in ("TPEX:5347", "TWSE:00878"):
        block = service.material_information(
            instrument_id, start_date="2026-10-01", end_date="2026-10-07",
            source="current", today_taipei=date(2026, 10, 7),
        )
        assert block.status == "unsupported"
        assert block.data is None
    assert not [call for call in client.calls if call[0] == "material-information"]


def test_research_service_reports_truncation_and_reconstructs_mops_selector_from_provider_key():
    client = FixtureClient(_fixture())
    service = taiwan_research.TaiwanResearchService(
        client=client, config={"base_url": "http://offline-material-service"}
    )
    taiwan_research.clear_taiwan_research_cache()
    block = service.material_information(
        "TWSE:2608", start_date="2024-01-01", end_date="2024-12-31",
        source="history", limit=1, today_taipei=date(2026, 10, 7),
    )
    event = block.data["events"][0]

    assert block.status == "partial"
    assert block.reason == "result_truncated"
    assert event["provider_key"] == "sii:2608:1130312:1"
    assert event["source_reference"] == {
        "label": "MOPS detail request",
        "method": "POST",
        "url": "https://mops.twse.com.tw/mops/api/t05st01_detail",
        "selectors": {
            "serialNumber": "1", "enterDate": "1130312",
            "marketKind": "sii", "companyId": "2608",
        },
        "is_navigable_permalink": False,
    }
    assert event["detail"].endswith("第二行。")


def test_material_information_api_forwards_source_and_bounded_selector(monkeypatch):
    calls = []

    class FakeService:
        def material_information(self, instrument_id, **selectors):
            calls.append((instrument_id, selectors))
            return ResearchDataBlock(
                data={"instrument_id": "TWSE:2608", "source_family": "history", "events": []},
                status="empty", reason="complete_selected_history_window_without_events",
                evidence={"instrument_id": "TWSE:2608", "source_family": "history"},
            )

    monkeypatch.setattr(taiwan_api, "is_market_enabled", lambda market: market == "TW")
    monkeypatch.setattr(taiwan_api, "get_taiwan_research_service", FakeService)
    app = FastAPI()
    app.include_router(taiwan_api.router, prefix="/api/research", dependencies=[Depends(get_current_user)])
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}

    with TestClient(app) as client:
        response = client.get("/api/research/taiwan/material-information", params={
            "instrument_id": "TWSE:2608", "start_date": "2024-01-01", "end_date": "2024-12-31",
            "source": "history", "limit": 100,
        })

    assert response.status_code == 200
    assert response.json()["block"]["status"] == "empty"
    assert calls == [(
        "TWSE:2608", {
            "start_date": "2024-01-01", "end_date": "2024-12-31", "source": "history", "limit": 100,
        },
    )]


def test_material_information_cache_is_defensive_scoped_and_does_not_cache_errors():
    from marketdata.errors import TwmdReadError

    class DynamicClient(FixtureClient):
        fail_next = False

        def get_response(self, path, **params):
            if path == 'instruments':
                return super().get_response(path, **params)
            self.calls.append((path, params))
            if self.fail_next:
                self.fail_next = False
                raise TwmdReadError('storage unavailable', status_code=503, reason_code='http_503')
            body = copy.deepcopy(self.responses[params['source']])
            body.update(start_date=params['start_date'], end_date=params['end_date'], limit=params['limit'])
            return body, {}

    taiwan_research.clear_taiwan_research_cache()
    client = DynamicClient(_fixture())
    config = {'base_url': 'http://offline-cache-material-service', 'token': 'offline-scope-a'}
    service = taiwan_research.TaiwanResearchService(client=client, config=config)
    selectors = dict(start_date='2026-10-07', end_date='2026-10-07', source='current', limit=50,
                     today_taipei=date(2026, 10, 7))
    first = service.material_information('TWSE:2330', **selectors)
    first.data['events'][0]['detail'] = 'consumer mutation'
    again = service.material_information('TWSE:2330', **selectors)
    assert again.data['events'][0]['detail'] != 'consumer mutation'
    assert len([call for call in client.calls if call[0] == 'material-information']) == 1
    service.material_information('TWSE:2330', **{**selectors, 'start_date': '2026-10-06'})
    service.material_information('TWSE:2330', **{**selectors, 'limit': 1})
    assert len([call for call in client.calls if call[0] == 'material-information']) == 3
    second = taiwan_research.TaiwanResearchService(client=client, config={**config, 'token': 'offline-scope-b'})
    assert second.material_information('TWSE:2330', **selectors).status == 'partial'
    assert len([call for call in client.calls if call[0] == 'material-information']) == 4
    other = taiwan_research.TaiwanResearchService(client=client, config={**config, 'base_url': 'http://other-offline-service'})
    client.fail_next = True
    failed = other.material_information('TWSE:2330', **selectors)
    assert failed.status == 'error' and failed.reason == 'http_503'
    assert failed.evidence['http_status'] == 503
    assert other.material_information('TWSE:2330', **selectors).status == 'partial'
    assert len([call for call in client.calls if call[0] == 'material-information']) == 6
    taiwan_research.clear_taiwan_research_cache()


def test_material_information_timeout_holds_the_actual_read_permit(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    gate = threading.Event()
    started = threading.Event()

    class SlowClient(FixtureClient):
        def get_response(self, path, **params):
            if path == 'material-information':
                started.set()
                gate.wait(2)
            return super().get_response(path, **params)

    taiwan_research.clear_taiwan_research_cache()
    client = SlowClient(_fixture())
    service = taiwan_research.TaiwanResearchService(client=client, config={'base_url': 'http://slow-offline-material-service'})
    selectors = dict(start_date='2026-10-07', end_date='2026-10-07', source='current', limit=50,
                     today_taipei=date(2026, 10, 7))
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(taiwan_research, '_READ_POOL', pool)
        monkeypatch.setattr(taiwan_research, '_READ_SLOTS', threading.BoundedSemaphore(1))
        monkeypatch.setattr(taiwan_research, '_REQUEST_DEADLINE_SECONDS', 0.05)
        try:
            timed_out = service.material_information('TWSE:2330', **selectors)
            assert started.is_set()
            assert timed_out.reason == 'timeout'
            assert service.material_information('TWSE:2330', **selectors).reason == 'concurrency_limit'
        finally:
            gate.set()
    assert len([call for call in client.calls if call[0] == 'material-information']) == 1
    taiwan_research.clear_taiwan_research_cache()


def test_material_information_api_requires_auth_and_enabled_market_and_masks_unexpected_errors(monkeypatch):
    calls = []
    enabled = {'value': True}

    class FailingService:
        def material_information(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise RuntimeError('private provider configuration')

    monkeypatch.setattr(taiwan_api, 'is_market_enabled', lambda _: enabled['value'])
    monkeypatch.setattr(taiwan_api, 'get_taiwan_research_service', FailingService)
    app = FastAPI()
    app.include_router(taiwan_api.router, prefix='/api/research', dependencies=[Depends(get_current_user)])
    params = dict(instrument_id='TWSE:2608', start_date='2024-01-01', end_date='2024-12-31', source='history')
    with TestClient(app) as client:
        assert client.get('/api/research/taiwan/material-information', params=params).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {'id': 1}
        enabled['value'] = False
        assert client.get('/api/research/taiwan/material-information', params=params).status_code == 404
        assert not calls
        enabled['value'] = True
        assert client.get('/api/research/taiwan/material-information', params={**params, 'source': 'both'}).status_code == 422
        assert client.get('/api/research/taiwan/material-information', params={**params, 'limit': 0}).status_code == 422
        assert not calls
        error = client.get('/api/research/taiwan/material-information', params=params)
        assert error.status_code == 503
        assert 'private' not in error.text
