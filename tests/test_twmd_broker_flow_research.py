from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdBrokerFlowCoverageObservation,
    TwmdBrokerFlowCoverageRead,
    TwmdBrokerFlowPriceLevelObservation,
    TwmdBrokerFlowPriceLevelsRead,
    TwmdBrokerFlowQuantityObservation,
    TwmdBrokerFlowQuantityRead,
)
from src.modules.research.twmd_broker_flow import broker_flow_block


def _quantity(provider, trade_date, branch_key, branch_code, unit, precision, buy, sell, revision):
    return TwmdBrokerFlowQuantityObservation(
        provider=provider, dataset="broker_flow", instrument_id="TWSE:2330", symbol="2330",
        trade_date=trade_date, source_branch_key=branch_key, branch_code=branch_code,
        branch_name=f"{provider} branch", native_unit=unit, precision_shares=precision,
        buy_native=buy, sell_native=sell, net_native=buy - sell,
        buy_vwap=Decimal("100.123456789012345678") if provider == "twse" else None,
        sell_vwap=None, revision_id=revision,
    )


def _coverage(provider, day, status, count, revision=None, failure=None):
    return TwmdBrokerFlowCoverageObservation(
        provider=provider, dataset="broker_flow", instrument_id="TWSE:2330", trade_date=day,
        status=status, record_count=count, revision_id=revision, failure_reason=failure,
    )


def _client(quantities, coverage, prices, *, fail_quantity=False):
    class Client:
        config = {"timeout_sec": 5}

        def broker_flow_quantities(self, *_args, **_kwargs):
            if fail_quantity:
                raise TwmdReadError("private endpoint details", status_code=503, reason_code="http_503")
            return TwmdBrokerFlowQuantityRead(
                "TWSE:2330", "/api/v1/broker-flow/quantities", "2026-07-23", "2026-07-24",
                quantities, "available" if quantities else "unknown", "selected_records_present" if quantities else "quantity_rows_not_returned",
            )

        def broker_flow_coverage(self, *_args, **_kwargs):
            statuses = {row.status for row in coverage}
            status = "available" if statuses == {"AVAILABLE"} else "partial"
            return TwmdBrokerFlowCoverageRead(
                "TWSE:2330", "/api/v1/broker-flow/coverage", "2026-07-23", "2026-07-24",
                coverage, status, "source_records_available" if status == "available" else "some_requested_dates_missing_or_failed",
            )

        def broker_flow_price_levels(self, *_args, **_kwargs):
            return TwmdBrokerFlowPriceLevelsRead(
                "TWSE:2330", "/api/v1/broker-flow/price-levels", "2026-07-24",
                prices, "available" if prices else "not_materialized",
                "detail_rows_present" if prices else "detail_projection_not_materialized",
            )

    return Client()


def test_same_branch_code_is_partitioned_by_provider_native_unit_and_revision():
    quantities = [
        _quantity("capital", "2026-07-23", "capital:4:12340001", "0001", "lots", 1000, 10, 5, "capital-r1"),
        _quantity("twse", "2026-07-24", "twse:0001", "0001", "shares", 1, 10000, 20000, "twse-r1"),
    ]
    coverage = [
        _coverage("capital", "2026-07-23", "AVAILABLE", 1, "capital-r1"),
        _coverage("twse", "2026-07-24", "AVAILABLE", 1, "twse-r1"),
    ]
    price = TwmdBrokerFlowPriceLevelObservation(
        provider="twse", dataset="broker_flow", instrument_id="TWSE:2330", symbol="2330",
        trade_date="2026-07-24", source_branch_key="twse:0001", branch_code="0001",
        branch_name="twse branch", price=Decimal("101.500000000000000001"), buy_native=100,
        sell_native=20, native_unit="shares", precision_shares=1, revision_id="twse-r2",
    )
    client = _client(quantities, coverage, [price])

    block = broker_flow_block(
        client, "TWSE:2330", date(2026, 7, 23), date(2026, 7, 24), today_taipei=date(2026, 10, 7)
    )

    data = block.data
    assert block.status == "partial"
    assert len(data["quantity_groups"]) == 2
    by_provider = {group["provider"]: group for group in data["quantity_groups"]}
    assert by_provider["capital"]["native_unit"] == "lots"
    assert by_provider["capital"]["observed_buy_denominator_native"] == 10
    assert by_provider["twse"]["native_unit"] == "shares"
    assert by_provider["twse"]["observed_buy_denominator_native"] == 10000
    assert [group["top_buy"][0]["source_branch_key"] for group in data["quantity_groups"]] == ["capital:4:12340001", "twse:0001"]
    assert data["quantity_observations"][1]["buy_vwap"] == "100.123456789012345678"
    assert data["price_levels"]["observations"][0]["price"] == "101.500000000000000001"
    assert data["price_levels"]["revision_consistency_with_same_date_quantities"] == "conflict"
    assert "revisions conflict" in " ".join(block.evidence["revision_consistency_warnings"])
    assert "not an all-market denominator" in by_provider["capital"]["denominator_definition"]


