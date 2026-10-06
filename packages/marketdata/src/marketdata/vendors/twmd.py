"""tw-market-data's stored prices, live quotes and instrument identity contracts."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from marketdata.cache import TTLCache
from marketdata.errors import TwmdReadError
from marketdata.http import MarketHttpError, MarketHttpResponse, market_get, record_error
from marketdata.symbol import Symbol
from marketdata.types import (
    Bar,
    InstitutionalFlowCoverage,
    InstitutionalFlowObservation,
    InstitutionalFlowRead,
    Quote,
    TwmdValuationObservation,
    TwmdValuationRead,
)
from marketdata.vendors.base import (
    CapitalFlowVendor,
    FundamentalsVendor,
    KlineVendor,
    QuoteVendor,
)

# Instrument reference data changes much less often than quotes. Cache only the catalog.
_catalog_cache = TTLCache(default_ttl_sec=300)
_TAIPEI = ZoneInfo("Asia/Taipei")
_VALUATION_ENDPOINT = "/api/v1/valuations"
_FLOW_ENDPOINT = "/api/v1/institutional-flows"

_TWSE_FLOW_FIELDS = (
    "foreign_non_dealer_buy_shares", "foreign_non_dealer_sell_shares",
    "foreign_non_dealer_net_shares", "foreign_dealer_buy_shares",
    "foreign_dealer_sell_shares", "foreign_dealer_net_shares",
    "investment_trust_buy_shares", "investment_trust_sell_shares",
    "investment_trust_net_shares", "dealer_reported_net_shares",
    "dealer_proprietary_buy_shares", "dealer_proprietary_sell_shares",
    "dealer_proprietary_net_shares", "dealer_hedging_buy_shares",
    "dealer_hedging_sell_shares", "dealer_hedging_net_shares",
    "total_institutional_net_shares",
)
_TPEX_FLOW_FIELDS = (
    "foreign_ex_dealer_buy_shares", "foreign_ex_dealer_sell_shares",
    "foreign_ex_dealer_net_shares", "foreign_dealer_buy_shares",
    "foreign_dealer_sell_shares", "foreign_dealer_net_shares",
    "combined_foreign_buy_shares", "combined_foreign_sell_shares",
    "combined_foreign_net_shares", "investment_trust_buy_shares",
    "investment_trust_sell_shares", "investment_trust_net_shares",
    "dealer_own_buy_shares", "dealer_own_sell_shares", "dealer_own_net_shares",
    "dealer_hedging_buy_shares", "dealer_hedging_sell_shares",
    "dealer_hedging_net_shares", "combined_dealer_buy_shares",
    "combined_dealer_sell_shares", "combined_dealer_net_shares",
    "total_institutional_net_shares",
)


def _date_value(value: date | str, label: str) -> date:
    if isinstance(value, datetime):
        raise TypeError(f"{label} must be a calendar date")
    if isinstance(value, date):
        return value
    try:
        raw = str(value)
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc


def _query_bounds(config: dict, *, today_taipei: date | None = None) -> tuple[date, date]:
    """Freeze one bounded default window, or validate the caller's exact range."""
    start_raw, end_raw = config.get("start_date"), config.get("end_date")
    if (start_raw is None) != (end_raw is None):
        raise ValueError("start_date and end_date must be provided together")
    today = today_taipei or datetime.now(_TAIPEI).date()
    if start_raw is None:
        end = today - timedelta(days=1)
        start = end - timedelta(days=29)
    else:
        start = _date_value(start_raw, "start_date")
        end = _date_value(end_raw, "end_date")
    if start > end:
        raise ValueError("start_date must not be after end_date")
    if end >= today:
        raise ValueError("end_date must be before the current Asia/Taipei date")
    return start, end


def _configured_today(config: dict) -> date | None:
    value = config.get("today_taipei")
    return _date_value(value, "today_taipei") if value is not None else None


