"""Strict decoding for TWMD's bounded consolidated financial-statement read."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import parse_qs, urlsplit

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdFinancialStatementContext,
    TwmdFinancialStatementCoverage,
    TwmdFinancialStatementDimension,
    TwmdFinancialStatementFact,
    TwmdFinancialStatementPeriod,
    TwmdFinancialStatementQualification,
    TwmdFinancialStatementRead,
    TwmdFinancialStatementReport,
    TwmdFinancialStatementUnit,
)

FINANCIAL_STATEMENTS = ("balance_sheet", "comprehensive_income", "cash_flows")
FINANCIAL_STATEMENT_SOURCE_CONTRACT = "mops.financial-statements/v1"
FINANCIAL_STATEMENT_PARSER_CONTRACT = "mops.inline-xbrl-financial-statements/v1"
FINANCIAL_STATEMENT_ENDPOINT = "/api/v1/financial-statements"
_STATEMENT_ORDER = {name: index for index, name in enumerate(FINANCIAL_STATEMENTS)}
_XBRLI = "http://www.xbrl.org/2003/instance"
_ISO4217 = "http://www.xbrl.org/2003/iso4217"
_ALLOWED_UNITS = {
    ((f"{{{_ISO4217}}}TWD",), ()),
    ((f"{{{_XBRLI}}}shares",), ()),
    ((f"{{{_XBRLI}}}pure",), ()),
    ((f"{{{_ISO4217}}}TWD",), (f"{{{_XBRLI}}}shares",)),
}
_QNAME = re.compile(r"\{[^{}\s]+\}[A-Za-z_][A-Za-z0-9_.-]*\Z", re.ASCII)
_TAXONOMIES = {
    "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full",
    "http://www.xbrl.org/tifrs/scf/2020-06-30",
    "http://www.xbrl.org/tifrs/ar/2020-06-30",
    "http://www.xbrl.org/tifrs/bsci/ci/2020-06-30",
    "http://www.xbrl.org/tifrs/es/2020-06-30",
    "http://www.xbrl.org/tifrs/notes/2020-06-30",
}
_LEXICAL_NUMBER = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?\Z", re.ASCII)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)
_ACCURACY = re.compile(r"-?[0-9]+\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_UTC = timezone.utc


def _invalid(message: str) -> TwmdReadError:
    return TwmdReadError(
        f"invalid twmd financial-statements response: {message}",
        reason_code="invalid_response",
    )


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise _invalid(f"{label} must be an object")
    return value


def _string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise _invalid(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _integer(value: object, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise _invalid(f"{label} must be an integer")
    return value


def _date(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _invalid(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise _invalid(f"{label} must be a canonical ISO date")
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


def _qname(value: object, label: str) -> str:
    raw = _string(value, label)
    if _QNAME.fullmatch(raw) is None:
        raise _invalid(f"{label} must be an expanded QName")
    return raw


def _concept(value: object, label: str) -> str:
    raw = _qname(value, label)
    if raw[1:raw.index("}")] not in _TAXONOMIES:
        raise _invalid(f"{label} belongs to an unsupported taxonomy")
    return raw


def _timestamp_or_none(value: object, label: str) -> str | None:
    return None if value is None else _utc(value, label)


def _scope_end(fiscal_year: int, fiscal_quarter: int) -> date:
    if fiscal_quarter == 4:
        return date(fiscal_year, 12, 31)
    month_after = fiscal_quarter * 3 + 1
    return date(fiscal_year, month_after, 1) - timedelta(days=1)


def _decode_period(
    raw: object,
    *,
    fiscal_year: int,
    fiscal_quarter: int,
    instrument_id: str,
) -> TwmdFinancialStatementPeriod:
    item = _object(raw, "fact.context.period")
    kind = _string(item.get("kind"), "fact.context.period.kind")
    instant = item.get("instant")
    start = item.get("start_date")
    end = item.get("end_date")
    if kind == "instant":
        if instant is None or start is not None or end is not None:
            raise _invalid("instant period has conflicting date fields")
        instant_text = _date(instant, "fact.context.period.instant")
        period_end = date.fromisoformat(instant_text)
        start_text = end_text = None
    elif kind == "duration":
        if instant is not None or start is None or end is None:
            raise _invalid("duration period has conflicting date fields")
        start_text = _date(start, "fact.context.period.start_date")
        end_text = _date(end, "fact.context.period.end_date")
        period_start, period_end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        if period_start > period_end:
            raise _invalid("duration period starts after it ends")
        instant_text = None
    else:
        raise _invalid("fact.context.period.kind is unsupported")

    if instrument_id.split(":", 1)[0] != "TWSE":
        raise _invalid("financial-statement period belongs to an unsupported venue")
    if period_end > _scope_end(fiscal_year, fiscal_quarter):
        raise _invalid("fact period ends after the selected fiscal scope")
    return TwmdFinancialStatementPeriod(kind, instant_text, start_text, end_text)


def _decode_fact(
    raw: object,
    *,
    instrument_id: str,
    fiscal_year: int,
    fiscal_quarter: int,
    statement_filter: str | None,
) -> TwmdFinancialStatementFact:
    item = _object(raw, "fact")
    statement = _string(item.get("statement"), "fact.statement")
    if statement not in _STATEMENT_ORDER or (statement_filter and statement != statement_filter):
        raise _invalid("fact statement does not match the selected statement")
    ordinal = _integer(item.get("occurrence_ordinal"), "fact.occurrence_ordinal", minimum=0)
    concept = _concept(item.get("concept_qname"), "fact.concept_qname")
    context_raw = _object(item.get("context"), "fact.context")
    source_context = _string(context_raw.get("source_id"), "fact.context.source_id")
    entity = _string(context_raw.get("entity_identifier"), "fact.context.entity_identifier")
    entity_scheme = _string(context_raw.get("entity_scheme"), "fact.context.entity_scheme")
    selected_code = instrument_id.split(":", 1)[1]
    if entity != selected_code or entity_scheme != "http://www.twse.com.tw":
        raise _invalid("fact context entity does not match the selected TWSE issuer")

    dimensions_raw = context_raw.get("dimensions")
    if not isinstance(dimensions_raw, list):
        raise _invalid("fact.context.dimensions must be a list")
    dimensions: list[TwmdFinancialStatementDimension] = []
    for raw_dimension in dimensions_raw:
        dimension = _object(raw_dimension, "fact.context.dimension")
        dimensions.append(TwmdFinancialStatementDimension(
            _concept(dimension.get("axis_qname"), "fact.context.dimension.axis_qname"),
            _concept(dimension.get("member_qname"), "fact.context.dimension.member_qname"),
        ))
    axes = [dimension.axis_qname for dimension in dimensions]
    if axes != sorted(axes) or len(set(axes)) != len(axes):
        raise _invalid("fact dimensions must be uniquely sorted by axis QName")
    period = _decode_period(
        context_raw.get("period"), fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter, instrument_id=instrument_id,
    )
    context = TwmdFinancialStatementContext(
        source_context,
        entity,
        entity_scheme,
        period,
        tuple(dimensions),
    )

    unit_raw = _object(item.get("unit"), "fact.unit")
    unit_id = _string(unit_raw.get("source_id"), "fact.unit.source_id")
    numerator_raw, denominator_raw = unit_raw.get("numerator"), unit_raw.get("denominator")
    if not isinstance(numerator_raw, list) or not isinstance(denominator_raw, list):
        raise _invalid("fact unit numerator and denominator must be lists")
    numerator = tuple(_qname(value, "fact.unit.numerator") for value in numerator_raw)
    denominator = tuple(_qname(value, "fact.unit.denominator") for value in denominator_raw)
    if (numerator, denominator) not in _ALLOWED_UNITS:
        raise _invalid("fact unit is outside the supported MOPS unit contract")
    unit = TwmdFinancialStatementUnit(unit_id, numerator, denominator)

    value_raw = item.get("value")
    if value_raw is not None:
        if not isinstance(value_raw, str) or len(value_raw) > 512 or _DECIMAL.fullmatch(value_raw) is None:
            raise _invalid("fact.value must be an exact finite decimal string or null")
        try:
            if not Decimal(value_raw).is_finite():
                raise _invalid("fact.value must be finite")
        except InvalidOperation as exc:
            raise _invalid("fact.value is not a decimal") from exc
    is_nil = item.get("is_nil")
    if type(is_nil) is not bool or (is_nil and value_raw is not None) or (not is_nil and value_raw is None):
        raise _invalid("fact null value and is_nil disagree")
    lexical = _string(item.get("lexical_value"), "fact.lexical_value", allow_empty=True)
    if len(lexical) > 256:
        raise _invalid("fact.lexical_value exceeds the upstream limit")
    format_qname = item.get("format_qname")
    if format_qname is not None:
        format_qname = _qname(format_qname, "fact.format_qname")
        if format_qname != "{http://www.xbrl.org/inlineXBRL/transformation/2015-02-26}numdotdecimal":
            raise _invalid("fact numeric transform is outside the accepted MOPS contract")
    scale = item.get("scale")
    if scale is not None and (type(scale) is not int or not -12 <= scale <= 12):
        raise _invalid("fact.scale is outside the upstream limit")
    sign = item.get("sign")
    if sign not in (None, "-"):
        raise _invalid("fact.sign is unsupported")
    if not is_nil:
        if _LEXICAL_NUMBER.fullmatch(lexical) is None:
            raise _invalid("fact.lexical_value must be an exact decimal with optional grouping commas")
        if "," in lexical and format_qname is None:
            raise _invalid("grouped lexical values require the source numdotdecimal transform")
        with localcontext() as decimal_context:
            decimal_context.prec = max(300, len(lexical) + abs(scale or 0) + 10)
            normalized = Decimal(lexical.replace(",", "")).scaleb(scale or 0)
            if sign == "-":
                normalized = -normalized
        if normalized != Decimal(value_raw):
            raise _invalid("fact value disagrees with its lexical value, sign, and scale")
    decimals = item.get("decimals")
    precision = item.get("precision")
    if decimals is not None and precision is not None:
        raise _invalid("fact has conflicting decimals and precision declarations")
    for label, value in (("decimals", decimals), ("precision", precision)):
        if value is not None and (
            not isinstance(value, str)
            or (value != "INF" and (
                len(value) > 4 or _ACCURACY.fullmatch(value) is None or not -256 <= int(value) <= 256
            ))
        ):
            raise _invalid(f"fact.{label} is malformed")
    return TwmdFinancialStatementFact(
        statement, ordinal, concept, context, unit, value_raw, is_nil, lexical,
        format_qname, scale, sign, decimals, precision,
    )


def _decode_qualification(raw: object, instrument_id: str, quarter_end: date) -> TwmdFinancialStatementQualification:
    item = _object(raw, "qualification")
    status = _string(item.get("status"), "qualification.status")
    if status not in {"qualified", "pending", "unsupported"}:
        raise _invalid("qualification.status is unsupported")
    reason = _string(item.get("reason"), "qualification.reason")
    industry = _optional_string(item.get("industry_code"), "qualification.industry_code")
    catalog = item.get("catalog_evidence")
    profile = item.get("profile_evidence")
    if catalog is not None:
        catalog = dict(_object(catalog, "qualification.catalog_evidence"))
        evidence_id = catalog.get("instrument_id")
        if evidence_id is not None and evidence_id != instrument_id:
            raise _invalid("qualification catalog evidence belongs to another issuer")
        if catalog.get("venue") not in (None, "TWSE"):
            raise _invalid("qualification catalog evidence belongs to another venue")
    if profile is not None:
        profile = dict(_object(profile, "qualification.profile_evidence"))
        evidence_id = profile.get("instrument_id")
        if evidence_id is not None and evidence_id != instrument_id:
            raise _invalid("qualification profile evidence belongs to another issuer")
    if status == "qualified" and industry != "24":
        raise _invalid("qualified issuer is outside the admitted industry")
    if status == "qualified" and (catalog is None or profile is None):
        raise _invalid("qualified issuer is missing frozen admission evidence")
    if status == "qualified" and (
        catalog.get("instrument_id") != instrument_id
        or catalog.get("venue") != "TWSE"
        or catalog.get("security_type") != "EQUITY"
        or type(catalog.get("is_active")) is not bool
        or profile.get("instrument_id") != instrument_id
        or profile.get("industry_code") != "24"
    ):
        raise _invalid("qualified issuer is missing consistent frozen catalog/profile evidence")
    if profile is not None and profile.get("industry_code") not in (None, industry):
        raise _invalid("qualification industry disagrees with profile evidence")
    if status == "qualified":
        listed_on = date.fromisoformat(_date(profile.get("listed_on"), "qualification.profile.listed_on"))
        if listed_on > quarter_end:
            raise _invalid("qualified issuer was listed after the selected quarter")
    return TwmdFinancialStatementQualification(status, reason, industry, catalog, profile)


def _decode_coverage(raw: object) -> TwmdFinancialStatementCoverage:
    item = _object(raw, "coverage")
    status = _string(item.get("status"), "coverage.status")
    presence = _string(item.get("latest_discovery_presence"), "coverage.latest_discovery_presence")
    if status not in {"AVAILABLE", "MISSING"} or presence not in {"present", "not_advertised", "missing"}:
        raise _invalid("coverage status or discovery presence is unsupported")
    capture_id = _optional_string(item.get("capture_id"), "coverage.capture_id")
    receipt = _timestamp_or_none(item.get("original_received_at_utc"), "coverage.original_received_at_utc")
    reason = _string(item.get("reason"), "coverage.reason")
    if (capture_id is None) != (receipt is None) or (presence == "missing") != (capture_id is None):
        raise _invalid("coverage capture, receipt, and discovery presence disagree")
    return TwmdFinancialStatementCoverage(status, reason, presence, capture_id, receipt)


def _decode_report(raw: object, *, instrument_id: str, fiscal_year: int, fiscal_quarter: int) -> TwmdFinancialStatementReport:
    item = _object(raw, "report")
    member = _string(item.get("member_filename"), "report.member_filename")
    code = instrument_id.split(":", 1)[1]
    expected_name = f"tifrs-fr1-m1-ci-cr-{code}-{fiscal_year}Q{fiscal_quarter}.html"
    if member != expected_name:
        raise _invalid("report filename does not match the selected issuer and fiscal scope")
    source_url = _string(item.get("source_url"), "report.source_url")
    try:
        parts = urlsplit(source_url)
        query = parse_qs(parts.query, strict_parsing=True)
    except ValueError as exc:
        raise _invalid("report source URL is malformed") from exc
    if (
        parts.scheme != "https" or parts.netloc != "mopsov.twse.com.tw"
        or parts.path != "/server-java/t164sb01" or parts.fragment
        or query != {"step": ["3"], "SYEAR": [str(fiscal_year)], "file_name": [member]}
    ):
        raise _invalid("report source URL does not match the fixed official MOPS selector")
    raw_sha = _string(item.get("raw_sha256"), "report.raw_sha256")
    if _DIGEST.fullmatch(raw_sha) is None:
        raise _invalid("report.raw_sha256 must be a lowercase SHA-256 digest")
    source_contract = _string(item.get("source_contract"), "report.source_contract")
    parser_contract = _string(item.get("parser_contract"), "report.parser_contract")
    if source_contract != FINANCIAL_STATEMENT_SOURCE_CONTRACT or parser_contract != FINANCIAL_STATEMENT_PARSER_CONTRACT:
        raise _invalid("report contract version is unsupported")
    published = _timestamp_or_none(item.get("published_at_utc"), "report.published_at_utc")
    if published is not None:
        raise _invalid("publisher time is unknown in the accepted financial-statement contract")
    amendment = _string(item.get("amendment_status"), "report.amendment_status")
    if amendment != "unknown":
        raise _invalid("report amendment status is outside the accepted contract")
    result = TwmdFinancialStatementReport(
        _string(item.get("document_id"), "report.document_id"),
        _string(item.get("capture_id"), "report.capture_id"),
        _string(item.get("semantic_revision_id"), "report.semantic_revision_id"),
        member, source_url, raw_sha, source_contract, parser_contract,
        _utc(item.get("original_received_at_utc"), "report.original_received_at_utc"),
        _utc(item.get("document_first_observed_at_utc"), "report.document_first_observed_at_utc"),
        _utc(item.get("semantic_revision_first_observed_at_utc"), "report.semantic_revision_first_observed_at_utc"),
        _utc(item.get("latest_observed_at_utc"), "report.latest_observed_at_utc"),
        published, amendment,
    )
    first_document, first_revision, received, latest = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        for value in (
            result.document_first_observed_at_utc, result.semantic_revision_first_observed_at_utc,
            result.original_received_at_utc, result.latest_observed_at_utc,
        )
    )
    if not first_revision <= first_document <= received <= latest:
        raise _invalid("report observation receipts have inconsistent chronology")
    return result


def decode_financial_statement_response(
    payload: object,
    *,
    instrument_id: str,
    fiscal_year: int,
    fiscal_quarter: int,
    report_scope: str,
    statement: str | None,
    limit: int,
) -> TwmdFinancialStatementRead:
    """Validate an API envelope and all facts against the requested scope."""
    item = _object(payload, "response")
    if item.get("instrument_id") != instrument_id:
        raise _invalid("response issuer does not match the request")
    if type(item.get("fiscal_year")) is not int or item["fiscal_year"] != fiscal_year:
        raise _invalid("response fiscal year does not match the request")
    if type(item.get("fiscal_quarter")) is not int or item["fiscal_quarter"] != fiscal_quarter:
        raise _invalid("response fiscal quarter does not match the request")
    if item.get("report_scope") != report_scope or report_scope != "consolidated":
        raise _invalid("response report scope does not match the supported scope")
    if item.get("statement") != statement:
        raise _invalid("response statement filter does not match the request")
    qualification = _decode_qualification(item.get("qualification"), instrument_id, _scope_end(fiscal_year, fiscal_quarter))
    coverage = _decode_coverage(item.get("coverage"))
    report_raw = item.get("report")
    report = None if report_raw is None else _decode_report(
        report_raw, instrument_id=instrument_id,
        fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter,
    )
    facts_raw = item.get("facts")
    if not isinstance(facts_raw, list) or len(facts_raw) > limit:
        raise _invalid("facts must be a bounded list")
    facts = tuple(_decode_fact(
        row, instrument_id=instrument_id, fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter, statement_filter=statement,
    ) for row in facts_raw)
    total = _integer(item.get("total_fact_count"), "total_fact_count", minimum=0)
    returned = _integer(item.get("returned_fact_count"), "returned_fact_count", minimum=0)
    truncated = item.get("truncated")
    if (type(truncated) is not bool or returned != len(facts) or returned != min(total, limit)
            or total > 20_000 or truncated != (returned < total)):
        raise _invalid("fact counts and truncation flag disagree")
    if facts:
        order_keys = [(_STATEMENT_ORDER[fact.statement], fact.occurrence_ordinal) for fact in facts]
        if order_keys != sorted(order_keys) or len(set(order_keys)) != len(order_keys):
            raise _invalid("facts are not in deterministic statement/occurrence order")
        for name in FINANCIAL_STATEMENTS:
            ordinals = [fact.occurrence_ordinal for fact in facts if fact.statement == name]
            if ordinals != list(range(len(ordinals))):
                raise _invalid("facts omit source occurrences from the returned prefix")
    if total == 0 and facts:
        raise _invalid("empty report count conflicts with returned facts")
    if report is None:
        if facts or total or coverage.status != "MISSING" or truncated:
            raise _invalid("unreported scope must not contain facts or available coverage")
        if coverage.latest_discovery_presence == "present":
            raise _invalid("present report discovery requires a retained report")
        if qualification.status == "unsupported" and coverage.reason != qualification.reason:
            raise _invalid("unsupported qualification reason and coverage reason disagree")
    else:
        if qualification.status != "qualified" or coverage.status != "AVAILABLE":
            raise _invalid("retained report must preserve its frozen qualified admission")
        if coverage.latest_discovery_presence == "missing" or total == 0:
            raise _invalid("retained report requires report facts and discovery evidence")
        if not truncated and returned != total:
            raise _invalid("complete report count is inconsistent")
        if not truncated:
            end = _scope_end(fiscal_year, fiscal_quarter).isoformat()
            start = date(fiscal_year, 1, 1).isoformat()
            mandatory = {
                "balance_sheet": "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}Assets",
                "comprehensive_income": "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}Revenue",
                "cash_flows": "{http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full}CashFlowsFromUsedInOperatingActivities",
            }
            for name in (FINANCIAL_STATEMENTS if statement is None else (statement,)):
                if not any(
                    fact.statement == name and fact.concept_qname == mandatory[name]
                    and not fact.is_nil
                    and (fact.context.period.instant == end if name == "balance_sheet" else
                         fact.context.period.start_date == start and fact.context.period.end_date == end)
                    for fact in facts
                ):
                    raise _invalid(f"complete {name} lacks the mandatory current-period fact")
    status = "unsupported" if qualification.status == "unsupported" else (
        "partial" if truncated else "available" if report is not None else "missing"
    )
    reason = "result_truncated" if truncated else (
        coverage.reason if report is None else "report_retained"
    )
    return TwmdFinancialStatementRead(
        instrument_id, fiscal_year, fiscal_quarter, report_scope, statement, limit,
        qualification, coverage, report, facts, total, returned, truncated,
        status, reason,
    )


__all__ = [
    "FINANCIAL_STATEMENTS",
    "FINANCIAL_STATEMENT_ENDPOINT",
    "FINANCIAL_STATEMENT_PARSER_CONTRACT",
    "FINANCIAL_STATEMENT_SOURCE_CONTRACT",
    "decode_financial_statement_response",
]
