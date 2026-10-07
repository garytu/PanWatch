from datetime import date, datetime, timezone
from types import SimpleNamespace

from src.modules.research.taiwan_discovery import (
    TaiwanDiscoveryService,
    clear_taiwan_discovery_cache,
)


class FakeOfficialClient:
    def __init__(self, *, valuation=None, revenues=None, flows=None):
        self.base_url = "https://official.example.test/api/v1?credential=private"
        self.config = {"base_url": self.base_url, "token": "test-token"}
        self.valuation = valuation or {}
        self.revenues = revenues or {}
        self.flows = flows or {}
        self.calls = []

    def valuation_history(self, instrument_id, start, end, *, today_taipei):
        self.calls.append(("valuation", instrument_id, start, end))
        return self.valuation[instrument_id]

    def monthly_revenues(self, instrument_id, start, end, *, today_taipei):
        self.calls.append(("monthly_revenue", instrument_id, start, end))
        return self.revenues[instrument_id]

    def institutional_flows(self, instrument_id, start, end, *, today_taipei):
        self.calls.append(("institutional_flows", instrument_id, start, end))
        return self.flows[instrument_id]


class FakeMarketData:
    def __init__(self, pool, client):
        self.pool = pool
        self.client = client

    def _twmd_research_client(self, _dataset):
        return self.client

    def taiwan_discovery_pool(self, **_kwargs):
        return self.pool


def _read_fixture(instrument_id, *, trade_date="2026-10-04", pe="10", pb="1.2", yield_pct="4"):
    row = SimpleNamespace(
        instrument_id=instrument_id,
        trade_date=trade_date,
        pe_ratio=pe,
        pb_ratio=pb,
        dividend_yield_pct=yield_pct,
        dividend_reference_year=2025,
        financial_reference_year=2025,
        financial_reference_quarter=4,
        source_contract="valuation-v1",
        source_url="https://official.example.test/valuations",
        request_scope="one instrument",
        received_at_utc="2026-10-05T01:00:00Z",
        payload_sha256="abc123",
        capture_id="capture-v1",
        revision=2,
    )
    return SimpleNamespace(
        instrument_id=instrument_id,
        endpoint="/api/v1/valuations",
        start_date="2026-09-07",
        end_date="2026-10-06",
        data=[row],
        status="available",
        reason="selected_record_present",
        schema_ready=True,
        coverage_header="available=30;missing=0;selected=present",
        selected_instrument_presence="present",
    )


def _revenue_read(instrument_id, rows):
    return SimpleNamespace(
        instrument_id=instrument_id,
        endpoint="/api/v1/monthly-revenues",
        dataset="monthly_revenue",
        schema_ready=True,
        coverage_status="AVAILABLE",
        qualification="qualified",
        qualification_reason="in_catalog",
        current_catalog_evidence={"source_contract": "catalog-v1"},
        units={"year_over_year_pct": "percent"},
        coverage=[],
        months=[SimpleNamespace(presence="present", row=row, data_month=row.data_month) for row in rows],
        status="available",
        reason="selected_record_present",
    )


def _revenue_row(month, yoy):
    return SimpleNamespace(
        data_month=month,
        year_over_year_pct=yoy,
        source_contract="monthly-revenue-v1",
        source_url="https://official.example.test/monthly-revenues",
        acquisition_date="2026-09-10",
        received_at_utc="2026-09-10T02:00:00Z",
        report_date="2026-09-09",
        capture_id=f"revenue-{month}",
        revision=3,
        payload_sha256="def456",
        request_scope="one month",
        content_hash="content-v1",
        source="twse",
    )


