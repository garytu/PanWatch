"""Raw-price TWSE/TPEx stock-to-index comparison with explicit common dates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, localcontext
import re
import time
from typing import Any

from marketdata.errors import TwmdReadError

from src.modules.research.twmd_profile_revenue import ResearchDataBlock


_MAX_DAYS = 366
_STOCK_LIMIT = 1000
_BENCHMARK_LIMIT = 366


def _date(value: date | str, label: str) -> date:
    if isinstance(value, datetime):
        raise TypeError(f"{label} must be a calendar date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD")
    return parsed


def _reason(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, TwmdReadError):
        status = exc.status_code if type(exc.status_code) is int and 100 <= exc.status_code <= 599 else None
        reason = exc.reason_code or (f"http_{status}" if status else "provider_error")
        if reason not in {"timeout", "transport_error", "invalid_response", "provider_error"} and not reason.startswith("http_"):
            reason = f"http_{status}" if status else "provider_error"
        return reason, status
    if isinstance(exc, TimeoutError):
        return "timeout", None
    if isinstance(exc, (TypeError, ValueError)):
        return "invalid_response", None
    return "provider_error", None


def _source_error(exc: Exception | None) -> dict[str, Any]:
    if exc is None:
        return {"status": "available", "reason": "read_succeeded", "http_status": None}
    reason, status = _reason(exc)
    return {"status": "error", "reason": reason, "http_status": status}


def _return_percent(first: Decimal, last: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 48
        return (last / first - Decimal(1)) * Decimal(100)


def benchmark_comparison_block(
    client,
    instrument_id: str,
    start_date: date | str,
    end_date: date | str,
    *,
    today_taipei: date,
    deadline_monotonic: float | None = None,
) -> ResearchDataBlock:
    """Compare raw EOD closes only on dates both official series actually contain."""
    start = _date(start_date, "start_date")
    end = _date(end_date, "end_date")
    if (end - start).days + 1 > _MAX_DAYS:
        raise ValueError("benchmark comparison is limited to 366 calendar days")
    if start > end or end >= today_taipei:
        raise ValueError("benchmark comparison requires a completed ordered date range")
    if not isinstance(instrument_id, str) or not re.fullmatch(r"(?:TWSE|TPEX):[0-9][0-9A-Z]{3,5}", instrument_id):
        raise ValueError("benchmark comparison requires a canonical Taiwan instrument ID")
    venue, symbol = instrument_id.split(":", 1)
    if venue not in {"TWSE", "TPEX"} or not symbol:
        raise ValueError("benchmark comparison requires a canonical TWSE:/TPEX: ID")

    requested = {
        "instrument_id": instrument_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "stock_bar_limit": _STOCK_LIMIT,
        "benchmark_bar_limit": _BENCHMARK_LIMIT,
        "benchmark_id": "TAIEX" if venue == "TWSE" else "TPEX",
    }
    benchmark_id = requested["benchmark_id"]
    definition_read = stock_read = benchmark_read = None
    definition_error = stock_error = benchmark_error = None

    def timeout() -> float:
        remaining = 5.0
        if deadline_monotonic is not None:
            remaining = min(remaining, deadline_monotonic - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("research deadline elapsed")
        return max(0.1, remaining)

    try:
        definition_read = client.benchmark_definitions(timeout_sec=timeout())
        matching = [item for item in definition_read if item.benchmark_id == benchmark_id]
        if len(matching) != 1 or matching[0].venue != venue or matching[0].stock_venue_default != benchmark_id:
            raise TwmdReadError("benchmark definitions do not map the stock venue", reason_code="invalid_response")
        definition = matching[0]
    except Exception as exc:
        definition_error = exc
        definition = None

    try:
        stock_read = client.daily_bars(instrument_id, limit=_STOCK_LIMIT, timeout_sec=timeout())
        if stock_read.instrument_id != instrument_id or stock_read.venue != venue:
            stock_read = None
            raise TwmdReadError("stock bars identity does not match selected venue", reason_code="invalid_response")
    except Exception as exc:
        stock_error = exc

    try:
        if definition_error is not None:
            raise definition_error
        benchmark_read = client.benchmark_bars(
            benchmark_id,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            limit=_BENCHMARK_LIMIT,
            timeout_sec=timeout(),
        )
        if benchmark_read.venue != venue or benchmark_read.benchmark_id != benchmark_id:
            benchmark_read = None
            raise TwmdReadError("benchmark bars identity does not match selected stock venue", reason_code="invalid_response")
    except Exception as exc:
        benchmark_error = exc

    stock_status = _source_error(stock_error)
    benchmark_status = _source_error(benchmark_error)
    if stock_read is not None:
        in_window_stock = [
            item.trade_date for item in stock_read.bars
            if start.isoformat() <= item.trade_date <= end.isoformat()
            and item.trade_date < today_taipei.isoformat()
        ]
        stock_status.update({
            "instrument_id": stock_read.instrument_id,
            "venue": stock_read.venue,
            "provider": stock_read.provider,
            "endpoint": "/api/v1/bars",
            "timeframe": stock_read.timeframe,
            "price_kind": stock_read.price_kind,
            "adjustment_mode": stock_read.adjustment_mode,
            "limit": stock_read.limit,
            "returned_count": stock_read.returned_count,
            "partial": stock_read.partial,
            "latest_dataset_trade_date": stock_read.latest_dataset_trade_date,
            "range_selection": "latest_only_response_filtered_by_trade_date",
            "latest_returned_trade_date": stock_read.bars[-1].trade_date if stock_read.bars else None,
            "oldest_returned_trade_date": stock_read.bars[0].trade_date if stock_read.bars else None,
            "requested_period_returned_count": len(in_window_stock),
            "requested_period_returned_dates": in_window_stock,
        })
    if benchmark_read is not None:
        benchmark_status.update({
            "benchmark_id": benchmark_read.benchmark_id,
            "venue": benchmark_read.venue,
            "provider": benchmark_read.provider,
            "endpoint": f"/api/v1/benchmarks/{benchmark_id}/bars",
            "source_alias": benchmark_read.source_alias,
            "source_contract": benchmark_read.source_contract,
            "source_url": benchmark_read.source_url,
            "unit": benchmark_read.unit,
            "basis": benchmark_read.basis,
            "requested_range": {"start_date": start.isoformat(), "end_date": end.isoformat(), "inclusive": True},
            "returned_count": benchmark_read.returned_count,
            "total_count": benchmark_read.total_count,
            "partial": benchmark_read.partial,
            "truncated": benchmark_read.truncated,
            "coverage_window": dict(benchmark_read.coverage_window),
            "gaps": list(benchmark_read.gaps),
            "provenance_total_count": benchmark_read.provenance_total_count,
            "provenance_truncated": benchmark_read.provenance_truncated,
            "bar_receipts": [{
                "trade_date": row.trade_date,
                "revision": row.revision,
                "capture_id": row.capture_id,
                "captured_at": row.captured_at,
                "source_contract": row.source_contract,
                "source_alias": row.source_alias,
                "source_url": row.source_url,
                "request_scope": row.request_scope,
                "payload_sha256": row.payload_sha256,
            } for row in benchmark_read.bars],
            "served_at": benchmark_read.served_at,
        })

    stock_by_date = {}
    if stock_read is not None:
        for row in stock_read.bars:
            if (start.isoformat() <= row.trade_date <= end.isoformat()
                    and row.trade_date < today_taipei.isoformat()
                    and row.observation_status == "traded" and row.close is not None and row.close > 0):
                stock_by_date[row.trade_date] = row
    benchmark_by_date = {
        row.trade_date: row
        for row in (benchmark_read.bars if benchmark_read is not None else ())
        if start.isoformat() <= row.trade_date <= end.isoformat()
        and row.trade_date < today_taipei.isoformat()
    }
    common_dates = sorted(stock_by_date.keys() & benchmark_by_date.keys())
    comparison = None
    if len(common_dates) >= 2:
        first_date, last_date = common_dates[0], common_dates[-1]
        stock_return = _return_percent(stock_by_date[first_date].close, stock_by_date[last_date].close)
        benchmark_return = _return_percent(benchmark_by_date[first_date].close, benchmark_by_date[last_date].close)
        comparison = {
            "requested_start_date": start.isoformat(),
            "requested_end_date": end.isoformat(),
            "calculation_start_date": first_date,
            "calculation_end_date": last_date,
            "observation_count": len(common_dates),
            "basis": "raw_price_return",
            "stock_return_pct": str(stock_return),
            "benchmark_return_pct": str(benchmark_return),
            "relative_return_percentage_points": str(stock_return - benchmark_return),
            "relative_return_definition": "stock raw-price return minus same-date benchmark raw-price-index return",
        }
    observations = []
    for trade_date in common_dates:
        stock_row = stock_by_date[trade_date]
        benchmark_row = benchmark_by_date[trade_date]
        observations.append({
            "trade_date": trade_date,
            "stock_close": str(stock_row.close),
            "stock_unit": "TWD/share",
            "stock_price_kind": "eod",
            "stock_adjustment_mode": "raw",
            "stock_coverage": {
                "dataset": stock_row.coverage_dataset,
                "partition_key": trade_date,
                "status": stock_row.coverage_status,
                "record_count": stock_row.coverage_record_count,
                "acquired_at": stock_row.coverage_acquired_at,
                "checksum": stock_row.coverage_checksum,
            },
            "benchmark_close": str(benchmark_row.close),
            "benchmark_unit": benchmark_row.unit,
            "benchmark_basis": benchmark_row.basis,
            "benchmark_revision": benchmark_row.revision,
            "benchmark_capture_id": benchmark_row.capture_id,
            "benchmark_captured_at": benchmark_row.captured_at,
            "benchmark_source_contract": benchmark_row.source_contract,
            "benchmark_source_alias": benchmark_row.source_alias,
            "benchmark_source_url": benchmark_row.source_url,
            "benchmark_request_scope": benchmark_row.request_scope,
            "benchmark_payload_sha256": benchmark_row.payload_sha256,
        })

    if stock_error is not None or benchmark_error is not None:
        status = "error" if stock_read is None and benchmark_read is None else "partial"
        reason = "stock_and_benchmark_read_failed" if status == "error" else (
            "stock_bars_unavailable" if stock_error is not None else "benchmark_bars_unavailable"
        )
    elif benchmark_read is None or not benchmark_read.bars:
        status, reason = "unavailable", "benchmark_no_bars"
    elif stock_read is None or not stock_by_date:
        status, reason = "unavailable", "stock_no_observed_closes_in_range"
    elif len(common_dates) < 2:
        status, reason = "unavailable", "insufficient_common_observation_dates"
    else:
        status, reason = "available", "raw_price_returns_on_common_observation_dates"

    evidence = {
        "provider": "twmd",
        "instrument_id": instrument_id,
        "endpoint": "/api/v1/bars; /api/v1/benchmarks; /api/v1/benchmarks/{benchmark_id}/bars",
        "selectors": requested,
        "source_contract": definition.source_contract if definition else None,
        "period": {
            "requested_start_date": start.isoformat(),
            "requested_end_date": end.isoformat(),
            "common_observation_dates": common_dates,
        },
        "source_report_date": None,
        "publication_time": None,
        "source_received_at_utc": None,
        "served_at": benchmark_read.served_at if benchmark_read else None,
        "units": {"stock_close": "TWD/share", "benchmark_close": "index_points", "relative_return": "percentage points"},
        "dataset_coverage": "source_calendar_gaps_not_exchange_calendar",
        "selected_instrument_presence": "present" if stock_by_date else "unknown",
        "revision": None,
        "capture_id": None,
        "payload_sha256": None,
        "stock_source": stock_status,
        "benchmark_source": benchmark_status,
        # Partial/truncated coverage remains useful with its source flags intact;
        # only a validated positive comparison is safe to persist in the research cache.
        "cacheable": status == "available" and stock_error is None and benchmark_error is None,
        "interpretation": (
            "Stock and index values are separate raw-price series. The stock endpoint returns latest-N only; "
            "this request filters its newest 1000 bars to the selected period. Comparisons use only actual dates "
            "present in both series. Benchmark calendar MISSING dates are not treated as exchange holidays or "
            "acquisition failures. Returns exclude dividends, are not total returns, and are not executable prices."
        ),
    }
    data = {
        "instrument_id": instrument_id,
        "benchmark_id": benchmark_id,
        "venue": venue,
        "requested_range": {"start_date": start.isoformat(), "end_date": end.isoformat(), "inclusive": True},
        "price_basis": "raw_price",
        "stock_series": {
            "instrument_id": instrument_id,
            "provider": stock_read.provider if stock_read else None,
            "timeframe": stock_read.timeframe if stock_read else "day",
            "price_kind": stock_read.price_kind if stock_read else "eod",
            "adjustment_mode": stock_read.adjustment_mode if stock_read else "raw",
            "unit": "TWD/share",
            "returned_count": stock_read.returned_count if stock_read else None,
            "partial": stock_read.partial if stock_read else None,
            "selection": "latest 1000 returned source bars filtered by actual trade_date",
        },
        "benchmark_series": {
            "benchmark_id": benchmark_id,
            "venue": venue,
            "provider": benchmark_read.provider if benchmark_read else (definition.provider if definition else None),
            "source_alias": benchmark_read.source_alias if benchmark_read else (definition.source_alias if definition else None),
            "source_contract": benchmark_read.source_contract if benchmark_read else (definition.source_contract if definition else None),
            "source_url": benchmark_read.source_url if benchmark_read else (definition.source_url if definition else None),
            "timeframe": "day",
            "price_kind": "benchmark_index",
            "adjustment_mode": "raw_price_index",
            "unit": "index_points",
            "returned_count": benchmark_read.returned_count if benchmark_read else None,
            "partial": benchmark_read.partial if benchmark_read else None,
            "truncated": benchmark_read.truncated if benchmark_read else None,
            "coverage_window": dict(benchmark_read.coverage_window) if benchmark_read else None,
        },
        "common_observation_dates": common_dates,
        "observations": observations,
        "comparison": comparison,
    }
    return ResearchDataBlock(data=data, status=status, reason=reason, evidence=evidence)


__all__ = ["benchmark_comparison_block"]
