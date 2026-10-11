"""Strict decoding for TWMD's bounded retained financial-period index."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdFinancialStatementPeriodAuthority,
    TwmdFinancialStatementPeriodIndexEntry,
    TwmdFinancialStatementPeriodIndexCoverage,
    TwmdFinancialStatementPeriodQualification,
    TwmdFinancialStatementPeriodStatementCoverage,
    TwmdFinancialStatementPeriodsRead,
)

FINANCIAL_STATEMENT_PERIODS_ENDPOINT = "/api/v1/financial-statement-periods"
FINANCIAL_STATEMENT_PERIODS_CONTRACT = "twmd.financial-statement-periods/v1"
FINANCIAL_STATEMENT_PERIODS_SOURCE = "mops_financial_statements"
FINANCIAL_STATEMENT_PERIODS_SOURCE_CONTRACT = "mops.financial-statements/v1"
FINANCIAL_STATEMENT_NAMES = ("balance_sheet", "comprehensive_income", "cash_flows")
FINANCIAL_PERIOD_PRESENCES = {
    "present_readable", "present_unreadable", "missing", "unsupported", "unknown",
}
_INSTRUMENT_ID = re.compile(r"(?:TWSE|TPEX):[0-9][0-9A-Z]{3,5}\Z", re.ASCII)


def _invalid(message: str) -> TwmdReadError:
    return TwmdReadError(
        f"invalid twmd financial-statement-periods response: {message}",
        reason_code="invalid_response",
    )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(f"{label} must be an object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{label} must be a non-empty string")
    return value


def _maybe_string(value: object, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _integer(value: object, label: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise _invalid(f"{label} must be an integer >= {minimum}")
    return value


def _utc(value: object, label: str) -> str:
    raw = _string(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _invalid(f"{label} must be a UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise _invalid(f"{label} must be a UTC timestamp")
    return raw


def _period_key(value: object, label: str) -> tuple[int, int]:
    item = _object(value, label)
    year = _integer(item.get("fiscal_year"), f"{label}.fiscal_year", minimum=2024)
    quarter = _integer(item.get("fiscal_quarter"), f"{label}.fiscal_quarter", minimum=1)
    if quarter > 4:
        raise _invalid(f"{label}.fiscal_quarter must be between 1 and 4")
    return year, quarter


def _period_number(key: tuple[int, int]) -> int:
    return key[0] * 4 + key[1] - 1


def _authority(value: object, label: str) -> TwmdFinancialStatementPeriodAuthority:
    item = _object(value, label)
    fields = (
        "capture_id", "document_id", "semantic_revision_id", "source_contract",
        "parser_contract", "original_received_at_utc", "document_first_observed_at_utc",
        "semantic_revision_first_observed_at_utc", "latest_observed_at_utc",
    )
    strings = {name: _string(item.get(name), f"{label}.{name}") for name in fields}
    if strings["source_contract"] != FINANCIAL_STATEMENT_PERIODS_SOURCE_CONTRACT:
        raise _invalid(f"{label}.source_contract is unsupported")
    revision = _integer(item.get("revision_number"), f"{label}.revision_number", minimum=1)
    for name in (
        "original_received_at_utc", "document_first_observed_at_utc",
        "semantic_revision_first_observed_at_utc", "latest_observed_at_utc",
    ):
        strings[name] = _utc(strings[name], f"{label}.{name}")
    return TwmdFinancialStatementPeriodAuthority(**strings, revision_number=revision)


def _statement_coverage(value: object, label: str) -> dict[str, TwmdFinancialStatementPeriodStatementCoverage]:
    item = _object(value, label)
    if set(item) - set(FINANCIAL_STATEMENT_NAMES):
        raise _invalid(f"{label} contains an unsupported statement")
    result: dict[str, TwmdFinancialStatementPeriodStatementCoverage] = {}
    for name, raw in item.items():
        coverage = _object(raw, f"{label}.{name}")
        presence = _string(coverage.get("presence"), f"{label}.{name}.presence")
        if presence not in FINANCIAL_PERIOD_PRESENCES:
            raise _invalid(f"{label}.{name}.presence is unsupported")
        count = _integer(coverage.get("fact_count"), f"{label}.{name}.fact_count", minimum=0)
        if (presence == "present_readable") != (count > 0):
            raise _invalid(f"{label}.{name} presence disagrees with fact_count")
        if presence not in {"present_readable", "present_unreadable"}:
            raise _invalid(f"{label}.{name}.presence must describe retained statement metadata")
        result[name] = TwmdFinancialStatementPeriodStatementCoverage(presence, count)
    return result


def _period_entry(
    value: object,
    *,
    label: str,
    report_scope: str,
    statement: str | None,
    coverage_status: str,
    qualification_status: str,
    window_start: tuple[int, int],
    window_end: tuple[int, int] | None,
) -> TwmdFinancialStatementPeriodIndexEntry:
    item = _object(value, label)
    key = _period_key(item, label)
    item_scope = _string(item.get("report_scope"), f"{label}.report_scope")
    if item_scope != report_scope:
        raise _invalid(f"{label}.report_scope does not match request")
    presence = _string(item.get("presence"), f"{label}.presence")
    if presence not in FINANCIAL_PERIOD_PRESENCES:
        raise _invalid(f"{label}.presence is unsupported")
    reason = _string(item.get("reason"), f"{label}.reason")
    statements = _statement_coverage(item.get("statement_coverage"), f"{label}.statement_coverage")
    authority_raw = item.get("authority")
    authority = None if authority_raw is None else _authority(authority_raw, f"{label}.authority")

    in_window = _period_number(key) >= _period_number(window_start)
    if window_end is not None:
        in_window = in_window and _period_number(key) <= _period_number(window_end)
    if not in_window:
        raise _invalid(f"{label} lies outside the declared fiscal window")

    retained = presence in {"present_readable", "present_unreadable"}
    if retained:
        if coverage_status != "complete" or qualification_status != "qualified":
            raise _invalid(f"{label} claims retained presence without complete qualified coverage")
        if authority is None:
            raise _invalid(f"{label} retained presence is missing authority")
        selected = (statement,) if statement is not None else FINANCIAL_STATEMENT_NAMES
        if not set(selected).issubset(statements):
            raise _invalid(f"{label} retained presence is missing selected statement coverage")
        if statement is None and set(statements) != set(FINANCIAL_STATEMENT_NAMES):
            raise _invalid(f"{label} aggregate presence is missing statement coverage")
        selected_readable = all(statements[name].presence == "present_readable" for name in selected)
        expected_presence = "present_readable" if selected_readable else "present_unreadable"
        if presence != expected_presence:
            raise _invalid(f"{label}.presence disagrees with statement coverage")
    elif authority is not None or statements:
        raise _invalid(f"{label} has authority or statement coverage without retained presence")
    elif presence == "missing" and (coverage_status != "complete" or qualification_status != "qualified"):
        raise _invalid(f"{label} claims missing without complete qualified coverage")
    elif presence == "unsupported" and qualification_status == "qualified" and coverage_status != "complete":
        raise _invalid(f"{label} claims unsupported without a complete issuer qualification")
    elif presence == "unknown" and coverage_status == "complete" and qualification_status == "qualified":
        raise _invalid(f"{label} is unknown despite complete qualified coverage")

    return TwmdFinancialStatementPeriodIndexEntry(
        key[0], key[1], item_scope, presence, reason, statements, authority,
    )


def _qualification(value: object, instrument_id: str) -> TwmdFinancialStatementPeriodQualification:
    item = _object(value, "qualification")
    status = _string(item.get("status"), "qualification.status")
    if status not in {"qualified", "pending", "unsupported"}:
        raise _invalid("qualification.status is unsupported")
    reason = _string(item.get("reason"), "qualification.reason")
    industry_code = _maybe_string(item.get("industry_code"), "qualification.industry_code")
    catalog_raw = item.get("catalog")
    profile_raw = item.get("profile")
    catalog = None if catalog_raw is None else dict(_object(catalog_raw, "qualification.catalog"))
    profile = None if profile_raw is None else dict(_object(profile_raw, "qualification.profile"))
    venue, _symbol = instrument_id.split(":", 1)
    if catalog is not None:
        catalog_id = catalog.get("instrument_id")
        if catalog_id is not None and catalog_id != instrument_id:
            raise _invalid("qualification.catalog belongs to a different instrument")
        catalog_venue = catalog.get("venue")
        if catalog_venue is not None and catalog_venue != venue:
            raise _invalid("qualification.catalog venue does not match the selected instrument")
        if "is_active" in catalog and type(catalog["is_active"]) is not bool:
            raise _invalid("qualification.catalog.is_active must be boolean")
    if profile is not None:
        profile_id = profile.get("instrument_id")
        if profile_id is not None and profile_id != instrument_id:
            raise _invalid("qualification.profile belongs to a different instrument")
    if status == "qualified":
        if venue != "TWSE" or industry_code != "24" or catalog is None or profile is None:
            raise _invalid("qualified issuer is outside the supported selector scope")
        if (
            catalog.get("instrument_id") != instrument_id
            or catalog.get("venue") != "TWSE"
            or catalog.get("security_type") != "EQUITY"
            or type(catalog.get("is_active")) is not bool
            or profile.get("instrument_id") != instrument_id
            or profile.get("industry_code") != "24"
        ):
            raise _invalid("qualified issuer evidence does not match the selected instrument")
        listed_on = profile.get("listed_on")
        if not isinstance(listed_on, str):
            raise _invalid("qualified profile is missing listed_on")
        try:
            parsed = date.fromisoformat(listed_on)
        except ValueError as exc:
            raise _invalid("qualified profile listed_on is malformed") from exc
        if parsed.isoformat() != listed_on:
            raise _invalid("qualified profile listed_on is not canonical")
    return TwmdFinancialStatementPeriodQualification(
        status, reason, industry_code, catalog, profile,
    )


def decode_financial_statement_periods_response(
    payload: object,
    *,
    instrument_id: str,
    report_scope: str,
    statement: str | None,
    limit: int,
    cursor: str | None,
) -> TwmdFinancialStatementPeriodsRead:
    """Validate one exact selector response and preserve its retained authority."""
    if not isinstance(instrument_id, str) or _INSTRUMENT_ID.fullmatch(instrument_id) is None:
        raise ValueError("financial-period reads require one canonical TWSE:/TPEX: instrument ID")
    if report_scope not in {"consolidated", "individual"}:
        raise ValueError("financial-period report_scope is unsupported")
    if statement is not None and statement not in FINANCIAL_STATEMENT_NAMES:
        raise ValueError("financial-period statement selector is unsupported")
    if type(limit) is not int or not 1 <= limit <= 40:
        raise ValueError("financial-period limit must be between 1 and 40")
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 2048):
        raise ValueError("financial-period cursor must be a non-empty token up to 2048 characters")

    response = _object(payload, "response")
    if response.get("contract_version") != "twmd.financial-statement-periods/v1":
        raise _invalid("contract_version is unsupported")
    venue = instrument_id.split(":", 1)[0]
    selectors = _object(response.get("selectors"), "selectors")
    expected = {
        "instrument_id": instrument_id,
        "venue": venue,
        "source": FINANCIAL_STATEMENT_PERIODS_SOURCE,
        "report_scope": report_scope,
        "statement": statement,
        "limit": limit,
    }
    if selectors != expected:
        raise _invalid("selectors do not exactly match the request")

    supported = dict(_object(response.get("supported_scope"), "supported_scope"))
    if (
        supported.get("source") != FINANCIAL_STATEMENT_PERIODS_SOURCE
        or supported.get("source_contract") != FINANCIAL_STATEMENT_PERIODS_SOURCE_CONTRACT
    ):
        raise _invalid("supported_scope source does not match the financial-statement contract")
    for name in ("venues", "industry_codes", "security_types", "report_scopes", "statements"):
        values = supported.get(name)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise _invalid(f"supported_scope.{name} must be a string list")
        if len(values) != len(set(values)):
            raise _invalid(f"supported_scope.{name} contains duplicates")
    if (
        "TWSE" not in supported["venues"] or "24" not in supported["industry_codes"]
        or "EQUITY" not in supported["security_types"]
        or "consolidated" not in supported["report_scopes"]
        or not set(FINANCIAL_STATEMENT_NAMES).issubset(supported["statements"])
    ):
        raise _invalid("supported_scope does not describe the contracted phase-one scope")

    window = _object(response.get("window"), "window")
    window_start = _period_key(window.get("start"), "window.start")
    if window_start != (2024, 1):
        raise _invalid("window.start must be 2024Q1 for contract v1")
    window_end_raw = window.get("end")
    window_end = None if window_end_raw is None else _period_key(window_end_raw, "window.end")
    if window_end is not None and _period_number(window_end) < _period_number(window_start):
        raise _invalid("window.end precedes window.start")

    qualification = _qualification(response.get("qualification"), instrument_id)
    coverage_raw = _object(response.get("coverage"), "coverage")
    coverage_status = _string(coverage_raw.get("status"), "coverage.status")
    if coverage_status not in {"complete", "partial", "unknown"}:
        raise _invalid("coverage.status is unsupported")
    coverage_reason = _string(coverage_raw.get("reason"), "coverage.reason")
    if coverage_status == "complete" and qualification.status != "qualified":
        raise _invalid("coverage cannot be complete without qualified issuer evidence")
    coverage = TwmdFinancialStatementPeriodIndexCoverage(coverage_status, coverage_reason)

    page_raw = response.get("periods")
    if not isinstance(page_raw, list) or len(page_raw) > min(limit, 40):
        raise _invalid("periods must be a bounded list matching the requested limit")
    periods = tuple(
        _period_entry(
            raw, label=f"periods[{index}]", report_scope=report_scope, statement=statement,
            coverage_status=coverage_status, qualification_status=qualification.status,
            window_start=window_start, window_end=window_end,
        )
        for index, raw in enumerate(page_raw)
    )
    keys = [(period.fiscal_year, period.fiscal_quarter) for period in periods]
    if len(keys) != len(set(keys)) or keys != sorted(keys, reverse=True):
        raise _invalid("periods must be unique and ordered newest first")

    has_more = response.get("has_more")
    if type(has_more) is not bool:
        raise _invalid("has_more must be boolean")
    cursor_raw = response.get("next_cursor")
    next_cursor = _maybe_string(cursor_raw, "next_cursor")
    if has_more != (next_cursor is not None) or (next_cursor is not None and len(next_cursor) > 2048):
        raise _invalid("next_cursor does not match has_more or exceeds its limit")
    if has_more and (not periods or qualification.status == "unsupported"):
        raise _invalid("unsupported or empty page cannot continue")
    if cursor is not None and qualification.status == "unsupported":
        raise _invalid("unsupported selection cannot continue a cursor")

    def latest(name: str) -> TwmdFinancialStatementPeriodIndexEntry | None:
        raw = response.get(name)
        return None if raw is None else _period_entry(
            raw, label=name, report_scope=report_scope, statement=statement,
            coverage_status=coverage_status, qualification_status=qualification.status,
            window_start=window_start, window_end=window_end,
        )

    latest_retained = latest("latest_retained_period")
    latest_readable = latest("latest_readable_period")
    if coverage_status != "complete" or qualification.status != "qualified":
        if latest_retained is not None or latest_readable is not None:
            raise _invalid("latest periods require complete matching qualified coverage")
    if latest_retained is not None and latest_retained.presence not in {"present_readable", "present_unreadable"}:
        raise _invalid("latest_retained_period does not describe retained authority")
    if latest_readable is not None and latest_readable.presence != "present_readable":
        raise _invalid("latest_readable_period is not readable")
    if latest_retained is not None and latest_readable is not None and (
        _period_number((latest_readable.fiscal_year, latest_readable.fiscal_quarter))
        > _period_number((latest_retained.fiscal_year, latest_retained.fiscal_quarter))
    ):
        raise _invalid("latest_readable_period is newer than latest_retained_period")
    for period in periods:
        key_number = _period_number((period.fiscal_year, period.fiscal_quarter))
        if period.presence in {"present_readable", "present_unreadable"} and (
            latest_retained is None
            or key_number > _period_number((latest_retained.fiscal_year, latest_retained.fiscal_quarter))
        ):
            raise _invalid("a retained page period is newer than latest_retained_period")
        if period.presence == "present_readable" and (
            latest_readable is None
            or key_number > _period_number((latest_readable.fiscal_year, latest_readable.fiscal_quarter))
        ):
            raise _invalid("a readable page period is newer than latest_readable_period")
    page_by_key = {(period.fiscal_year, period.fiscal_quarter): period for period in periods}
    for candidate in (latest_retained, latest_readable):
        if candidate is None:
            continue
        page_item = page_by_key.get((candidate.fiscal_year, candidate.fiscal_quarter))
        if page_item is not None and page_item != candidate:
            raise _invalid("latest period conflicts with its page entry")

    served_at = _utc(response.get("served_at_utc"), "served_at_utc")
    return TwmdFinancialStatementPeriodsRead(
        contract_version="twmd.financial-statement-periods/v1",
        instrument_id=instrument_id,
        venue=venue,
        source=FINANCIAL_STATEMENT_PERIODS_SOURCE,
        report_scope=report_scope,
        statement=statement,
        limit=limit,
        supported_scope=supported,
        window_start=window_start,
        window_end=window_end,
        qualification=qualification,
        coverage=coverage,
        periods=periods,
        next_cursor=next_cursor,
        has_more=has_more,
        latest_retained_period=latest_retained,
        latest_readable_period=latest_readable,
        served_at_utc=served_at,
    )