def _flow_read(instrument_id, trade_date="2026-10-03", net=1250):
    row = SimpleNamespace(
        instrument_id=instrument_id,
        trade_date=trade_date,
        native_values={"total_institutional_net_shares": net},
        native_unit="shares",
        source_contract="flow-v1",
        source_url="https://official.example.test/institutional-flows",
        request_scope="one instrument",
        acquired_at="2026-10-04T01:00:00Z",
        first_observed_at="2026-10-04T01:01:00Z",
        received_at_utc="2026-10-04T01:02:00Z",
        payload_sha256="ghi789",
        capture_id="flow-capture-v1",
        revision=1,
    )
    coverage = SimpleNamespace(
        trade_date=trade_date,
        status="AVAILABLE",
        record_count=1,
        selected_instrument_presence="present",
        acquired_at="2026-10-04T01:00:00Z",
        received_at_utc="2026-10-04T01:02:00Z",
        sha256="ghi789",
        capture_id="flow-capture-v1",
        source_contract="flow-v1",
        source_url="https://official.example.test/institutional-flows",
        request_scope="one instrument",
        payload_sha256="ghi789",
    )
    return SimpleNamespace(
        instrument_id=instrument_id,
        endpoint="/api/v1/institutional-flows",
        source_contract="flow-v1",
        native_unit="shares",
        schema_ready=True,
        coverage=[coverage],
        data=[row],
        status="available",
        reason="selected_record_present",
        request_scope="one instrument",
    )


def _pool(*identities, turnovers=None, security_types=None):
    from marketdata.types import TaiwanDiscoveryPool

    turnovers = turnovers or {identity: 100 for identity in identities}
    security_types = security_types or {identity: "EQUITY" for identity in identities}
    items = [
        SimpleNamespace(
            symbol=identity,
            name=f"名稱 {identity}",
            turnover=turnovers[identity],
            change_pct=1.0,
            trade_date="2026-10-06",
        )
        for identity in identities
    ]
    return TaiwanDiscoveryPool(
        items=items,
        status="available",
        catalog_count=len(identities),
        eligible_catalog_count=len(identities),
        scanned_instrument_count=len(identities),
        price_snapshot_count=len(identities),
        catalog_request_count=1,
        price_snapshot_request_count=1,
        price_universe_selected_count=len(identities),
        price_snapshot_batches_planned=1,
        ranked_price_count=len(identities),
        security_type_by_instrument_id=security_types,
        price_data_dates=["2026-10-06"],
    )


def _run(pool, client, conditions):
    clear_taiwan_discovery_cache()
    market_data = FakeMarketData(pool, client)
    return TaiwanDiscoveryService(market_data=market_data).collect(
        conditions,
        today_taipei=date(2026, 10, 7),
        now_utc=datetime(2026, 10, 6, 16, tzinfo=timezone.utc),
    )


def test_official_filter_preserves_dates_conditions_scope_and_source_evidence():
    identity = "TWSE:2330"
    client = FakeOfficialClient(
        valuation={identity: _read_fixture(identity)},
        revenues={identity: _revenue_read(identity, [_revenue_row("2026-08-01", "15.5")])},
        flows={identity: _flow_read(identity)},
    )
    result = _run(
        _pool(identity),
        client,
        {
            "pe_max": 20,
            "pb_max": 2,
            "dividend_yield_min_pct": 3,
            "revenue_yoy_min_pct": 10,
            "institutional_net_min_shares": 1000,
        },
    )

    assert result["conditions"]["pe_max"]["threshold"] == "20"
    assert result["selectors"]["daily_end_date"] == "2026-10-06"
    assert result["selectors"]["revenue_end_month"] == "2026-09"
    assert result["scope"]["price_data_dates"] == ["2026-10-06"]
    assert result["scope"]["candidates_examined"] == 1
    assert result["scope"]["request_counts"]["total"] == 5
    assert [call[0] for call in client.calls] == ["institutional_flows", "monthly_revenue", "valuation"]
    assert [item["instrument_id"] for item in result["matches"]] == [identity]
    candidate = result["matches"][0]
    assert all(item["passed"] for item in candidate["condition_results"].values())
    assert candidate["condition_results"]["institutional_net_min_shares"]["data_date"] == "2026-10-03"
    assert candidate["data_evidence"]["valuation"]["source_contract"] == "valuation-v1"
    assert candidate["data_evidence"]["valuation"]["capture_id"] == "capture-v1"
    assert candidate["data_evidence"]["valuation"]["dividend_reference_year"] == 2025
    assert "not a current annualized yield inference" in candidate["data_evidence"]["valuation"]["dividend_yield_interpretation"]
    assert candidate["data_evidence"]["monthly_revenue"]["revision"] == 3
    assert candidate["data_evidence"]["institutional_flows"]["first_observed_at"] == "2026-10-04T01:01:00Z"
    assert "credential" not in str(candidate["data_evidence"])