def test_coverage_statuses_and_empty_materialized_price_detail_remain_explicit():
    coverage = [
        _coverage("capital", "2026-07-20", "EMPTY", 0, "cap-r1"),
        _coverage("capital", "2026-07-21", "FAILED", 0, "cap-r2", "archive parse failed"),
        _coverage("capital", "2026-07-22", "MISSING", 0),
        _coverage("twse", "2026-07-24", "CLOSED", 0),
    ]

    class Client:
        config = {"timeout_sec": 5}
        def broker_flow_quantities(self, *_args, **_kwargs):
            return TwmdBrokerFlowQuantityRead("TWSE:2330", "/q", "2026-07-20", "2026-07-24", [], "unknown", "quantity_rows_not_returned")
        def broker_flow_coverage(self, *_args, **_kwargs):
            return TwmdBrokerFlowCoverageRead("TWSE:2330", "/c", "2026-07-20", "2026-07-24", coverage, "partial", "mixed_source_coverage_statuses")
        def broker_flow_price_levels(self, *_args, **_kwargs):
            return TwmdBrokerFlowPriceLevelsRead("TWSE:2330", "/p", "2026-07-24", [], "unknown", "materialized_no_rows_status_unknown")

    block = broker_flow_block(
        Client(), "TWSE:2330", date(2026, 7, 20), date(2026, 7, 24), today_taipei=date(2026, 10, 7)
    )

    assert block.status == "partial"
    assert [row["status"] for row in block.data["coverage_observations"]] == ["EMPTY", "FAILED", "MISSING", "CLOSED"]
    assert block.data["coverage_observations"][1]["failure_reason"] == "archive parse failed"
    assert block.data["price_levels"]["status"] == "unknown"
    assert "cannot distinguish those outcomes" in block.data["price_levels"]["no_rows_interpretation"]


def test_provider_endpoint_failure_does_not_erase_coverage_and_price_status():
    coverage = [_coverage("twse", "2026-10-02", "AVAILABLE", 1, "r1")]
    block = broker_flow_block(
        _client([], coverage, [], fail_quantity=True),
        "TWSE:2330", date(2026, 10, 2), date(2026, 10, 2), today_taipei=date(2026, 10, 7),
    )

    assert block.status == "partial"
    assert block.data["quantity_range"]["status"] == "error"
    assert block.data["quantity_range"]["reason"] == "http_503"
    assert block.data["coverage_observations"][0]["status"] == "AVAILABLE"
    assert block.data["price_levels"]["status"] == "not_materialized"


def test_over_31_day_quantity_range_is_explicit_while_coverage_can_continue_to_366_days():
    calls = []

    class Client:
        config = {"timeout_sec": 5}
        def broker_flow_quantities(self, *_args, **_kwargs):
            calls.append("quantities")
            raise AssertionError("quantity endpoint must not truncate or exceed its bound")
        def broker_flow_coverage(self, *_args, **_kwargs):
            calls.append("coverage")
            return TwmdBrokerFlowCoverageRead("TWSE:2330", "/c", "2026-09-01", "2026-10-02", [], "missing", "coverage_missing")
        def broker_flow_price_levels(self, *_args, **_kwargs):
            calls.append("price-levels")
            return TwmdBrokerFlowPriceLevelsRead("TWSE:2330", "/p", "2026-10-02", [], "not_materialized", "detail_projection_not_materialized")

    block = broker_flow_block(
        Client(), "TWSE:2330", date(2026, 9, 1), date(2026, 10, 2), today_taipei=date(2026, 10, 7)
    )

    assert calls == ["coverage", "price-levels"]
    assert block.data["quantity_range"]["status"] == "unsupported"
    assert block.data["quantity_range"]["reason"] == "quantity_range_exceeds_31_calendar_days"
    assert block.data["coverage_range"]["max_calendar_days"] == 366
    assert block.data["coverage_range"]["start_date"] == "2026-09-01"


def test_count_or_revision_conflicts_cannot_claim_complete_source_coverage():
    quantities = [_quantity('twse', '2026-10-02', 'twse:0001', '0001', 'shares', 1, 10, 5, 'r1')]
    for rows, count, revision in [(quantities, 2, 'r1'), (quantities, 1, 'r2'), ([], 1, 'r1')]:
        block = broker_flow_block(_client(rows, [_coverage('twse', '2026-10-02', 'AVAILABLE', count, revision)], []),
                                  'TWSE:2330', date(2026, 10, 2), date(2026, 10, 2), today_taipei=date(2026, 10, 7))
        assert block.status == 'partial'
        assert block.data['revision_consistency_warnings']
        assert block.data['revision_consistency_warnings'] == block.evidence['revision_consistency_warnings']
        for group in block.data['quantity_groups']:
            assert not group['coverage_complete_for_source_dates']
            assert group['coverage_reconciliation_dates'] == ['2026-10-02']


