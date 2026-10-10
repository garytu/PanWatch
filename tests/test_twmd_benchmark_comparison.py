from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdBenchmarkBar,
    TwmdBenchmarkDefinition,
    TwmdBenchmarkBarsRead,
    TwmdDailyBarsRead,
    TwmdDailyPriceBar,
)
from src.modules.research.twmd_benchmarks import benchmark_comparison_block
from src.modules.research.twmd_profile_revenue import ResearchDataBlock
import src.modules.research.taiwan_research as research


def _definition(benchmark_id="TAIEX", venue="TWSE"):
    return TwmdBenchmarkDefinition(
        benchmark_id=benchmark_id, venue=venue, name=benchmark_id, name_zh=benchmark_id,
        provider="TWSE" if venue == "TWSE" else "TPEx",
        source_alias="MI_5MINS_HIST" if venue == "TWSE" else "tpex_index",
        source_contract="official_daily_index", source_url="https://publisher.example/index",
        stock_venue_default=benchmark_id,
    )


def _stock(dates=("2026-10-01", "2026-10-02", "2026-10-06"), *, identity="TWSE:2330"):
    venue, symbol = identity.split(":", 1)
    bars = tuple(TwmdDailyPriceBar(
        trade_date=trade_date, close=Decimal(str(100 + index * 10)),
        observation_status="traded", coverage_status="AVAILABLE",
    ) for index, trade_date in enumerate(dates))
    return TwmdDailyBarsRead(
        instrument_id=identity, symbol=symbol, venue=venue, name="Stock",
        timeframe="day", price_kind="eod", adjustment_mode="raw", provider=venue,
        limit=1000, returned_count=len(bars), partial=True, bars=bars,
        latest_dataset_trade_date=dates[-1] if dates else None,
    )


def _benchmark(dates=("2026-10-01", "2026-10-03", "2026-10-06"), *, benchmark_id="TAIEX", venue="TWSE"):
    bars = tuple(TwmdBenchmarkBar(
        benchmark_id=benchmark_id, trade_date=trade_date,
        open=Decimal(str(1000 + index * 10)), high=Decimal(str(1010 + index * 10)),
        low=Decimal(str(995 + index * 10)), close=Decimal(str(1000 + index * 10)),
        unit="index_points", basis="raw_price_index", revision=index + 1,
        capture_id=f"capture-{index}", captured_at="2026-10-07T08:00:00Z",
        source_contract="official_daily_index", source_alias="index",
        source_url="https://publisher.example/index", request_scope="latest",
        payload_sha256=f"{index:064x}",
    ) for index, trade_date in enumerate(dates))
    return TwmdBenchmarkBarsRead(
        benchmark_id=benchmark_id, venue=venue,
        provider="TWSE" if venue == "TWSE" else "TPEx", source_alias="index",
        source_contract="official_daily_index", source_url="https://publisher.example/index",
        timeframe="day", price_kind="benchmark_index", adjustment_mode="raw_price_index",
        unit="index_points", basis="raw_price_index", limit=366,
        returned_count=len(bars), total_count=len(bars), partial=True, truncated=True,
        requested_start_date="2026-10-01", requested_end_date="2026-10-06",
        coverage_window={"start_date": "2026-10-01", "end_date": "2026-10-06", "available_count": 3,
                         "missing_count": 3, "complete": False},
        coverage=(), gaps=("2026-10-02",), bars=bars, provenance=(),
        provenance_total_count=len(bars), provenance_truncated=True,
        served_at="2026-10-07T08:02:00Z",
    )


class _Client:
    def __init__(self, *, stock=None, benchmark=None, definition=None, stock_error=None, benchmark_error=None):
        self.stock = stock if stock is not None else _stock()
        self.benchmark = benchmark if benchmark is not None else _benchmark()
        self.definition = definition if definition is not None else _definition()
        self.stock_error = stock_error
        self.benchmark_error = benchmark_error
        self.calls = []

    def benchmark_definitions(self, **kwargs):
        self.calls.append(("definitions", kwargs))
        return (self.definition,)

    def daily_bars(self, instrument_id, **kwargs):
        self.calls.append(("stock", instrument_id, kwargs))
        if self.stock_error:
            raise self.stock_error
        return self.stock

    def benchmark_bars(self, benchmark_id, **kwargs):
        self.calls.append(("benchmark", benchmark_id, kwargs))
        if self.benchmark_error:
            raise self.benchmark_error
        return self.benchmark


def _compare(client, instrument="TWSE:2330"):
    return benchmark_comparison_block(
        client, instrument, "2026-10-01", "2026-10-06", today_taipei=date(2026, 10, 8),
    )


def test_comparison_uses_actual_common_dates_and_percentage_point_difference():
    client = _Client()
    block = _compare(client)
    assert block.status == "available"
    assert block.data["common_observation_dates"] == ["2026-10-01", "2026-10-06"]
    result = block.data["comparison"]
    assert Decimal(result["stock_return_pct"]) == Decimal("20")
    assert Decimal(result["benchmark_return_pct"]) == Decimal("2")
    assert Decimal(result["relative_return_percentage_points"]) == Decimal("18")
    assert result["basis"] == "raw_price_return"
    assert result["observation_count"] == 2
    assert block.evidence["cacheable"] is True
    assert block.evidence["benchmark_source"]["partial"] is True
    assert block.evidence["benchmark_source"]["truncated"] is True
    assert block.evidence["stock_source"]["requested_period_returned_dates"] == [
        "2026-10-01", "2026-10-02", "2026-10-06"]
    assert "returned_bar_dates" not in block.evidence["stock_source"]
    assert client.calls[1][2]["limit"] == 1000
    assert client.calls[2][2]["start_date"] == "2026-10-01"
    assert client.calls[2][2]["end_date"] == "2026-10-06"