def test_latest_month_without_yoy_is_not_backfilled_from_older_month():
    identity = "TWSE:2330"
    client = FakeOfficialClient(
        revenues={identity: _revenue_read(identity, [
            _revenue_row("2026-07-01", "50"),
            _revenue_row("2026-08-01", None),
        ])},
    )
    result = _run(_pool(identity), client, {"revenue_yoy_min_pct": 1})
    row = result["excluded"][0]
    assert row["data_dates"]["monthly_revenue"] == "2026-08"
    assert row["condition_results"]["revenue_yoy_min_pct"]["reason"] == "value_missing"
    assert row["values"]["revenue_yoy_pct"] is None


def test_etf_revenue_is_explicitly_unsupported_without_reading_it():
    identity = "TWSE:00878"
    client = FakeOfficialClient()
    result = _run(
        _pool(identity, security_types={identity: "ETF"}),
        client,
        {"revenue_yoy_min_pct": 1},
    )
    row = result["excluded"][0]
    assert row["condition_results"]["revenue_yoy_min_pct"]["reason"] == "unsupported_etf"
    assert not client.calls
    assert row["data_evidence"]["monthly_revenue"]["presence"] == "unknown"


def test_stale_daily_and_missing_flow_values_are_excluded_without_zero_fill():
    identity = "TWSE:2330"
    missing_flow = SimpleNamespace(
        instrument_id=identity,
        endpoint="/api/v1/institutional-flows",
        source_contract="flow-v1",
        native_unit="shares",
        schema_ready=True,
        coverage=[],
        data=[],
        status="missing",
        reason="coverage_missing",
        request_scope="one instrument",
    )
    client = FakeOfficialClient(
        valuation={identity: _read_fixture(identity, trade_date="2026-09-29")},
        flows={identity: missing_flow},
    )
    result = _run(_pool(identity), client, {"pe_max": 20, "institutional_net_min_shares": 0})
    row = result["excluded"][0]
    assert row["condition_results"]["pe_max"]["reason"] == "stale"
    assert row["condition_results"]["institutional_net_min_shares"]["reason"] == "coverage_missing"
    assert row["values"]["institutional_net_shares"] is None
    assert row["exclusion_reasons"] == ["stale", "coverage_missing"]


def test_revenue_freshness_uses_calendar_month_end_boundary():
    condition = TaiwanDiscoveryService._condition_result
    row = SimpleNamespace(data_month="2026-07-01", year_over_year_pct="5")
    service = TaiwanDiscoveryService.__new__(TaiwanDiscoveryService)
    exact_boundary = condition(
        service, "revenue_yoy_min_pct", "0", row, None,
        SimpleNamespace(), date(2026, 10, 29),
    )
    stale_next_day = condition(
        service, "revenue_yoy_min_pct", "0", row, None,
        SimpleNamespace(), date(2026, 10, 30),
    )
    assert exact_boundary["passed"] is True
    assert stale_next_day["reason"] == "stale"


def test_stable_candidate_tie_order_uses_canonical_instrument_id():
    ids = ["TPEX:6488", "TWSE:2330"]
    client = FakeOfficialClient(valuation={identity: _read_fixture(identity) for identity in ids})
    result = _run(
        _pool(*ids, turnovers={identity: 100 for identity in ids}),
        client,
        {"pe_max": 20},
    )
    assert [item["instrument_id"] for item in result["matches"]] == ["TPEX:6488", "TWSE:2330"]


def test_expired_request_deadline_reports_unread_candidates_and_starts_no_factor_reads(monkeypatch):
    from src.modules.research import taiwan_discovery

    identity = "TWSE:2330"
    client = FakeOfficialClient()
    monkeypatch.setattr(taiwan_discovery, "_REQUEST_DEADLINE_SECONDS", 0)
    result = _run(_pool(identity), client, {"pe_max": 20})
    row = result["excluded"][0]
    assert result["scope"]["partial_scan"] is True
    assert result["scope"]["candidates_timed_out"] == 1
    assert row["exclusion_reasons"] == ["screen_deadline_exceeded"]
    assert row["condition_results"]["pe_max"]["reason"] == "screen_deadline_exceeded"
    assert client.calls == []


