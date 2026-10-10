"""Strict decoders for official TWMD benchmark and raw daily-bar reads."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from urllib.parse import urlsplit

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdBenchmarkBar,
    TwmdBenchmarkBarsRead,
    TwmdBenchmarkCoverageDay,
    TwmdBenchmarkDefinition,
    TwmdBenchmarkProvenance,
    TwmdDailyBarsRead,
    TwmdDailyPriceBar,
)


_DEFINITIONS = {
    "TAIEX": ("TWSE", "TWSE", "MI_5MINS_HIST", "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"),
    "TPEX": ("TPEX", "TPEx", "tpex_index", "https://www.tpex.org.tw/openapi/v1/tpex_index"),
}
_DIGEST_LENGTH = 64


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def _date(value: object, field: str) -> str:
    raw = _string(value, field)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != raw:
        raise ValueError(f"{field} must use YYYY-MM-DD")
    return raw


def _timestamp(value: object, field: str) -> str:
    raw = _string(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return raw


def _digest(value: object, field: str) -> str:
    raw = _string(value, field)
    if len(raw) != _DIGEST_LENGTH or any(char not in "0123456789abcdef" for char in raw.lower()):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return raw


def _publisher_endpoint(value: object, field: str, expected: str) -> str:
    """Allow query-scoped capture URLs only on the pinned HTTPS publisher path."""
    raw = _string(value, field)
    parsed = urlsplit(raw)
    expected_parts = urlsplit(expected)
    if (
        parsed.scheme != "https" or parsed.netloc != expected_parts.netloc
        or parsed.path != expected_parts.path or parsed.username or parsed.password
        or parsed.fragment
    ):
        raise ValueError(f"{field} does not use the pinned publisher endpoint")
    return raw


def _decimal(value: object, field: str, *, source_string: bool = False) -> Decimal:
    if source_string and not isinstance(value, str):
        raise ValueError(f"{field} must preserve the source decimal string")
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{field} must be a decimal token")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal token") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be a finite positive decimal")
    return result


def decode_benchmark_definitions(payload: object) -> tuple[TwmdBenchmarkDefinition, ...]:
    """Validate the fixed official benchmark IDs and their venue defaults."""
    try:
        root = _object(payload, "benchmark definitions response")
        rows = root.get("benchmarks")
        if not isinstance(rows, list):
            raise ValueError("benchmarks must be a list")
        result = []
        seen: set[str] = set()
        for raw in rows:
            item = _object(raw, "benchmark definition")
            benchmark_id = _string(item.get("benchmark_id"), "benchmark_id")
            if benchmark_id not in _DEFINITIONS or benchmark_id in seen:
                raise ValueError("benchmark definition ID is unsupported or duplicated")
            venue, provider, alias, expected_url = _DEFINITIONS[benchmark_id]
            if item.get("venue") != venue or item.get("provider") != provider or item.get("source_alias") != alias:
                raise ValueError("benchmark definition venue or source identity is inconsistent")
            if item.get("price_kind") != "benchmark_index" or item.get("unit") != "index_points":
                raise ValueError("benchmark definition price kind or unit is invalid")
            if item.get("basis") != "raw_price_index" or item.get("stock_venue_default") != benchmark_id:
                raise ValueError("benchmark definition basis or stock-venue default is invalid")
            source_url = _publisher_endpoint(item.get("source_url"), "source_url", expected_url)
            if source_url != expected_url:
                raise ValueError("benchmark definition URL must be the pinned publisher endpoint")
            result.append(TwmdBenchmarkDefinition(
                benchmark_id=benchmark_id,
                venue=venue,
                name=_string(item.get("name"), "name"),
                name_zh=_string(item.get("name_zh"), "name_zh"),
                provider=provider,
                source_alias=alias,
                source_contract=_string(item.get("source_contract"), "source_contract"),
                source_url=source_url,
                stock_venue_default=benchmark_id,
            ))
            seen.add(benchmark_id)
        if seen != set(_DEFINITIONS):
            raise ValueError("benchmark definitions must include TAIEX and TPEX")
        return tuple(result)
    except (TypeError, ValueError) as exc:
        raise TwmdReadError("twmd benchmark definitions violated their contract", reason_code="invalid_response") from exc


def decode_benchmark_bars(
    payload: object,
    *,
    benchmark_id: str,
    requested_start: str | None,
    requested_end: str | None,
    limit: int,
) -> TwmdBenchmarkBarsRead:
    """Validate identity, dates, units, bars, coverage, and capture receipts."""
    try:
        if benchmark_id not in _DEFINITIONS:
            raise ValueError("requested benchmark ID is unsupported")
        if (requested_start is None) != (requested_end is None):
            raise ValueError("requested date bounds must be paired")
        root = _object(payload, "benchmark bars response")
        venue, provider, alias, expected_url = _DEFINITIONS[benchmark_id]
        for key, expected in {
            "benchmark_id": benchmark_id,
            "venue": venue,
            "provider": provider,
            "source_alias": alias,
            "timeframe": "day",
            "price_kind": "benchmark_index",
            "adjustment_mode": "raw_price_index",
            "unit": "index_points",
            "basis": "raw_price_index",
        }.items():
            if root.get(key) != expected:
                raise ValueError(f"{key} does not match the requested benchmark contract")
        source_contract = _string(root.get("source_contract"), "source_contract")
        source_url = _publisher_endpoint(root.get("source_url"), "source_url", expected_url)
        if source_url != expected_url:
            raise ValueError("benchmark bars source URL must be the pinned publisher endpoint")
        _string(root.get("name"), "name")
        _string(root.get("name_zh"), "name_zh")
        response_limit = _integer(root.get("limit"), "limit", minimum=1)
        if response_limit != limit or response_limit > 1000:
            raise ValueError("response limit does not match the bounded request")
        returned = _integer(root.get("returned_count"), "returned_count")
        total = _integer(root.get("total_count"), "total_count")
        if total < returned or returned > limit:
            raise ValueError("benchmark bar counts are inconsistent")
        partial = _boolean(root.get("partial"), "partial")
        truncated = _boolean(root.get("truncated"), "truncated")
        if truncated != (total > returned):
            raise ValueError("benchmark truncation flag does not match counts")
        requested = _object(root.get("requested_range"), "requested_range")
        if requested.get("inclusive") is not True:
            raise ValueError("benchmark requested range must be inclusive")
        response_start = requested.get("start_date")
        response_end = requested.get("end_date")
        response_start = _date(response_start, "requested_range.start_date") if response_start is not None else None
        response_end = _date(response_end, "requested_range.end_date") if response_end is not None else None
        if response_start != requested_start or response_end != requested_end:
            raise ValueError("benchmark response range does not match the request")
        window = _object(root.get("coverage_window"), "coverage_window")
        window_start = _date(window.get("start_date"), "coverage_window.start_date")
        window_end = _date(window.get("end_date"), "coverage_window.end_date")
        if window_start > window_end:
            raise ValueError("benchmark coverage window is reversed")
        for key in ("complete", "schema_ready", "evidence_truncated"):
            _boolean(window.get(key), f"coverage_window.{key}")
        available_count = _integer(window.get("available_count"), "coverage_window.available_count")
        missing_count = _integer(window.get("missing_count"), "coverage_window.missing_count")

        raw_bars = root.get("bars")
        raw_coverage = root.get("coverage")
        raw_gaps = root.get("gaps")
        raw_provenance = root.get("provenance")
        if not all(isinstance(value, list) for value in (raw_bars, raw_coverage, raw_gaps, raw_provenance)):
            raise ValueError("benchmark bars, coverage, gaps, and provenance must be lists")
        if len(raw_bars) != returned:
            raise ValueError("returned_count does not match bars")
        bars: list[TwmdBenchmarkBar] = []
        for raw in raw_bars:
            item = _object(raw, "benchmark bar")
            if item.get("benchmark_id") != benchmark_id or item.get("unit") != "index_points" or item.get("basis") != "raw_price_index":
                raise ValueError("benchmark bar identity, unit, or basis is invalid")
            trade_date = _date(item.get("trade_date"), "trade_date")
            opened = _decimal(item.get("open"), "open", source_string=True)
            high = _decimal(item.get("high"), "high", source_string=True)
            low = _decimal(item.get("low"), "low", source_string=True)
            close = _decimal(item.get("close"), "close", source_string=True)
            if high < low or not low <= opened <= high or not low <= close <= high:
                raise ValueError("benchmark OHLC values are inconsistent")
            bar_contract = _string(item.get("source_contract"), "bar.source_contract")
            bar_alias = _string(item.get("source_alias"), "bar.source_alias")
            bar_url = _publisher_endpoint(item.get("source_url"), "bar.source_url", expected_url)
            if (bar_contract, bar_alias) != (source_contract, alias):
                raise ValueError("benchmark bar provenance differs from its definition")
            if requested_start and not requested_start <= trade_date <= requested_end:
                raise ValueError("benchmark bar is outside the requested date range")
            bars.append(TwmdBenchmarkBar(
                benchmark_id=benchmark_id, trade_date=trade_date,
                open=opened, high=high, low=low, close=close,
                unit="index_points", basis="raw_price_index",
                revision=_integer(item.get("revision"), "revision", minimum=1),
                capture_id=_string(item.get("capture_id"), "capture_id"),
                captured_at=_timestamp(item.get("captured_at"), "captured_at"),
                source_contract=bar_contract, source_alias=bar_alias, source_url=bar_url,
                request_scope=_string(item.get("request_scope"), "request_scope"),
                payload_sha256=_digest(item.get("payload_sha256"), "payload_sha256"),
            ))
        dates = [item.trade_date for item in bars]
        if dates != sorted(set(dates)):
            raise ValueError("benchmark bars must be unique and sorted oldest first")

        coverage: list[TwmdBenchmarkCoverageDay] = []
        for raw in raw_coverage:
            item = _object(raw, "benchmark coverage day")
            trade_date = _date(item.get("trade_date"), "coverage.trade_date")
            status = _string(item.get("status"), "coverage.status")
            if status not in {"AVAILABLE", "MISSING"}:
                raise ValueError("benchmark coverage status is invalid")
            coverage.append(TwmdBenchmarkCoverageDay(trade_date=trade_date, status=status))
        coverage_dates = [item.trade_date for item in coverage]
        if coverage_dates != sorted(set(coverage_dates)):
            raise ValueError("benchmark coverage dates must be unique and ordered")
        if not coverage_dates or coverage_dates[0] != window_start or coverage_dates[-1] != window_end:
            raise ValueError("benchmark coverage endpoints do not match the declared window")
        gaps = tuple(_date(item, "gap") for item in raw_gaps)
        expected_gaps = tuple(item.trade_date for item in coverage if item.status == "MISSING")
        if gaps != expected_gaps:
            raise ValueError("benchmark gaps do not match coverage")
        if len(coverage) != (date.fromisoformat(window_end) - date.fromisoformat(window_start)).days + 1:
            raise ValueError("benchmark coverage does not span its declared window")
        if requested_start is not None and (window_start != requested_start or window_end != requested_end):
            raise ValueError("bounded benchmark coverage window does not match the request")
        available_dates = {item.trade_date for item in coverage if item.status == "AVAILABLE"}
        if any(item.trade_date in set(coverage_dates) and item.trade_date not in available_dates for item in bars):
            raise ValueError("a selected benchmark bar is marked missing in coverage")
        if available_count != sum(item.status == "AVAILABLE" for item in coverage) or missing_count != len(gaps):
            raise ValueError("benchmark coverage summary counts are inconsistent")
        if available_count > total or (requested_start is not None and available_count != total):
            raise ValueError("benchmark total does not match retained coverage")
        complete = window["schema_ready"] and not gaps
        if window["complete"] != complete:
            raise ValueError("benchmark completeness does not match source coverage")
        expected_partial = not complete or total > returned or (requested_start is None and returned < limit)
        if partial != expected_partial:
            raise ValueError("benchmark partial flag does not match coverage and selection")
        if window["evidence_truncated"] != (requested_start is None and total > available_count):
            raise ValueError("benchmark evidence truncation does not match the coverage window")
        if not window["schema_ready"] and (total or available_count or raw_provenance):
            raise ValueError("unready benchmark schema cannot contain retained observations")

        provenance: list[TwmdBenchmarkProvenance] = []
        for raw in raw_provenance:
            item = _object(raw, "benchmark provenance")
            if item.get("benchmark_id") != benchmark_id:
                raise ValueError("benchmark provenance identity is inconsistent")
            prov_contract = _string(item.get("source_contract"), "provenance.source_contract")
            prov_alias = _string(item.get("source_alias"), "provenance.source_alias")
            prov_url = _string(item.get("source_url"), "provenance.source_url")
            _publisher_endpoint(prov_url, "provenance.source_url", expected_url)
            if (prov_contract, prov_alias) != (source_contract, alias):
                raise ValueError("benchmark provenance source identity is inconsistent")
            publication_start = item.get("publication_start")
            publication_end = item.get("publication_end")
            provenance.append(TwmdBenchmarkProvenance(
                capture_id=_string(item.get("capture_id"), "provenance.capture_id"),
                benchmark_id=benchmark_id,
                captured_at=_timestamp(item.get("captured_at"), "provenance.captured_at"),
                acquisition_date=_date(item.get("acquisition_date"), "provenance.acquisition_date"),
                source_contract=prov_contract, source_alias=prov_alias, source_url=prov_url,
                request_scope=_string(item.get("request_scope"), "provenance.request_scope"),
                publication_start=_date(publication_start, "publication_start") if publication_start is not None else None,
                publication_end=_date(publication_end, "publication_end") if publication_end is not None else None,
                payload_sha256=_digest(item.get("payload_sha256"), "provenance.payload_sha256"),
                record_count=_integer(item.get("record_count"), "provenance.record_count"),
            ))
        provenance_total = _integer(root.get("provenance_total_count"), "provenance_total_count")
        provenance_truncated = _boolean(root.get("provenance_truncated"), "provenance_truncated")
        if provenance_total < len(provenance) or provenance_truncated != (provenance_total > len(provenance)):
            raise ValueError("benchmark provenance counts are inconsistent")
        provenance_by_capture = {item.capture_id: item for item in provenance}
        if len(provenance_by_capture) != len(provenance):
            raise ValueError("benchmark provenance capture IDs must be unique")
        for bar in bars:
            receipt = provenance_by_capture.get(bar.capture_id)
            if receipt is None and not provenance_truncated:
                raise ValueError("benchmark bar has no retained capture receipt")
            if receipt is not None and (
                receipt.benchmark_id != bar.benchmark_id
                or receipt.source_contract != bar.source_contract
                or receipt.source_alias != bar.source_alias
                or receipt.source_url != bar.source_url
                or receipt.request_scope != bar.request_scope
                or receipt.payload_sha256 != bar.payload_sha256
                or receipt.captured_at != bar.captured_at
            ):
                raise ValueError("benchmark bar capture does not match its retained provenance receipt")
        served_at = _timestamp(root.get("served_at"), "served_at")
        return TwmdBenchmarkBarsRead(
            benchmark_id=benchmark_id, venue=venue, provider=provider,
            source_alias=alias, source_contract=source_contract, source_url=source_url,
            timeframe="day", price_kind="benchmark_index", adjustment_mode="raw_price_index",
            unit="index_points", basis="raw_price_index", limit=limit,
            returned_count=returned, total_count=total, partial=partial, truncated=truncated,
            requested_start_date=requested_start, requested_end_date=requested_end,
            coverage_window=dict(window), coverage=tuple(coverage), gaps=gaps,
            bars=tuple(bars), provenance=tuple(provenance),
            provenance_total_count=provenance_total, provenance_truncated=provenance_truncated,
            served_at=served_at,
        )
    except (TypeError, ValueError) as exc:
        raise TwmdReadError("twmd benchmark bars violated their contract", reason_code="invalid_response") from exc


def decode_daily_bars(
    payload: object,
    *,
    instrument_id: str,
    limit: int,
) -> TwmdDailyBarsRead:
    """Validate latest-only raw stock bars while preserving real null closes."""
    try:
        if not isinstance(instrument_id, str) or not re.fullmatch(r"(?:TWSE|TPEX):[0-9][0-9A-Z]{3,5}", instrument_id):
            raise ValueError("daily-bar request requires a canonical venue identity")
        venue, symbol = instrument_id.split(":", 1)
        if venue not in {"TWSE", "TPEX"} or not symbol:
            raise ValueError("daily-bar request identity is invalid")
        root = _object(payload, "daily bars response")
        for key, expected in {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "venue": venue,
            "timeframe": "day",
            "price_kind": "eod",
            "adjustment_mode": "raw",
            "provider": venue,
        }.items():
            if root.get(key) != expected:
                raise ValueError(f"daily bars {key} does not match raw stock identity")
        if _integer(root.get("limit"), "limit", minimum=1) != limit:
            raise ValueError("daily bars limit does not match the request")
        units = _object(root.get("units"), "units")
        if units != {"currency": "TWD", "price": "TWD", "volume": "shares", "value": "TWD", "transactions": "trades"}:
            raise ValueError("daily bars do not declare the official TWD and share units")
        returned = _integer(root.get("returned_count"), "returned_count")
        if returned > limit:
            raise ValueError("daily bars response exceeds its request limit")
        partial = _boolean(root.get("partial"), "partial")
        raw_bars = root.get("bars")
        if not isinstance(raw_bars, list) or len(raw_bars) != returned:
            raise ValueError("daily bars count does not match the response")
        bars: list[TwmdDailyPriceBar] = []
        for raw in raw_bars:
            item = _object(raw, "daily bar")
            if item.get("instrument_id") != instrument_id or item.get("symbol") != symbol:
                raise ValueError("daily bar identity does not match request")
            trade_date = _date(item.get("trade_date"), "trade_date")
            close_value = item.get("close")
            close = None if close_value is None else _decimal(close_value, "close")
            observation = _string(item.get("observation_status"), "observation_status")
            coverage = _object(item.get("coverage"), "daily bar coverage")
            coverage_status = _string(coverage.get("status"), "coverage.status")
            if coverage.get("dataset") != f"{venue.lower()}_daily_price":
                raise ValueError("daily bar coverage dataset does not match the venue")
            if _date(coverage.get("partition_key"), "coverage.partition_key") != trade_date:
                raise ValueError("daily bar coverage partition does not match its trade date")
            if coverage_status not in {"AVAILABLE", "EMPTY", "MISSING"}:
                raise ValueError("daily bar coverage status is invalid")
            record_count = _integer(coverage.get("record_count"), "coverage.record_count")
            if observation == "traded" and (coverage_status != "AVAILABLE" or record_count < 1):
                raise ValueError("traded daily bars require available source coverage")
            if observation not in {"traded", "untraded", "no_close"}:
                raise ValueError("daily bar observation status is invalid")
            if observation == "traded" and close is None:
                raise ValueError("traded daily bars require a close")
            if observation == "no_close" and close is not None:
                raise ValueError("no_close daily bars must preserve a null close")
            bars.append(TwmdDailyPriceBar(
                trade_date=trade_date, close=close,
                observation_status=observation, coverage_status=coverage_status,
                coverage_dataset=coverage["dataset"], coverage_record_count=record_count,
                coverage_acquired_at=_timestamp(coverage["acquired_at"], "coverage.acquired_at") if coverage.get("acquired_at") is not None else None,
                coverage_checksum=_string(coverage["checksum"], "coverage.checksum") if coverage.get("checksum") is not None else None,
            ))
        dates = [item.trade_date for item in bars]
        if dates != sorted(set(dates)):
            raise ValueError("daily bars must be unique and sorted oldest first")
        availability = _object(root.get("availability"), "availability")
        latest = availability.get("latest_dataset_trade_date")
        latest = _date(latest, "latest_dataset_trade_date") if latest is not None else None
        return TwmdDailyBarsRead(
            instrument_id=instrument_id, symbol=symbol, venue=venue,
            name=_string(root.get("name"), "name", allow_empty=True),
            timeframe="day", price_kind="eod", adjustment_mode="raw", provider=venue,
            limit=limit, returned_count=returned, partial=partial,
            bars=tuple(bars), latest_dataset_trade_date=latest,
        )
    except (TypeError, ValueError) as exc:
        raise TwmdReadError("twmd daily bars violated their contract", reason_code="invalid_response") from exc