def test_zero_benchmark_bars_stays_unavailable_without_fabricated_observations():
    client = _Client(benchmark=_benchmark(dates=()))
    block = _compare(client)
    assert block.status == "unavailable" and block.reason == "benchmark_no_bars"
    assert block.data["observations"] == [] and block.data["comparison"] is None
    assert block.evidence["cacheable"] is False


def test_nonoverlapping_dates_stay_unavailable_and_never_fill_gaps():
    client = _Client(
        stock=_stock(("2026-10-01", "2026-10-02")),
        benchmark=_benchmark(("2026-10-03", "2026-10-06")),
    )
    block = _compare(client)
    assert block.status == "unavailable" and block.reason == "insufficient_common_observation_dates"
    assert block.data["common_observation_dates"] == []
    assert block.data["comparison"] is None


def test_one_common_date_is_not_enough_for_a_return():
    client = _Client(
        stock=_stock(("2026-10-01", "2026-10-02")),
        benchmark=_benchmark(("2026-10-01", "2026-10-06")),
    )
    block = _compare(client)
    assert block.status == "unavailable" and block.data["comparison"] is None
    assert block.data["common_observation_dates"] == ["2026-10-01"]


@pytest.mark.parametrize("kwargs", [
    {"stock_error": TimeoutError("slow stock")},
    {"benchmark_error": TwmdReadError("missing benchmark", status_code=503, reason_code="http_503")},
])
def test_one_component_failure_is_isolated_and_never_cached(kwargs):
    block = _compare(_Client(**kwargs))
    assert block.status == "partial"
    assert block.evidence["cacheable"] is False


def test_wrong_venue_definition_or_read_is_rejected_without_using_it():
    client = _Client(definition=_definition("TPEX", "TPEX"))
    block = _compare(client)
    assert block.status == "partial" and block.data["comparison"] is None
    assert block.evidence["benchmark_source"]["status"] == "error"
    assert all(call[0] != "benchmark" for call in client.calls)

    bad_benchmark = _benchmark(venue="TPEX", benchmark_id="TPEX")
    block = _compare(_Client(benchmark=bad_benchmark))
    assert block.status == "partial" and block.data["comparison"] is None


def test_tpex_stock_maps_to_tpex_index_and_keeps_null_or_nontraded_rows_out():
    stock = _stock(("2026-10-01", "2026-10-02", "2026-10-06"), identity="TPEX:5347")
    stock = TwmdDailyBarsRead(**{**stock.__dict__, "bars": (
        TwmdDailyPriceBar("2026-10-01", Decimal("100"), "traded", "AVAILABLE"),
        TwmdDailyPriceBar("2026-10-02", None, "no_close", "EMPTY"),
        TwmdDailyPriceBar("2026-10-06", Decimal("120"), "traded", "AVAILABLE"),
    )})
    client = _Client(stock=stock, benchmark=_benchmark(benchmark_id="TPEX", venue="TPEX"),
                     definition=_definition("TPEX", "TPEX"))
    block = _compare(client, "TPEX:5347")
    assert client.calls[2][1] == "TPEX"
    assert block.status == "available" and block.data["common_observation_dates"] == ["2026-10-01", "2026-10-06"]


def test_research_cache_keeps_valid_partial_results_but_never_caches_failures():
    research.clear_taiwan_research_cache()
    service = research.TaiwanResearchService(client=object(), config={"base_url": "http://fixture", "token": "a"})
    key = research._cache_key(service.config, "TWSE:2330", "benchmark_comparison", ("2026-10-01", "2026-10-06"))
    partial = ResearchDataBlock(
        data={"comparison": {"basis": "raw_price_return"}}, status="available",
        reason="raw_price_returns_on_common_observation_dates",
        evidence={"cacheable": True, "benchmark_source": {"partial": True, "truncated": True}},
    )
    cached = service._load_block("benchmark_comparison", key, lambda: partial)
    cached.data["comparison"].clear()
    restored = research._cache_get(key)
    assert restored.data["comparison"] == {"basis": "raw_price_return"}
    assert restored.evidence["benchmark_source"] == {"partial": True, "truncated": True}

    failures = []
    error_key = (*key, "error")
    error = ResearchDataBlock(data=None, status="error", reason="http_503", evidence={"cacheable": False})
    service._load_block("benchmark_comparison", error_key, lambda: (failures.append(True) or error))
    service._load_block("benchmark_comparison", error_key, lambda: (failures.append(True) or error))
    assert len(failures) == 2 and research._cache_get(error_key) is None

    other = research._cache_key({"base_url": "http://fixture", "token": "b"}, "TWSE:2330", "benchmark_comparison", ("2026-10-01", "2026-10-06"))
    assert other != key
    research.clear_taiwan_research_cache()


@pytest.mark.parametrize('identity', ['TWSE:TAIEX', 'TPEX:TPEX', 'TWSE:1', '2330'])
def test_comparison_rejects_non_stock_identity_without_source_reads(identity):
    client = _Client()
    with pytest.raises(ValueError):
        _compare(client, identity)
    assert client.calls == []


def test_comparison_retains_stock_acquisition_and_actual_benchmark_source_url():
    block = _compare(_Client())
    row = block.data['observations'][0]
    assert row['stock_coverage']['partition_key'] == row['trade_date']
    assert row['stock_coverage']['status'] == 'AVAILABLE'
    assert row['stock_coverage']['acquired_at'] is None
    assert row['benchmark_source_url'] == 'https://publisher.example/index'
    assert block.evidence['publication_time'] is None