def test_busy_screen_returns_partial_checkpoint_without_requesting_prices(monkeypatch):
    from src.modules.research import taiwan_discovery

    class Busy:
        def acquire(self, **_kwargs):
            return False

    monkeypatch.setattr(taiwan_discovery, '_SCREEN_REQUEST_SLOTS', Busy())
    result = _run(_pool('TWSE:2330'), FakeOfficialClient(), {'pe_max': 20})
    assert result['scope']['scan_status'] == 'discovery_concurrency_limit'
    assert result['scope']['partial_scan'] is True
    assert result['scope']['request_counts']['total'] == 0
    assert result['scope']['evaluated_at_utc'] == '2026-10-06T16:00:00Z'
    assert result['scope']['limits']['concurrent_factor_reads'] == 4


def test_price_provider_failure_keeps_recoverable_partial_checkpoint():
    class BrokenPrices(FakeMarketData):
        def taiwan_discovery_pool(self, **_kwargs):
            raise TimeoutError()

    clear_taiwan_discovery_cache()
    result = TaiwanDiscoveryService(market_data=BrokenPrices(None, FakeOfficialClient())).collect(
        {'pe_max': 20}, now_utc=datetime(2026, 10, 6, 16, tzinfo=timezone.utc),
    )
    assert result['scope']['scan_status'] == 'timeout'
    assert result['scope']['partial_scan'] is True
    assert result['scope']['request_counts']['failed'] == 1


def test_bounded_candidate_reads_and_cache_isolate_same_code_across_venues():
    identities = ['TPEX:2330', 'TWSE:2330'] + [f'TWSE:{1000 + i}' for i in range(19)]
    client = FakeOfficialClient(valuation={identity: _read_fixture(identity) for identity in identities})
    clear_taiwan_discovery_cache()
    service = TaiwanDiscoveryService(market_data=FakeMarketData(_pool(*identities), client))
    first = service.collect({'pe_max': 20}, now_utc=datetime(2026, 10, 6, 16, tzinfo=timezone.utc))
    second = service.collect({'pe_max': 15}, now_utc=datetime(2026, 10, 6, 17, tzinfo=timezone.utc))
    assert len(client.calls) == 20
    assert first['scope']['candidates_selected'] == 20
    assert first['scope']['partial_reasons'] == ['candidate_cap_reached']
    assert {'TPEX:2330', 'TWSE:2330'}.issubset({item['instrument_id'] for item in second['matches']})
    assert second['scope']['request_counts']['valuation'] == 0
    assert second['scope']['request_counts']['cache_hits'] == 20
    assert second['conditions']['pe_max']['threshold'] == '15'


def test_partial_provider_failure_preserves_other_candidates():
    ids = ['TWSE:2330', 'TPEX:2330']
    client = FakeOfficialClient(valuation={'TWSE:2330': _read_fixture('TWSE:2330')})
    result = _run(_pool(*ids), client, {'pe_max': 20})
    assert [item['instrument_id'] for item in result['matches']] == ['TWSE:2330']
    assert result['excluded'][0]['instrument_id'] == 'TPEX:2330'
    assert result['scope']['partial_scan'] is True
    assert 'candidate_data_reads_failed' in result['scope']['partial_reasons']
    assert result['scope']['request_counts']['failed'] == 1


def test_unsupported_tpex_etf_valuation_does_not_call_issuer_selector():
    identity = 'TPEX:006201'
    client = FakeOfficialClient()
    result = _run(_pool(identity, security_types={identity: 'ETF'}), client, {'pb_max': 2})
    assert client.calls == []
    assert result['excluded'][0]['exclusion_reasons'] == ['unsupported_valuation_selector']


def test_running_reads_hold_request_permit_after_response_timeout(monkeypatch):
    import threading
    from src.modules.research import taiwan_discovery

    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    permit = threading.BoundedSemaphore(1)

    class SlowClient(FakeOfficialClient):
        def valuation_history(self, *args, **kwargs):
            entered.set()
            try:
                assert release.wait(2)
                return super().valuation_history(*args, **kwargs)
            finally:
                stopped.set()

    monkeypatch.setattr(taiwan_discovery, '_SCREEN_REQUEST_SLOTS', permit)
    monkeypatch.setattr(taiwan_discovery, '_REQUEST_DEADLINE_SECONDS', 0.05)
    client = SlowClient(valuation={'TWSE:2330': _read_fixture('TWSE:2330')})
    try:
        result = _run(_pool('TWSE:2330'), client, {'pe_max': 20})
        assert entered.is_set()
        assert result['scope']['candidates_timed_out'] == 1
        assert result['scope']['request_counts']['valuation'] == 1
        assert not permit.acquire(blocking=False)
        release.set()
        assert stopped.wait(2)
        assert permit.acquire(timeout=2)
        permit.release()
    finally:
        release.set()
        stopped.wait(2)


