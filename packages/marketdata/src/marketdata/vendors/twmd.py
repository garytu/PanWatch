"""tw-market-data's stored prices, live quotes and instrument identity contracts."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

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
    TwmdCompanyProfile,
    TwmdCompanyProfileRead,
    TwmdCompanyProfileSnapshot,
    TwmdMonthlyRevenueCoverage,
    TwmdMonthlyRevenueMonth,
    TwmdMonthlyRevenueRead,
    TwmdMonthlyRevenueRow,
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
_COMPANY_PROFILE_ENDPOINT = "/api/v1/company-profiles"
_MONTHLY_REVENUE_ENDPOINT = "/api/v1/monthly-revenues"
_MONTHLY_REVENUE_FLOOR = date(2024, 1, 1)
_MONTHLY_REVENUE_MAX_MONTHS = 120
_RESEARCH_READ_CACHE_TTL_SEC = 300.0
_company_profile_cache = TTLCache(default_ttl_sec=_RESEARCH_READ_CACHE_TTL_SEC)
_monthly_revenue_cache = TTLCache(default_ttl_sec=_RESEARCH_READ_CACHE_TTL_SEC)
_PROFILE_CONTRACTS = {
    "TWSE": "twse_openapi_t187ap03_L/v1",
    "TPEX": "tpex.openapi.mopsfin_t187ap03_O/v1.0.0",
}
_REVENUE_CONTRACTS = {
    "twse": "twse_openapi_t187ap05_L/v1",
    "mops": "mops_t21_sii_monthly_revenue/v1",
    "tpex": "tpex.openapi.mopsfin_t187ap05_O/v1.0.0",
}

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


def _source_datetime(value, field_name: str, *, required: bool = False) -> str | None:
    raw = _source_string(value, field_name, required=required)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return raw


def _source_decimal_string(value, field_name: str, *, required: bool = False) -> str | None:
    raw = _source_string(value, field_name, required=required)
    if raw is None:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a decimal string or null") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return raw


def _source_units(value, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError(f"{field_name} must be a string mapping")
    return dict(value)


def _required_string_field(value: dict, field_name: str) -> str:
    key = field_name.rsplit(".", 1)[-1]
    if key not in value or not isinstance(value[key], str):
        raise ValueError(f"{field_name} must be a string")
    return value[key]


def _validate_revenue_source(source: str, contract: str, instrument_id: str) -> None:
    allowed = {"tpex"} if instrument_id.startswith("TPEX:") else {"twse", "mops"}
    if source not in allowed or _REVENUE_CONTRACTS.get(source) != contract:
        raise ValueError("monthly-revenue source contract does not match the selected venue")


def _validate_month(value: str, field_name: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError(f"{field_name} must use YYYY-MM")
    try:
        month = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid calendar month") from exc
    if month.isoformat() != f"{value}-01":
        raise ValueError(f"{field_name} must use YYYY-MM")
    return month


def _months_inclusive(start: date, end: date) -> list[date]:
    months: list[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return months


def _profile_snapshot(value) -> TwmdCompanyProfileSnapshot | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("latest_snapshot must be an object or null")
    required = {
        "capture_id", "report_date", "received_at_utc", "source_contract", "payload_sha256",
        "row_count", "coverage_status",
    }
    if required.difference(value):
        raise ValueError(f"latest_snapshot is missing {sorted(required.difference(value))[0]}")
    if value.get("coverage_status") != "AVAILABLE":
        raise ValueError("latest_snapshot coverage_status must be AVAILABLE")
    row_count = _source_int(value.get("row_count"), "latest_snapshot.row_count")
    if row_count is None or row_count < 0:
        raise ValueError("latest_snapshot.row_count must be a non-negative integer")
    return TwmdCompanyProfileSnapshot(
        capture_id=_source_string(value.get("capture_id"), "latest_snapshot.capture_id", required=True) or "",
        report_date=_validate_source_date(value.get("report_date"), "latest_snapshot.report_date"),
        received_at_utc=_source_datetime(
            value.get("received_at_utc"), "latest_snapshot.received_at_utc", required=True
        ) or "",
        source_contract=_source_string(
            value.get("source_contract"), "latest_snapshot.source_contract", required=True
        ) or "",
        payload_sha256=_source_string(
            value.get("payload_sha256"), "latest_snapshot.payload_sha256", required=True
        ) or "",
        row_count=row_count,
        coverage_status="AVAILABLE",
    )


def _company_profile(value, instrument_id: str) -> TwmdCompanyProfile | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("profile must be an object or null")
    if value.get("instrument_id") != instrument_id:
        raise ValueError("profile instrument_id does not match the request")
    required = {
        "company_name", "industry_code", "established_on", "listed_on", "par_value_raw",
        "par_value_amount", "par_value_currency", "par_value_meaning", "paid_in_capital",
        "issued_share_count", "financial_report_type_code", "source_content_hash", "report_date",
        "original_received_at_utc", "source_contract", "payload_sha256", "revision",
        "share_semantics", "qualification", "qualification_reason", "latest_snapshot_report_date",
        "latest_snapshot_received_at_utc", "latest_snapshot_presence", "snapshot_capture_id",
    }
    missing = required.difference(value)
    if missing:
        raise ValueError(f"profile is missing {sorted(missing)[0]}")
    issued_share_count = _source_int(value.get("issued_share_count"), "profile.issued_share_count")
    revision = _source_int(value.get("revision"), "profile.revision")
    if issued_share_count is None or issued_share_count < 0:
        raise ValueError("profile.issued_share_count is outside its valid range")
    if revision is None or revision < 1:
        raise ValueError("profile.revision is outside its valid range")
    private_shares = _source_int(value.get("private_share_count"), "profile.private_share_count")
    preferred_shares = _source_int(value.get("preferred_share_count"), "profile.preferred_share_count")
    if any(item is not None and item < 0 for item in (private_shares, preferred_shares)):
        raise ValueError("profile share counts must be non-negative")
    latest_presence = _source_string(
        value.get("latest_snapshot_presence"), "profile.latest_snapshot_presence", required=True
    ) or ""
    if latest_presence not in {"present", "absent"}:
        raise ValueError("profile.latest_snapshot_presence must be present or absent")
    latest_report = value.get("latest_snapshot_report_date")
    latest_received = value.get("latest_snapshot_received_at_utc")
    latest_report_date = (
        _validate_source_date(latest_report, "profile.latest_snapshot_report_date")
        if latest_report is not None else None
    )
    latest_received_at = _source_datetime(
        latest_received, "profile.latest_snapshot_received_at_utc"
    )
    return TwmdCompanyProfile(
        instrument_id=instrument_id,
        company_name=_source_string(value.get("company_name"), "profile.company_name", required=True) or "",
        industry_code=_source_string(value.get("industry_code"), "profile.industry_code", required=True) or "",
        established_on=_validate_source_date(value.get("established_on"), "profile.established_on"),
        listed_on=_validate_source_date(value.get("listed_on"), "profile.listed_on"),
        par_value_raw=_source_string(value.get("par_value_raw"), "profile.par_value_raw", required=True) or "",
        par_value_amount=_source_decimal_string(value.get("par_value_amount"), "profile.par_value_amount"),
        par_value_currency=_source_string(value.get("par_value_currency"), "profile.par_value_currency"),
        par_value_meaning=_source_string(value.get("par_value_meaning"), "profile.par_value_meaning", required=True) or "",
        paid_in_capital=_source_decimal_string(value.get("paid_in_capital"), "profile.paid_in_capital", required=True) or "",
        issued_share_count=issued_share_count,
        private_share_count=private_shares,
        preferred_share_count=preferred_shares,
        financial_report_type_code=_source_string(
            value.get("financial_report_type_code"), "profile.financial_report_type_code", required=True
        ) or "",
        source_content_hash=_source_string(
            value.get("source_content_hash"), "profile.source_content_hash", required=True
        ) or "",
        report_date=_validate_source_date(value.get("report_date"), "profile.report_date"),
        original_received_at_utc=_source_datetime(
            value.get("original_received_at_utc"), "profile.original_received_at_utc", required=True
        ) or "",
        source_contract=_source_string(value.get("source_contract"), "profile.source_contract", required=True) or "",
        payload_sha256=_source_string(value.get("payload_sha256"), "profile.payload_sha256", required=True) or "",
        revision=revision,
        share_semantics=_source_string(value.get("share_semantics"), "profile.share_semantics", required=True) or "",
        qualification=_source_string(value.get("qualification"), "profile.qualification", required=True) or "",
        qualification_reason=_source_string(
            value.get("qualification_reason"), "profile.qualification_reason", required=True
        ) or "",
        latest_snapshot_report_date=latest_report_date,
        latest_snapshot_received_at_utc=latest_received_at,
        latest_snapshot_presence=latest_presence,
        snapshot_capture_id=_source_string(value.get("snapshot_capture_id"), "profile.snapshot_capture_id"),
    )


def _monthly_revenue_row(value, instrument_id: str, month: str) -> TwmdMonthlyRevenueRow | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("monthly-revenue row must be an object or null")
    if value.get("symbol") != instrument_id.split(":", 1)[1]:
        raise ValueError("monthly-revenue row symbol does not match the request")
    row_month = _validate_source_date(value.get("data_month"), "row.data_month")
    if row_month != month:
        raise ValueError("monthly-revenue row data_month does not match its month entry")
    numeric_fields = (
        "monthly_revenue", "previous_month_revenue", "year_ago_monthly_revenue",
        "month_over_month_pct", "year_over_year_pct", "cumulative_revenue",
        "year_ago_cumulative_revenue", "cumulative_yoy_pct",
    )
    missing_numeric = set(numeric_fields).difference(value)
    if missing_numeric:
        raise ValueError(f"row is missing {sorted(missing_numeric)[0]}")
    numbers = {
        name: _source_decimal_string(value.get(name), f"row.{name}")
        for name in numeric_fields
    }
    revision = _source_int(value.get("revision"), "row.revision")
    if revision is None or revision < 1:
        raise ValueError("row.revision must be a positive integer")
    return TwmdMonthlyRevenueRow(
        symbol=instrument_id.split(":", 1)[1],
        data_month=row_month,
        company_name=_source_string(value.get("company_name"), "row.company_name", required=True) or "",
        industry=_source_string(value.get("industry"), "row.industry", required=True) or "",
        **numbers,
        notes=_required_string_field(value, "row.notes"),
        content_hash=_source_string(value.get("content_hash"), "row.content_hash", required=True) or "",
        revision=revision,
        capture_id=_source_string(value.get("capture_id"), "row.capture_id", required=True) or "",
        source=_source_string(value.get("source"), "row.source", required=True) or "",
        source_contract=_source_string(value.get("source_contract"), "row.source_contract", required=True) or "",
        request_scope=_source_string(value.get("request_scope"), "row.request_scope", required=True) or "",
        source_url=_source_string(value.get("source_url"), "row.source_url", required=True) or "",
        acquisition_date=_validate_source_date(value.get("acquisition_date"), "row.acquisition_date"),
        received_at_utc=_source_datetime(value.get("received_at_utc"), "row.received_at_utc", required=True) or "",
        report_date=_validate_source_date(value.get("report_date"), "row.report_date"),
        payload_sha256=_source_string(value.get("payload_sha256"), "row.payload_sha256", required=True) or "",
    )


def _monthly_revenue_coverage(value, requested_months: set[str]) -> TwmdMonthlyRevenueCoverage:
    if not isinstance(value, dict):
        raise ValueError("monthly-revenue coverage entries must be objects")
    required = {
        "capture_id", "source", "data_month", "row_count", "source_contract", "request_scope",
        "source_url", "acquisition_date", "received_at_utc", "report_date", "payload_sha256",
        "selected_issuer_present",
    }
    if required.difference(value):
        raise ValueError(f"monthly-revenue coverage is missing {sorted(required.difference(value))[0]}")
    data_month = _validate_source_date(value.get("data_month"), "coverage.data_month")
    if data_month not in requested_months:
        raise ValueError("monthly-revenue coverage is outside the requested range")
    row_count = _source_int(value.get("row_count"), "coverage.row_count")
    if row_count is None or row_count < 0:
        raise ValueError("coverage.row_count must be a non-negative integer")
    selected_present = value.get("selected_issuer_present")
    if not isinstance(selected_present, bool):
        raise ValueError("coverage.selected_issuer_present must be boolean")
    return TwmdMonthlyRevenueCoverage(
        capture_id=_source_string(value.get("capture_id"), "coverage.capture_id", required=True) or "",
        source=_source_string(value.get("source"), "coverage.source", required=True) or "",
        data_month=data_month,
        row_count=row_count,
        source_contract=_source_string(value.get("source_contract"), "coverage.source_contract", required=True) or "",
        request_scope=_source_string(value.get("request_scope"), "coverage.request_scope", required=True) or "",
        source_url=_source_string(value.get("source_url"), "coverage.source_url", required=True) or "",
        acquisition_date=_validate_source_date(value.get("acquisition_date"), "coverage.acquisition_date"),
        received_at_utc=_source_datetime(
            value.get("received_at_utc"), "coverage.received_at_utc", required=True
        ) or "",
        report_date=_validate_source_date(value.get("report_date"), "coverage.report_date"),
        payload_sha256=_source_string(value.get("payload_sha256"), "coverage.payload_sha256", required=True) or "",
        selected_issuer_present=selected_present,
    )


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

    def get_response(
        self,
        path: str,
        *,
        timeout_sec: float | None = None,
        retries: int | None = None,
        **params,
    ) -> tuple[object, dict[str, str]]:
        """Read JSON while retaining HTTP headers and surfacing every failure."""
        token = self.config.get("token")
        timeout = self._timeout(timeout_sec if timeout_sec is not None else self.config.get("timeout_sec", 5))
        retry_count = 1 if retries is None else retries
        if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
            raise ValueError("twmd retries must be a non-negative integer")
        try:
            response = market_get(
                f"{self.base_url}/api/v1/{path}",
                host_key="twmd",
                params=params or None,
                headers={"Authorization": f"Bearer {token}"} if token else None,
                timeout=timeout,
                retries=retry_count,
                parse="json",
                proxy=self.config.get("proxy"),
                log_label="twmd",
                raise_for_status=False,
                include_response=True,
                raise_on_error=True,
            )
        except MarketHttpError as exc:
            cause: BaseException | None = exc
            reason_code = "transport_error"
            while cause is not None:
                if isinstance(cause, httpx.TimeoutException):
                    reason_code = "timeout"
                    break
                if isinstance(cause, json.JSONDecodeError):
                    reason_code = "invalid_response"
                    break
                cause = cause.__cause__
            raise TwmdReadError(
                f"twmd GET /api/v1/{path} transport failed",
                reason_code=reason_code,
            ) from exc
        if not isinstance(response, MarketHttpResponse):
            raise TwmdReadError(
                f"twmd GET /api/v1/{path} returned no response",
                reason_code="invalid_response",
            )
        if response.status_code < 200 or response.status_code >= 300:
            record_error(f"twmd GET /api/v1/{path}: HTTP {response.status_code}")
            raise TwmdReadError(
                f"twmd GET /api/v1/{path} returned HTTP {response.status_code}",
                status_code=response.status_code,
                reason_code=f"http_{response.status_code}",
            )
        return response.data, response.headers

    @staticmethod
    def _timeout(value) -> float:
        if isinstance(value, bool):
            raise ValueError("twmd timeout must be a finite positive number")
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("twmd timeout must be a finite positive number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("twmd timeout must be a finite positive number")
        return timeout

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

    def company_profile(self, symbol: Symbol | str) -> TwmdCompanyProfileRead:
        """Read one latest-only issuer profile; no historical selectors are accepted."""
        instrument_id = self._canonical_id(symbol)
        cache_key = (
            "twmd", self.base_url, self.config.get("token"), instrument_id,
            "company_profiles", "latest",
        )
        cached = _company_profile_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

        profile_timeout = self._timeout(self.config.get("profile_timeout_sec", 20))
        payload, _ = self.get_response(
            "company-profiles",
            instrument_id=instrument_id,
            timeout_sec=profile_timeout,
            retries=0,
        )
        if not isinstance(payload, dict):
            raise TwmdReadError(
                "twmd company-profiles response must be an object",
                reason_code="invalid_response",
            )
        try:
            required_response_fields = {
                "instrument_id", "source_contract", "schema_ready", "coverage_status",
                "latest_snapshot_presence", "qualification", "qualification_reason", "units",
                "latest_snapshot", "profile",
            }
            if required_response_fields.difference(payload):
                raise ValueError(
                    f"response is missing {sorted(required_response_fields.difference(payload))[0]}"
                )
            if payload.get("instrument_id") != instrument_id:
                raise ValueError("response instrument_id does not match the request")
            source_contract = _source_string(
                payload.get("source_contract"), "source_contract", required=True
            ) or ""
            schema_ready = payload.get("schema_ready")
            if not isinstance(schema_ready, bool):
                raise ValueError("schema_ready must be boolean")
            coverage_status = _source_string(
                payload.get("coverage_status"), "coverage_status", required=True
            ) or ""
            if coverage_status not in {"AVAILABLE", "MISSING"}:
                raise ValueError("coverage_status has an unknown value")
            latest_presence = _source_string(
                payload.get("latest_snapshot_presence"),
                "latest_snapshot_presence",
                required=True,
            ) or ""
            if latest_presence not in {"present", "absent", "missing"}:
                raise ValueError("latest_snapshot_presence has an unknown value")
            qualification = _source_string(
                payload.get("qualification"), "qualification", required=True
            ) or ""
            qualification_reason = _source_string(
                payload.get("qualification_reason"), "qualification_reason", required=True
            ) or ""
            units = _source_units(payload.get("units"), "units")
            snapshot = _profile_snapshot(payload.get("latest_snapshot"))
            profile = _company_profile(payload.get("profile"), instrument_id)
            expected_contract = _PROFILE_CONTRACTS[instrument_id.split(":", 1)[0]]
            if source_contract != expected_contract or any(
                item is not None and item.source_contract != expected_contract
                for item in (snapshot, profile)
            ):
                raise ValueError("profile source contract does not match the selected venue")
            if not schema_ready and (coverage_status != "MISSING" or snapshot is not None or profile is not None):
                raise ValueError("profile data conflicts with missing schema")
            if (snapshot is None) != (coverage_status == "MISSING"):
                raise ValueError("latest snapshot conflicts with coverage_status")
            if snapshot is None and latest_presence != "missing":
                raise ValueError("latest snapshot presence conflicts with missing coverage")
            if snapshot is not None and latest_presence == "missing":
                raise ValueError("latest snapshot presence conflicts with available coverage")
            if profile is not None and profile.latest_snapshot_presence != latest_presence:
                raise ValueError("profile presence conflicts with response presence")
            if latest_presence == "present" and profile is None:
                raise ValueError("present latest snapshot has no selected profile")
            if latest_presence == "missing" and profile is not None:
                raise ValueError("missing snapshot cannot contain a profile")
        except (TypeError, ValueError) as exc:
            raise TwmdReadError(
                f"invalid twmd company-profile response: {exc}",
                reason_code="invalid_response",
            ) from exc

        if qualification.startswith("unsupported_"):
            status, reason = "unsupported", qualification_reason
        elif coverage_status == "MISSING" or latest_presence == "missing":
            status, reason = "missing", "profile_snapshot_not_retained"
        elif latest_presence == "absent" and profile is not None:
            status, reason = "partial", "issuer_absent_from_latest_snapshot_retained_profile"
        elif latest_presence == "absent":
            status, reason = "absent", "issuer_absent_from_latest_snapshot"
        else:
            status, reason = "available", "selected_profile_present"
        read = TwmdCompanyProfileRead(
            instrument_id=instrument_id,
            endpoint=_COMPANY_PROFILE_ENDPOINT,
            source_contract=source_contract,
            schema_ready=schema_ready,
            coverage_status=coverage_status,
            latest_snapshot_presence=latest_presence,
            qualification=qualification,
            qualification_reason=qualification_reason,
            units=units,
            latest_snapshot=snapshot,
            profile=profile,
            status=status,
            reason=reason,
        )
        _company_profile_cache.set(cache_key, copy.deepcopy(read))
        return copy.deepcopy(read)

    def monthly_revenues(
        self,
        symbol: Symbol | str,
        start_month: str,
        end_month: str,
        *,
        today_taipei: date | None = None,
    ) -> TwmdMonthlyRevenueRead:
        """Read one bounded inclusive month range without filling missing months."""
        start = _validate_month(start_month, "start_month")
        end = _validate_month(end_month, "end_month")
        if start > end:
            raise ValueError("start_month must not be after end_month")
        if start < _MONTHLY_REVENUE_FLOOR:
            raise ValueError("monthly-revenue reads begin at 2024-01")
        if today_taipei is not None and (isinstance(today_taipei, datetime) or not isinstance(today_taipei, date)):
            raise TypeError("today_taipei must be a calendar date")
        today = today_taipei or datetime.now(_TAIPEI).date()
        current_month = today.replace(day=1)
        if end > current_month:
            raise ValueError("monthly-revenue reads cannot include a future Taipei month")
        month_count = (end.year - start.year) * 12 + end.month - start.month + 1
        if month_count > _MONTHLY_REVENUE_MAX_MONTHS:
            raise ValueError("monthly-revenue reads are limited to 120 months")
        requested_dates = _months_inclusive(start, end)

        instrument_id = self._canonical_id(symbol)
        start_value, end_value = start.strftime("%Y-%m"), end.strftime("%Y-%m")
        cache_key = (
            "twmd", self.base_url, self.config.get("token"), instrument_id,
            "monthly_revenues", start_value, end_value
        )
        cached = _monthly_revenue_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

        payload, _ = self.get_response(
            "monthly-revenues",
            instrument_id=instrument_id,
            start_month=start_value,
            end_month=end_value,
        )
        if not isinstance(payload, dict):
            raise TwmdReadError(
                "twmd monthly-revenues response must be an object",
                reason_code="invalid_response",
            )
        try:
            required_response_fields = {
                "instrument_id", "dataset", "start_month", "end_month", "schema_ready",
                "coverage_status", "qualification", "qualification_reason",
                "current_catalog_evidence", "units", "coverage", "months", "served_at",
            }
            if required_response_fields.difference(payload):
                raise ValueError(
                    f"response is missing {sorted(required_response_fields.difference(payload))[0]}"
                )
            if payload.get("instrument_id") != instrument_id:
                raise ValueError("response instrument_id does not match the request")
            if _validate_source_date(payload.get("start_month"), "start_month") != start.isoformat():
                raise ValueError("response start_month does not match the request")
            if _validate_source_date(payload.get("end_month"), "end_month") != end.isoformat():
                raise ValueError("response end_month does not match the request")
            dataset = _source_string(payload.get("dataset"), "dataset", required=True) or ""
            expected_dataset = (
                "tpex_monthly_revenue_latest" if instrument_id.startswith("TPEX:") else "monthly_revenue"
            )
            if dataset != expected_dataset:
                raise ValueError("dataset does not match the selected venue")
            schema_ready = payload.get("schema_ready")
            if not isinstance(schema_ready, bool):
                raise ValueError("schema_ready must be boolean")
            coverage_status = _source_string(
                payload.get("coverage_status"), "coverage_status", required=True
            ) or ""
            if coverage_status not in {"AVAILABLE", "MISSING"}:
                raise ValueError("coverage_status has an unknown value")
            qualification = _source_string(
                payload.get("qualification"), "qualification", required=True
            ) or ""
            qualification_reason = _source_string(
                payload.get("qualification_reason"), "qualification_reason", required=True
            ) or ""
            catalog = payload.get("current_catalog_evidence")
            if not isinstance(catalog, dict) or not isinstance(catalog.get("status"), str):
                raise ValueError("current_catalog_evidence must be an object with status")
            units = _source_units(payload.get("units"), "units")
            raw_coverage, raw_months = payload.get("coverage"), payload.get("months")
            if not isinstance(raw_coverage, list) or not isinstance(raw_months, list):
                raise ValueError("coverage and months must be lists")
            requested = {item.isoformat() for item in requested_dates}
            coverage = [_monthly_revenue_coverage(item, requested) for item in raw_coverage]
            for item in coverage:
                _validate_revenue_source(item.source, item.source_contract, instrument_id)
            coverage_keys = [(item.source, item.data_month) for item in coverage]
            if len(coverage_keys) != len(set(coverage_keys)):
                raise ValueError("monthly-revenue coverage repeats source and month")
            if coverage_status == "AVAILABLE" and not coverage:
                raise ValueError("AVAILABLE coverage requires at least one retained report")
            if coverage_status == "MISSING" and coverage:
                raise ValueError("MISSING coverage cannot contain retained reports")
            if not schema_ready and (coverage or coverage_status != "MISSING"):
                raise ValueError("monthly-revenue data conflicts with missing schema")
            if len(raw_months) != len(requested_dates):
                raise ValueError("months must contain exactly one entry per requested month")
            coverage_by_month: dict[str, list[TwmdMonthlyRevenueCoverage]] = {}
            for item in coverage:
                coverage_by_month.setdefault(item.data_month, []).append(item)
            months: list[TwmdMonthlyRevenueMonth] = []
            for raw_month, expected_month in zip(raw_months, requested_dates, strict=True):
                if not isinstance(raw_month, dict):
                    raise ValueError("monthly-revenue month entries must be objects")
                if {"data_month", "presence", "row"}.difference(raw_month):
                    raise ValueError("monthly-revenue month entry is missing a required field")
                month = _validate_source_date(raw_month.get("data_month"), "month.data_month")
                if month != expected_month.isoformat():
                    raise ValueError("months must be ordered and match the requested range")
                presence = _source_string(raw_month.get("presence"), "month.presence", required=True) or ""
                if presence not in {"present", "not_in_captured_report", "missing"}:
                    raise ValueError("monthly-revenue presence has an unknown value")
                month_coverage = coverage_by_month.get(month, [])
                expected_presence = (
                    "present" if any(item.selected_issuer_present for item in month_coverage)
                    else "not_in_captured_report" if month_coverage
                    else "missing"
                )
                if presence != expected_presence:
                    raise ValueError("monthly-revenue presence conflicts with source coverage")
                row = _monthly_revenue_row(raw_month.get("row"), instrument_id, month)
                if row is not None:
                    _validate_revenue_source(row.source, row.source_contract, instrument_id)
                if presence == "present" and row is None:
                    raise ValueError("present issuer coverage has no retained row")
                if presence == "missing" and row is not None:
                    raise ValueError("missing month coverage cannot contain a retained row")
                # The publisher can omit an issuer in a newer report while twmd
                # retains that issuer's older row. Keep both facts independently.
                months.append(TwmdMonthlyRevenueMonth(month, presence, row))
            served_at = _source_datetime(payload.get("served_at"), "served_at", required=True) or ""
        except (TypeError, ValueError) as exc:
            raise TwmdReadError(
                f"invalid twmd monthly-revenue response: {exc}",
                reason_code="invalid_response",
            ) from exc

        has_missing = any(item.presence == "missing" for item in months)
        has_absence = any(item.presence == "not_in_captured_report" for item in months)
        has_present = any(item.presence == "present" for item in months)
        has_retained_absent_row = any(
            item.presence == "not_in_captured_report" and item.row is not None
            for item in months
        )
        if qualification.startswith("unsupported_"):
            status, reason = "unsupported", qualification_reason
        elif coverage_status == "MISSING":
            status, reason = "missing", "monthly_revenue_coverage_missing"
        elif has_present:
            status = "partial" if has_missing or has_absence else "available"
            reason = (
                "some_requested_months_missing" if has_missing
                else "some_requested_months_absent" if has_absence
                else "selected_monthly_revenue_present"
            )
        elif has_retained_absent_row:
            status, reason = "partial", "retained_row_with_report_absence"
        elif has_missing and has_absence:
            status, reason = "partial", "some_requested_months_missing_or_absent"
        elif has_missing:
            status, reason = "missing", "requested_months_missing"
        elif has_absence:
            status, reason = "absent", "issuer_absent_from_captured_reports"
        else:
            status, reason = "unknown", "selected_presence_unreported"
        read = TwmdMonthlyRevenueRead(
            instrument_id=instrument_id,
            endpoint=_MONTHLY_REVENUE_ENDPOINT,
            dataset=dataset,
            start_month=start.isoformat(),
            end_month=end.isoformat(),
            schema_ready=schema_ready,
            coverage_status=coverage_status,
            qualification=qualification,
            qualification_reason=qualification_reason,
            current_catalog_evidence=copy.deepcopy(dict(catalog)),
            units=units,
            coverage=coverage,
            months=months,
            served_at=served_at,
            status=status,
            reason=reason,
        )
        _monthly_revenue_cache.set(cache_key, copy.deepcopy(read))
        return copy.deepcopy(read)

    def adjacent_monthly_revenues(
        self,
        symbol: Symbol | str,
        month: str,
        *,
        today_taipei: date | None = None,
    ) -> TwmdMonthlyRevenueRead:
        """Read the named month and its predecessor; the caller supplies the period."""
        target = _validate_month(month, "month")
        previous = (
            date(target.year - 1, 12, 1)
            if target.month == 1
            else date(target.year, target.month - 1, 1)
        )
        return self.monthly_revenues(
            symbol,
            previous.strftime("%Y-%m"),
            target.strftime("%Y-%m"),
            today_taipei=today_taipei,
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