def test_missing_and_failed_dates_are_listed_in_the_observed_group():
    quantities = [_quantity('twse', '2026-10-01', 'twse:0001', '0001', 'shares', 1, 10, 5, 'r1')]
    coverage = [_coverage('twse', '2026-10-01', 'AVAILABLE', 1, 'r1'),
                _coverage('twse', '2026-10-02', 'MISSING', 0),
                _coverage('twse', '2026-10-03', 'FAILED', 0, 'r3', 'producer failed')]
    block = broker_flow_block(_client(quantities, coverage, []), 'TWSE:2330', date(2026, 10, 1), date(2026, 10, 3), today_taipei=date(2026, 10, 7))
    assert block.data['quantity_groups'][0]['coverage_missing_dates'] == ['2026-10-02', '2026-10-03']
    assert block.data['quantity_groups'][0]['observed_buy_denominator_native'] == 10


def test_ranking_uses_side_totals_with_deterministic_ties_and_null_zero_denominator():
    quantities = [_quantity('twse', '2026-10-02', f'twse:{index:04}', f'{index:04}', 'shares', 1, 10, 0, 'r1') for index in range(6, 0, -1)]
    block = broker_flow_block(_client(quantities, [_coverage('twse', '2026-10-02', 'AVAILABLE', 6, 'r1')], []),
                              'TWSE:2330', date(2026, 10, 2), date(2026, 10, 2), today_taipei=date(2026, 10, 7))
    group = block.data['quantity_groups'][0]
    assert [row['branch_code'] for row in group['top_buy']] == ['0001', '0002', '0003', '0004', '0005']
    assert group['observed_buy_denominator_native'] == 60
    assert group['top_n_buy_concentration_pct'] == '83.3333'
    assert group['top_n_sell_concentration_pct'] is None
    assert group['coverage_complete_for_source_dates']


def test_broker_component_failure_is_not_cached_as_a_successful_partial_block():
    from src.modules.research.taiwan_research import TaiwanResearchService, clear_taiwan_research_cache
    from src.modules.research.twmd_profile_revenue import ResearchDataBlock
    clear_taiwan_research_cache()
    service = TaiwanResearchService(config={'base_url': 'http://fixture-broker-cache'})
    calls = []
    def build():
        calls.append(1)
        return ResearchDataBlock({'quantity_range': {'status': 'error'}, 'coverage_range': {'status': 'available'}}, 'partial', 'some_broker_flow_endpoints_failed', {})
    key = ('broker-test',)
    service._load_block('broker_flow', key, build)
    from src.modules.research.taiwan_research import _cache_get
    assert _cache_get(key) is None
    clear_taiwan_research_cache()


def test_broker_subrequests_do_not_start_after_the_shared_deadline(monkeypatch):
    import src.modules.research.twmd_broker_flow as broker
    monkeypatch.setattr(broker.time, 'monotonic', lambda: 20.0)
    calls = []
    class Client:
        config = {'timeout_sec': 5}
        def read(self, *_a, **_k):
            calls.append(1)
            raise AssertionError('deadline has expired')
        broker_flow_quantities = broker_flow_coverage = broker_flow_price_levels = read
    block = broker_flow_block(Client(), 'TWSE:2330', date(2026, 10, 2), date(2026, 10, 2), today_taipei=date(2026, 10, 7), deadline_monotonic=10)
    assert calls == []
    assert block.status == 'error'
    assert block.reason == 'timeout'


def test_non_four_digit_twse_etf_is_unsupported_without_any_broker_http_read(monkeypatch):
    from src.modules.research.taiwan_research import TaiwanResearchService, clear_taiwan_research_cache
    from marketdata.vendors.twmd import TwmdClient
    clear_taiwan_research_cache()
    calls = []
    def response(_self, path, **_params):
        calls.append(path)
        if path == 'instruments':
            return [{'instrument_id': 'TWSE:00878', 'symbol': '00878', 'venue': 'TWSE', 'security_type': 'ETF', 'is_active': True}], {}
        raise TwmdReadError('fixture unavailable', reason_code='provider_error')
    monkeypatch.setattr(TwmdClient, 'get_response', response)
    payload = TaiwanResearchService(client=TwmdClient({}), config={'base_url': 'http://etf-broker-fixture'}).collect(
        'TWSE:00878', start_date='2026-10-02', end_date='2026-10-02', start_month='2026-08', end_month='2026-08', today_taipei=date(2026, 10, 7))
    assert payload['blocks']['broker_flow']['status'] == 'unsupported'
    assert payload['blocks']['broker_flow']['reason'] == 'twse_four_digit_only'
    assert not any('broker-flow' in path for path in calls)
    clear_taiwan_research_cache()