def test_service_enforces_filter_bounds_before_any_source_reads():
    import pytest

    invalid_conditions = [
        {'pe_max': 0}, {'pb_max': 1001}, {'dividend_yield_min_pct': -1},
        {'revenue_yoy_min_pct': -1001}, {'institutional_net_min_shares': 10_000_000_001},
        {'pe_max': True}, {'institutional_net_min_shares': 1.5}, {'consecutive_days': 3},
    ]
    for conditions in invalid_conditions:
        with pytest.raises(ValueError):
            _run(_pool('TWSE:2330'), FakeOfficialClient(), conditions)


def test_cached_month_rechecks_freshness_on_each_request():
    identity = 'TWSE:2330'
    client = FakeOfficialClient(revenues={identity: _revenue_read(identity, [_revenue_row('2026-07-01', '5')])})
    clear_taiwan_discovery_cache()
    service = TaiwanDiscoveryService(market_data=FakeMarketData(_pool(identity), client))
    first = service.collect({'revenue_yoy_min_pct': 0}, now_utc=datetime(2026, 10, 29, tzinfo=timezone.utc))
    second = service.collect({'revenue_yoy_min_pct': 0}, now_utc=datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert len(first['matches']) == 1
    assert second['matches'] == []
    assert second['excluded'][0]['exclusion_reasons'] == ['stale']
    assert second['scope']['request_counts']['monthly_revenue'] == 0
    assert second['scope']['request_counts']['cache_hits'] == 1
    assert len(client.calls) == 1


def test_native_typed_cache_is_distinct_from_transport_attempts(monkeypatch):
    from marketdata.vendors.twmd import TwmdClient

    identity = 'TWSE:2330'
    read = _revenue_read(identity, [_revenue_row('2026-08-01', '10')])
    read.served_at = '2026-10-05T01:00:00Z'
    monkeypatch.setattr(TwmdClient, 'monthly_revenues', lambda *_args, **_kwargs: read)
    client = TwmdClient({'base_url': 'https://example.test'})
    result = _run(_pool(identity), client, {'revenue_yoy_min_pct': 0})
    assert result['scope']['request_counts']['monthly_revenue'] == 1
    assert result['scope']['request_counts']['http_attempts']['monthly_revenue'] == 0
    assert result['scope']['request_counts']['cache_hits'] == 1
    assert result['matches'][0]['data_evidence']['monthly_revenue']['source_served_at'] == read.served_at

    def uncached_read(self, *_args, **_kwargs):
        self.get_response('monthly-revenues')
        return read

    monkeypatch.setattr(TwmdClient, 'monthly_revenues', uncached_read)
    monkeypatch.setattr(TwmdClient, 'get_response', lambda *_args, **_kwargs: ({}, {}))
    result = _run(_pool(identity), client, {'revenue_yoy_min_pct': 0})
    assert result['scope']['request_counts']['http_attempts']['monthly_revenue'] == 1
    assert result['scope']['request_counts']['cache_hits'] == 0


def test_explicitly_unconfigured_factor_source_is_partial_and_never_replaced():
    class Unconfigured(FakeMarketData):
        def _twmd_research_client(self, _dataset):
            raise ValueError('explicitly configured alternative provider')

    clear_taiwan_discovery_cache()
    result = TaiwanDiscoveryService(market_data=Unconfigured(_pool('TWSE:2330'), None)).collect(
        {'pe_max': 20}, now_utc=datetime(2026, 10, 6, 16, tzinfo=timezone.utc),
    )
    assert result['scope']['partial_scan'] is True
    assert result['scope']['source_status'] == {'valuation': 'official_source_not_configured'}
    assert result['scope']['request_counts']['valuation'] == 0
    assert result['excluded'][0]['exclusion_reasons'] == ['official_source_not_configured']