def _source_string(value, field_name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a source string or null")
    if required and not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _source_int(value, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer or null")
    return value


def _header_bool(value: str | None, name: str) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered not in {"true", "false"}:
        raise ValueError(f"invalid {name} response header")
    return lowered == "true"


def _valuation_header_coverage(value: str | None) -> tuple[int | None, int | None, str]:
    if value is None:
        return None, None, "unknown"
    match = re.fullmatch(
        r"available=(\d+);missing=(\d+);selected=(present|absent|missing)",
        value.strip(),
    )
    if match is None:
        raise ValueError("invalid X-TWMD-Coverage response header")
    return int(match.group(1)), int(match.group(2)), match.group(3)


def _validate_source_date(value, field_name: str) -> str:
    raw = _source_string(value, field_name, required=True)
    assert raw is not None
    try:
        if date.fromisoformat(raw).isoformat() != raw:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from exc
    return raw


def _valuation_observation(row, instrument_id: str) -> TwmdValuationObservation:
    if not isinstance(row, dict):
        raise ValueError("valuation rows must be objects")
    if row.get("instrument_id") != instrument_id:
        raise ValueError("valuation row instrument_id does not match the request")
    values: dict[str, str | None] = {}
    for field_name in ("close_price", "pe_ratio", "pb_ratio", "dividend_yield_pct"):
        if field_name not in row:
            raise ValueError(f"valuation row is missing {field_name}")
    for field_name in (
        "close_price", "pe_ratio", "pb_ratio", "dividend_yield_pct", "dividend_per_share"
    ):
        value = _source_string(row.get(field_name), field_name)
        if value is not None:
            try:
                numeric = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError(f"{field_name} must be a decimal string or null") from exc
            if not numeric.is_finite():
                raise ValueError(f"{field_name} must be finite")
        values[field_name] = value
    for field_name in (
        "dividend_reference_year", "financial_reference_year", "financial_reference_quarter"
    ):
        if field_name in row:
            value = _source_int(row[field_name], field_name)
        else:
            value = None
        values[field_name] = value  # type: ignore[assignment]
    return TwmdValuationObservation(
        instrument_id=instrument_id,
        symbol=_source_string(row.get("symbol"), "symbol", required=True) or "",
        trade_date=_validate_source_date(row.get("trade_date"), "trade_date"),
        company_name=_source_string(row.get("company_name"), "company_name", required=True) or "",
        close_price=values["close_price"],
        pe_ratio=values["pe_ratio"],
        pb_ratio=values["pb_ratio"],
        dividend_yield_pct=values["dividend_yield_pct"],
        dividend_per_share=values["dividend_per_share"],
        dividend_per_share_currency=_source_string(
            row.get("dividend_per_share_currency"), "dividend_per_share_currency"
        ),
        dividend_reference_year=values["dividend_reference_year"],  # type: ignore[arg-type]
        financial_reference_year=values["financial_reference_year"],  # type: ignore[arg-type]
        financial_reference_quarter=values["financial_reference_quarter"],  # type: ignore[arg-type]
        source_contract=_source_string(row.get("source_contract"), "source_contract"),
        source_url=_source_string(row.get("source_url"), "source_url"),
        request_scope=_source_string(row.get("request_scope"), "request_scope"),
        received_at_utc=_source_string(row.get("received_at_utc"), "received_at_utc"),
        payload_sha256=_source_string(row.get("payload_sha256"), "payload_sha256"),
        capture_id=_source_string(row.get("capture_id"), "capture_id"),
        revision=_source_int(row.get("revision"), "revision"),
    )


def _flow_coverage(row, is_tpex: bool) -> InstitutionalFlowCoverage:
    if not isinstance(row, dict):
        raise ValueError("flow coverage entries must be objects")
    status = _source_string(row.get("status"), "coverage.status", required=True) or ""
    allowed_statuses = {"AVAILABLE", "MISSING"} if is_tpex else {"AVAILABLE", "MISSING", "EMPTY"}
    if status not in allowed_statuses:
        raise ValueError("coverage.status has an unknown value")
    selected = _source_string(
        row.get("selected_instrument_presence"), "selected_instrument_presence"
    )
    if selected is not None and selected not in {"present", "absent", "missing"}:
        raise ValueError("selected_instrument_presence has an unknown value")
    record_count = _source_int(row.get("record_count"), "record_count")
    if record_count is None or record_count < 0:
        raise ValueError("record_count must be a non-negative integer")
    if status != "AVAILABLE" and record_count != 0:
        raise ValueError("non-AVAILABLE coverage cannot contain records")
    if is_tpex:
        if ((status == "AVAILABLE" and selected not in {"present", "absent"})
                or (status == "MISSING" and selected != "missing")):
            raise ValueError("selected presence conflicts with TPEx coverage")
        return InstitutionalFlowCoverage(
            trade_date=_validate_source_date(row.get("trade_date"), "coverage.trade_date"),
            status=status,
            record_count=record_count,
            selected_instrument_presence=selected,
            received_at_utc=_source_string(row.get("received_at_utc"), "received_at_utc"),
            capture_id=_source_string(row.get("capture_id"), "capture_id"),
            source_contract=_source_string(row.get("source_contract"), "source_contract"),
            source_url=_source_string(row.get("source_url"), "source_url"),
            request_scope=_source_string(row.get("request_scope"), "request_scope"),
            payload_sha256=_source_string(row.get("payload_sha256"), "payload_sha256"),
        )
    return InstitutionalFlowCoverage(
        trade_date=_validate_source_date(row.get("trade_date"), "coverage.trade_date"),
        status=status,
        record_count=record_count,
        acquired_at=_source_string(row.get("acquired_at"), "acquired_at"),
        sha256=_source_string(row.get("sha256"), "sha256"),
    )


def _flow_observation(
    row, instrument_id: str, source_contract: str, native_unit: str, is_tpex: bool
) -> InstitutionalFlowObservation:
    if not isinstance(row, dict):
        raise ValueError("flow rows must be objects")
    if row.get("instrument_id") != instrument_id:
        raise ValueError("flow row instrument_id does not match the request")
    fields = _TPEX_FLOW_FIELDS if is_tpex else _TWSE_FLOW_FIELDS
    values: dict[str, int | None] = {}
    for field_name in fields:
        if field_name not in row:
            raise ValueError(f"flow row is missing {field_name}")
        value = _source_int(row[field_name], field_name)
        if is_tpex and value is None:
            raise ValueError(f"TPEx flow field {field_name} must be an integer")
        values[field_name] = value
    row_contract = _source_string(row.get("source_contract"), "source_contract") or source_contract
    return InstitutionalFlowObservation(
        instrument_id=instrument_id,
        symbol=_source_string(row.get("symbol"), "symbol", required=True) or "",
        name=(
            _source_string(row.get("company_name"), "company_name", required=True)
            if is_tpex else _source_string(row.get("name"), "name", required=True)
        ) or "",
        trade_date=_validate_source_date(row.get("trade_date"), "trade_date"),
        native_unit=native_unit,
        native_values=values,
        source_contract=row_contract,
        source_url=_source_string(row.get("source_url"), "source_url"),
        request_scope=_source_string(row.get("request_scope"), "request_scope"),
        first_observed_at=_source_string(row.get("first_observed_at"), "first_observed_at"),
        acquired_at=_source_string(row.get("acquired_at"), "acquired_at"),
        received_at_utc=_source_string(row.get("received_at_utc"), "received_at_utc"),
        payload_sha256=_source_string(row.get("payload_sha256"), "payload_sha256"),
        capture_id=_source_string(row.get("capture_id"), "capture_id"),
        revision=_source_int(row.get("revision"), "revision"),
    )


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


class TwmdClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = (config.get("base_url") or "http://127.0.0.1:8000").rstrip("/")

    def get(self, path: str, **params):
        token = self.config.get("token")
        return market_get(
            f"{self.base_url}/api/v1/{path}", host_key="twmd", params=params or None,
            headers={"Authorization": f"Bearer {token}"} if token else None,
            timeout=float(self.config.get("timeout_sec") or 5), retries=1, parse="json",
            proxy=self.config.get("proxy"), log_label="twmd",
        )

    def get_response(self, path: str, **params) -> tuple[object, dict[str, str]]:
        """Read JSON while retaining HTTP headers and surfacing every failure."""
        token = self.config.get("token")
        try:
            response = market_get(
                f"{self.base_url}/api/v1/{path}",
                host_key="twmd",
                params=params or None,
                headers={"Authorization": f"Bearer {token}"} if token else None,
                timeout=float(self.config.get("timeout_sec") or 5),
                retries=1,
                parse="json",
                proxy=self.config.get("proxy"),
                log_label="twmd",
                raise_for_status=False,
                include_response=True,
                raise_on_error=True,
            )
        except MarketHttpError as exc:
            raise TwmdReadError(f"twmd GET /api/v1/{path} transport failed") from exc
        if not isinstance(response, MarketHttpResponse):
            raise TwmdReadError(f"twmd GET /api/v1/{path} returned no response")
        if response.status_code < 200 or response.status_code >= 300:
            record_error(f"twmd GET /api/v1/{path}: HTTP {response.status_code}")
            raise TwmdReadError(
                f"twmd GET /api/v1/{path} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.data, response.headers

    def _canonical_id(self, symbol: Symbol | str) -> str:
        parsed = symbol if isinstance(symbol, Symbol) else Symbol.parse(symbol, "TW")
        if parsed.market.value != "TW":
            raise ValueError("twmd research reads require a Taiwan symbol")
        if parsed.venue:
            return parsed.identity
        payload, _ = self.get_response("instruments")
        if not isinstance(payload, list):
            raise TwmdReadError("twmd instruments response must be a list")
        matches = [
            row for row in payload
            if isinstance(row, dict) and row.get("symbol") == parsed.code
            and row.get("venue") in {"TWSE", "TPEX"}
        ]
        active = [row for row in matches if row.get("is_active") is True]
        identities = {row.get("instrument_id") for row in (active or matches)}
        if len(identities) != 1:
            raise ValueError(
                f"Taiwan symbol {parsed.code} needs an explicit TWSE:/TPEX: venue"
            )
        identity = next(iter(identities))
        if not isinstance(identity, str):
            raise TwmdReadError("twmd instruments response has an invalid instrument_id")
        return identity

    def valuation_history(
        self,
        symbol: Symbol | str,
        start_date: date | str,
        end_date: date | str,
        *,
        today_taipei: date | None = None,
    ) -> TwmdValuationRead:
        start, end = _query_bounds(
            {"start_date": start_date, "end_date": end_date}, today_taipei=today_taipei
        )
        instrument_id = self._canonical_id(symbol)
        if instrument_id.startswith("TPEX:"):
            if start < date(2024, 1, 1):
                raise ValueError("TPEx valuations begin on 2024-01-01")
            if (end - start).days + 1 > 366:
                raise ValueError("TPEx valuation reads are limited to 366 calendar days")
        payload, response_headers = self.get_response(
            "valuations", instrument_id=instrument_id,
            start=start.isoformat(), end=end.isoformat(),
        )
        if not isinstance(payload, list):
            raise TwmdReadError("twmd valuations response must be a list")
        try:
            rows = [_valuation_observation(row, instrument_id) for row in payload]
            row_dates = [date.fromisoformat(row.trade_date) for row in rows]
            if any(day < start or day > end for day in row_dates):
                raise ValueError("valuation response contains a row outside the requested range")
            if len(set(row_dates)) != len(row_dates):
                raise ValueError("valuation response contains duplicate trade dates")
            schema_ready = _header_bool(response_headers.get("x-twmd-schema-ready"), "X-TWMD-Schema-Ready")
            coverage_header = response_headers.get("x-twmd-coverage")
            available, missing, selected = _valuation_header_coverage(coverage_header)
            if instrument_id.startswith("TPEX:") and (schema_ready is None or coverage_header is None):
                raise ValueError("TPEx valuation response is missing coverage headers")
            if instrument_id.startswith("TPEX:"):
                if available + missing != (end - start).days + 1:
                    raise ValueError("TPEx valuation coverage counts do not match the requested range")
                if len(rows) > available or (schema_ready is False and available):
                    raise ValueError("TPEx valuation rows conflict with dataset coverage")
                expected_presence = "present" if rows else "absent" if available else "missing"
                if selected != expected_presence:
                    raise ValueError("TPEx valuation rows conflict with selected presence")
            if instrument_id.startswith("TPEX:") and (
                selected == "present" and not rows or selected in {"absent", "missing"} and rows
            ):
                raise ValueError("TPEx valuation data conflicts with selected presence header")
        except (TypeError, ValueError) as exc:
            raise TwmdReadError(f"invalid twmd valuation response: {exc}") from exc

        if rows:
            partial = bool(missing or (available is not None and len(rows) < available))
            status = "partial" if partial else "available"
            reason = "some_requested_dates_missing_or_absent" if partial else "selected_record_present"
            selected_presence = "present"
        elif instrument_id.startswith("TWSE:"):
            status, reason, selected_presence = "unknown", "coverage_not_returned", "unknown"
        elif schema_ready is False or (available == 0 and missing and selected == "missing"):
            status, reason, selected_presence = "missing", "coverage_missing", "missing"
        elif available and selected == "absent":
            status = "partial" if missing else "absent"
            reason = "some_requested_dates_missing" if missing else "issuer_absent_from_available_report"
            selected_presence = "absent"
        elif available:
            status, reason, selected_presence = "unknown", "selected_presence_unreported", "unknown"
        else:
            status, reason, selected_presence = "missing", "coverage_missing", "missing"
        return TwmdValuationRead(
            instrument_id=instrument_id,
            endpoint=_VALUATION_ENDPOINT,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            data=rows,
            status=status,
            reason=reason,
            schema_ready=schema_ready,
            coverage_header=coverage_header,
            selected_instrument_presence=selected_presence,
            response_headers=response_headers,
        )

    def institutional_flows(
        self,
        symbol: Symbol | str,
        start_date: date | str,
        end_date: date | str,
        *,
        today_taipei: date | None = None,
    ) -> InstitutionalFlowRead:
        start, end = _query_bounds(
            {"start_date": start_date, "end_date": end_date}, today_taipei=today_taipei
        )
        instrument_id = self._canonical_id(symbol)
        if start < date(2024, 1, 1):
            raise ValueError("institutional flow history begins on 2024-01-01")
        if (end - start).days + 1 > 366:
            raise ValueError("institutional flow reads are limited to 366 calendar days")
        payload, response_headers = self.get_response(
            "institutional-flows", instrument_id=instrument_id,
            start_date=start.isoformat(), end_date=end.isoformat(),
        )
        if not isinstance(payload, dict):
            raise TwmdReadError("twmd institutional-flows response must be an object")
        try:
            if payload.get("instrument_id") != instrument_id:
                raise ValueError("response instrument_id does not match the request")
            source_contract = _source_string(payload.get("source_contract"), "source_contract", required=True)
            native_unit = _source_string(payload.get("native_unit"), "native_unit", required=True)
            if native_unit != "shares":
                raise ValueError("institutional-flow native_unit must be shares")
            schema_ready = payload.get("schema_ready")
            if not isinstance(schema_ready, bool):
                raise ValueError("schema_ready must be boolean")
            raw_coverage, raw_data = payload.get("coverage"), payload.get("data")
            if not isinstance(raw_coverage, list) or not isinstance(raw_data, list):
                raise ValueError("coverage and data must be lists")
            is_tpex = instrument_id.startswith("TPEX:")
            coverage = [_flow_coverage(row, is_tpex) for row in raw_coverage]
            observations = [
                _flow_observation(row, instrument_id, source_contract, native_unit, is_tpex)
                for row in raw_data
            ]
            requested_dates = [
                (start + timedelta(days=offset)).isoformat()
                for offset in range((end - start).days + 1)
            ]
            coverage_dates = [row.trade_date for row in coverage]
            if len(set(coverage_dates)) != len(coverage_dates) or sorted(coverage_dates) != requested_dates:
                raise ValueError("flow coverage must contain exactly one entry for each requested date")
            observed_dates = [row.trade_date for row in observations]
            if len(set(observed_dates)) != len(observed_dates):
                raise ValueError("flow response contains duplicate instrument rows")
            if any(day not in set(requested_dates) for day in observed_dates):
                raise ValueError("flow response contains a row outside the requested range")
            coverage_by_date = {row.trade_date: row for row in coverage}
            if not schema_ready and (observations or any(row.status != "MISSING" for row in coverage)):
                raise ValueError("flow data conflicts with missing schema")
            for row in observations:
                row_coverage = coverage_by_date[row.trade_date]
                if row_coverage.status.upper() != "AVAILABLE":
                    raise ValueError("flow observation has no AVAILABLE date coverage")
                if is_tpex and row_coverage.selected_instrument_presence != "present":
                    raise ValueError("TPEx flow observation conflicts with selected presence")
            if is_tpex and any(
                row.selected_instrument_presence == "present" and row.trade_date not in observed_dates
                for row in coverage
            ):
                raise ValueError("TPEx selected presence has no matching observation")
            if not is_tpex:
                present_dates = set(observed_dates)
                coverage = [
                    replace(
                        row,
                        selected_instrument_presence=(
                            "present" if row.trade_date in present_dates else "absent"
                        ) if row.status == "AVAILABLE" else "missing" if row.status == "MISSING" else None,
                    )
                    for row in coverage
                ]
            request_scope = _source_string(payload.get("request_scope"), "request_scope")
        except (TypeError, ValueError) as exc:
            raise TwmdReadError(f"invalid twmd institutional-flow response: {exc}") from exc

        statuses = {row.status.upper() for row in coverage}
        has_missing = "MISSING" in statuses or not schema_ready
        has_selected_absence = any(
            row.status.upper() == "AVAILABLE"
            and row.selected_instrument_presence in {"absent", "missing"}
            for row in coverage
        )
        if observations:
            status = "partial" if has_missing or has_selected_absence else "available"
            reason = (
                "some_requested_dates_missing" if has_missing
                else "some_requested_dates_absent" if has_selected_absence
                else "selected_record_present"
            )
        elif has_missing and statuses.difference({"MISSING"}):
            status, reason = "partial", "some_requested_dates_missing"
        elif has_selected_absence:
            status, reason = "absent", "issuer_absent_from_available_report"
        elif "EMPTY" in statuses:
            status, reason = "empty", "source_report_explicitly_no_data"
        elif not schema_ready or statuses and statuses <= {"MISSING"}:
            status, reason = "missing", "coverage_missing"
        else:
            status, reason = "unknown", "selected_presence_unreported"
        return InstitutionalFlowRead(
            instrument_id=instrument_id,
            endpoint=_FLOW_ENDPOINT,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            source_contract=source_contract,
            native_unit=native_unit,
            schema_ready=schema_ready,
            coverage=coverage,
            data=observations,
            status=status,
            reason=reason,
            request_scope=request_scope,
            response_headers=response_headers,
        )

    def instruments(self) -> list[dict]:
        key = (self.base_url, self.config.get("token"))
        cached = _catalog_cache.get(key)
        if cached is not None:
            return cached
        rows = self.get("instruments")
        if not isinstance(rows, list):
            return []
        rows = [row for row in rows if row.get("venue") in {"TWSE", "TPEX"}]
        if rows:
            _catalog_cache.set(key, rows)
        return rows

    def resolve(self, symbol: Symbol) -> str:
        if symbol.venue:
            return symbol.identity
        matches = [row for row in self.instruments() if row.get("symbol") == symbol.code]
        active = [row for row in matches if row.get("is_active")]
        matches = active or matches
        identities = {row["instrument_id"] for row in matches}
        if len(identities) != 1:
            raise ValueError(f"Taiwan symbol {symbol.code} needs an explicit TWSE:/TPEX: venue")
        return identities.pop()

    def bars(self, symbol: Symbol, *, timeframe="day", limit=120, **params) -> dict:
        payload = self.get("bars", instrument_id=self.resolve(symbol), timeframe=timeframe,
                           limit=limit, **params)
        return payload if isinstance(payload, dict) else {}


class TwmdQuoteVendor(QuoteVendor):
    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        client = TwmdClient(config)
        resolved = {}
        out = []
        for symbol in symbols:
            try:
                resolved[symbol.identity] = client.resolve(symbol)
            except ValueError as exc:
                out.append(Quote(symbol=symbol.identity, market="TW", current_price=None,
                                 timestamp=None, availability="unknown_instrument",
                                 usable_for_trading=False, freshness={"status": "unknown", "reason": str(exc)}))
        ids = list(dict.fromkeys(resolved.values()))
        quotes = {}
        for offset in range(0, len(ids), 100):
            payload = client.get("quotes", instrument_ids=",".join(ids[offset:offset + 100]),
                                 include_eod="true") or {}
            for row in payload.get("quotes", []):
                quotes[row["instrument_id"]] = row
        for requested, instrument_id in resolved.items():
            live = quotes.get(instrument_id) or {}
            fallback = live.get("eod_fallback")
            has_live = number(live.get("last_price")) is not None
            price = live if has_live else (fallback or {})
            kind = "live" if has_live else ("eod" if fallback else None)
            timestamp = None
            if has_live and live.get("observed_at"):
                try:
                    timestamp = datetime.fromisoformat(live["observed_at"])
                except ValueError:
                    pass
            freshness = live.get("freshness") or {}
            previous = price.get("previous_observation") or {}
            out.append(Quote(
                symbol=requested, market="TW", instrument_id=instrument_id,
                venue=instrument_id.split(":")[0], name=live.get("name") or price.get("name") or "",
                current_price=number(price.get("last_price") if has_live else price.get("close")),
                prev_close=number(live.get("previous_close") if has_live else previous.get("close")),
                reference_price=number(live.get("reference_price")),
                open_price=number(price.get("open")), high_price=number(price.get("high")),
                low_price=number(price.get("low")), change_amount=number(price.get("change")),
                change_pct=number(price.get("change_pct")), volume=number(price.get("volume")),
                turnover=number(price.get("turnover") if has_live else price.get("value")),
                timestamp=timestamp, price_kind=kind, provider=price.get("provider"),
                trade_date=price.get("trade_date"), change_basis=price.get("change_basis"),
                adjustment_mode=price.get("adjustment_mode"), availability=live.get("availability", "unavailable"),
                freshness=freshness, collection_health=live.get("collection_health") or {},
                usable_for_trading=bool(has_live and timestamp and live.get("availability") == "available"
                                        and freshness.get("status") == "fresh" and freshness.get("usable_for_trading")),
                units=price.get("units") or {"currency": "TWD", "volume": "shares"},
                volume_semantics=live.get("volume_semantics") if has_live else "daily",
                eod_fallback=fallback,
            ))
        return out


class TwmdFundamentalsVendor(FundamentalsVendor):
    """Official TW valuation rows projected into compatible legacy fields."""

    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict):
        from marketdata.types import Fundamentals

        if not symbols:
            return []
        today_taipei = _configured_today(config) or datetime.now(_TAIPEI).date()
        start, end = _query_bounds(config, today_taipei=today_taipei)
        client = TwmdClient(config)
        out = []
        for symbol in symbols:
            read = client.valuation_history(symbol, start, end, today_taipei=today_taipei)
            latest = max(read.data, key=lambda row: row.trade_date, default=None)
            evidence = {
                "provider": "twmd",
                "source": "official_twmd",
                "endpoint": read.endpoint,
                "selectors": {"instrument_id": read.instrument_id,
                              "start": read.start_date, "end": read.end_date},
                "status": read.status,
                "reason": read.reason,
                "schema_ready": read.schema_ready,
                "coverage": read.coverage_header,
                "selected_instrument_presence": read.selected_instrument_presence,
                "units": {
                    "close_price": "TWD per share",
                    "pe_ratio": "provider-reported multiple; period semantics unspecified",
                    "pb_ratio": "provider-reported multiple",
                    "dividend_yield_pct": "percent",
                    "dividend_per_share": (
                        latest.dividend_per_share_currency or "source currency unspecified"
                        if latest else "source currency unspecified"
                    ),
                },
                "response_headers": read.response_headers,
                "rows": [asdict(row) for row in read.data],
            }
            out.append(Fundamentals(
                symbol=read.instrument_id,
                market=symbol.market.value,
                name=latest.company_name if latest else "",
                # The source calls this generic PE. It does not promise TTM or static semantics.
                pe_ttm=None,
                pe_static=None,
                pe_ratio=number(latest.pe_ratio) if latest else None,
                pb=number(latest.pb_ratio) if latest else None,
                dividend_yield=number(latest.dividend_yield_pct) if latest else None,
                report_date="",
                valuation_trade_date=latest.trade_date if latest else None,
                valuation_evidence=evidence,
            ))
        return out


class TwmdCapitalFlowVendor(CapitalFlowVendor):
    """Official TWSE/TPEx institutional shares mapped to the old flow surface."""

    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict):
        from marketdata.types import CapitalFlow

        if not symbols:
            return []
        today_taipei = _configured_today(config) or datetime.now(_TAIPEI).date()
        start, end = _query_bounds(config, today_taipei=today_taipei)
        client = TwmdClient(config)
        out = []
        for symbol in symbols:
            read = client.institutional_flows(
                symbol, start, end, today_taipei=today_taipei
            )
            latest = max(read.data, key=lambda row: row.trade_date, default=None)
            native = latest.native_values if latest else {}
            if read.instrument_id.startswith("TPEX:"):
                foreign = native.get("foreign_ex_dealer_net_shares")
                trust = native.get("investment_trust_net_shares")
                dealer = native.get("combined_dealer_net_shares")
            else:
                foreign = native.get("foreign_non_dealer_net_shares")
                trust = native.get("investment_trust_net_shares")
                dealer = native.get("dealer_reported_net_shares")
            evidence = {
                "provider": "twmd",
                "source": "official_twmd",
                "endpoint": read.endpoint,
                "selectors": {"instrument_id": read.instrument_id,
                              "start_date": read.start_date, "end_date": read.end_date},
                "source_contract": read.source_contract,
                "native_unit": read.native_unit,
                "request_scope": read.request_scope,
                "schema_ready": read.schema_ready,
                "status": read.status,
                "reason": read.reason,
                "coverage": [asdict(row) for row in read.coverage],
                "rows": [asdict(row) for row in read.data],
                "response_headers": read.response_headers,
            }
            out.append(CapitalFlow(
                symbol=read.instrument_id,
                name=latest.name if latest else "",
                flow_kind="institutional_shares",
                unit="shares",
                trade_date=latest.trade_date if latest else None,
                foreign_net_shares=foreign,
                trust_net_shares=trust,
                dealer_net_shares=dealer,
                institutional_net_shares=native.get("total_institutional_net_shares"),
                # The endpoint returns calendar-date coverage, not a trading calendar.
                institutional_net_5d_shares=None,
                native_components=native,
                evidence=evidence,
            ))
        return out


class TwmdKlineVendor(KlineVendor):
    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        payload = TwmdClient(config).bars(symbols[0], limit=min(1000, int(config.get("days") or 120)))
        return _observed_bars(payload)


class TwmdIntradayVendor(KlineVendor):
    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        params = {key: config[key] for key in ("start_date", "end_date") if config.get(key)}
        payload = TwmdClient(config).bars(symbols[0], timeframe=config.get("timeframe", "1m"),
                                         limit=int(config.get("limit") or 270), **params)
        return _observed_bars(payload)


def _observed_bars(payload: dict) -> list[Bar]:
    out = []
    for row in payload.get("bars", []):
        values = [number(row.get(key)) for key in ("open", "close", "high", "low", "volume")]
        if any(value is None for value in values) or row.get("finalized") is False:
            continue
        if row.get("status", "observed") != "observed":
            continue
        out.append(Bar(date=row.get("timestamp") or row["trade_date"], open=values[0], close=values[1],
                       high=values[2], low=values[3], volume=values[4], provider=payload.get("provider"),
                       adjustment_mode=payload.get("adjustment_mode"), volume_unit="shares"))
    return out
