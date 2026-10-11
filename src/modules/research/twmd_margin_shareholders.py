"""Source-preserving adapters for official margin and TDCC research blocks."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal

from marketdata.errors import TwmdReadError
from marketdata.types import TwmdMarginShortSaleRead, TwmdShareholderDistributionRead

from src.modules.research.twmd_profile_revenue import ResearchDataBlock


_TDCC_TOTAL_LEVEL = {"bulk_current": 17, "historical_html": 16}
_TDCC_LARGE_LEVELS = range(12, 16)
_TDCC_TIER_LABELS = {
    12: "400,001-600,000",
    13: "600,001-800,000",
    14: "800,001-1,000,000",
    15: "1,000,001以上",
}


def _margin_row_payload(row) -> dict:
    payload = asdict(row)
    for field in ("margin_utilization_rate", "short_sale_utilization_rate"):
        value = payload[field]
        payload[field] = str(value) if value is not None else None
    return payload


def _distribution_row_payload(row) -> dict:
    payload = asdict(row)
    payload["share_percentage_points"] = str(payload["share_percentage_points"])
    return payload


def _error_block(exc: TwmdReadError, endpoint: str, selectors: dict[str, str]) -> ResearchDataBlock:
    reason = exc.reason_code or (f"http_{exc.status_code}" if exc.status_code else "provider_error")
    return ResearchDataBlock(
        data=None,
        status="error",
        reason=reason,
        evidence={
            "provider": "twmd",
            "instrument_id": selectors.get("instrument_id"),
            "endpoint": endpoint,
            "selectors": selectors,
            "source_contract": None,
            "period": None,
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {},
            "dataset_coverage": None,
            "selected_instrument_presence": None,
            "http_status": exc.status_code,
        },
    )


def margin_short_sale_block(read: TwmdMarginShortSaleRead) -> ResearchDataBlock:
    observations = [_margin_row_payload(row) for row in read.data]
    latest = read.data[-1] if read.data else None
    previous = read.data[-2] if len(read.data) > 1 else None
    changes = None
    if latest is not None:
        changes = {
            "margin_balance": (
                latest.margin_balance - previous.margin_balance if previous else None
            ),
            "short_sale_balance": (
                latest.short_sale_balance - previous.short_sale_balance if previous else None
            ),
        }
    return ResearchDataBlock(
        data={
            "instrument_id": read.instrument_id,
            "native_unit": "trading_units",
            "observations": observations,
            "latest": _margin_row_payload(latest) if latest else None,
            "previous": _margin_row_payload(previous) if previous else None,
            "changes": changes,
            "comparison_reason": "previous_observation_unavailable" if latest and previous is None else None,
        },
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {
                "instrument_id": read.instrument_id,
                "start": read.start_date,
                "end": read.end_date,
            },
            "source_contract": None,
            "period": {"trade_dates": [row.trade_date for row in read.data]},
            "per_period_provenance": observations,
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {"quantities": "trading_units", "utilization_rates": "percent"},
            "dataset_coverage": [asdict(row) for row in read.coverage],
            "coverage_error_reason": read.coverage_error_reason,
            "selected_instrument_presence": "present" if read.data else "unknown",
            "revision": None,
            "capture_id": None,
            "payload_sha256": None,
            "provenance_limit": "The margin response has no row-level source receipt, revision, or contract fields.",
        },
    )


def _distribution_period(read: TwmdShareholderDistributionRead, report_date: str, variant: str) -> dict | None:
    rows = [row for row in read.data if row.report_date == report_date and row.report_variant == variant]
    total_level = _TDCC_TOTAL_LEVEL[variant]
    by_level = {row.source_level: row for row in rows}
    required_levels = set(range(1, total_level + 1))
    if set(by_level) != required_levels:
        raise ValueError("TDCC distribution report does not contain every required source level")
    if any(row.row_kind != "bucket" for level, row in by_level.items() if level not in {16, 17}):
        raise ValueError("TDCC bucket row_kind is invalid")
    if variant == "bulk_current":
        adjustment = by_level[16]
        total = by_level[17]
        buckets = [by_level[level] for level in range(1, 16)]
        if sum(row.holder_count for row in buckets) != total.holder_count:
            raise ValueError("TDCC bulk_current holder total does not reconcile")
        if sum(row.share_count for row in buckets) - adjustment.share_count != total.share_count:
            raise ValueError("TDCC bulk_current share total does not reconcile with adjustment")
    else:
        total = by_level[16]
        buckets = [by_level[level] for level in range(1, 16)]
        if sum(row.holder_count for row in buckets) != total.holder_count:
            raise ValueError("TDCC historical holder total does not reconcile")
        if sum(row.share_count for row in buckets) != total.share_count:
            raise ValueError("TDCC historical share total does not reconcile")
        adjustment = None
    for level, expected in _TDCC_TIER_LABELS.items():
        row = by_level[level]
        if row.source_tier_label is not None and row.source_tier_label != expected:
            raise ValueError("TDCC large-holder source tier mapping changed")
    if total.share_percentage_points != Decimal(100):
        raise ValueError("TDCC official total percentage must be 100")
    numerator = sum(by_level[level].share_count for level in _TDCC_LARGE_LEVELS)
    denominator = total.share_count
    percentage = (
        Decimal(numerator) * Decimal(100) / Decimal(denominator)
        if denominator > 0 else None
    )
    return {
        "report_date": report_date,
        "report_variant": variant,
        "provider": total.provider,
        "native_unit": "shares",
        "buckets": [_distribution_row_payload(by_level[level]) for level in range(1, 16)],
        "adjustment": _distribution_row_payload(adjustment) if adjustment else None,
        "official_total": _distribution_row_payload(total),
        "total_holder_accounts": total.holder_count,
        "total_share_count": denominator,
        "large_holding": {
            "threshold": ">400,000 shares",
            "minimum_shares": 400001,
            "source_levels": list(_TDCC_LARGE_LEVELS),
            "share_count": numerator,
            "percentage_of_official_total": str(percentage) if percentage is not None else None,
            "denominator_share_count": denominator if denominator > 0 else None,
            "denominator_source_level": total_level,
            "formula": "sum levels 12-15 shares / official total-row shares * 100",
        },
        "note": "Holder accounts are custody-account buckets and do not identify beneficial owners or investors.",
    }


def shareholder_distribution_block(read: TwmdShareholderDistributionRead) -> ResearchDataBlock:
    periods: dict[tuple[str, str], dict] = {}
    report_groups = sorted({(row.report_date, row.report_variant) for row in read.data})
    for report_date, variant in report_groups:
        period = _distribution_period(read, report_date, variant)
        if period is not None:
            periods[(report_date, variant)] = period
    latest_key = max(
        periods,
        default=None,
        key=lambda item: (item[0], item[1] == "bulk_current"),
    )
    latest = periods.get(latest_key) if latest_key else None
    previous = None
    comparison_reason = "latest_period_unavailable"
    changes = None
    if latest is not None and latest_key is not None:
        expected_previous = (date.fromisoformat(latest_key[0]) - timedelta(days=7)).isoformat()
        previous = periods.get((expected_previous, latest_key[1]))
        comparison_reason = None if previous else "previous_week_unavailable_or_variant_changed"
        if previous:
            latest_percentage = latest["large_holding"]["percentage_of_official_total"]
            previous_percentage = previous["large_holding"]["percentage_of_official_total"]
            changes = {
                "total_share_count": latest["total_share_count"] - previous["total_share_count"],
                "large_holding_share_count": (
                    latest["large_holding"]["share_count"] - previous["large_holding"]["share_count"]
                ),
                "large_holding_percentage_points": (
                    str(Decimal(latest_percentage) - Decimal(previous_percentage))
                    if latest_percentage is not None and previous_percentage is not None else None
                ),
            }
    evidence_rows = [_distribution_row_payload(row) for row in read.data]
    providers = sorted({row.provider for row in read.data})
    return ResearchDataBlock(
        data={
            "instrument_id": read.instrument_id,
            "native_unit": "shares",
            "report_date": latest["report_date"] if latest else None,
            "report_variant": latest["report_variant"] if latest else None,
            "latest": latest,
            "previous": previous,
            "changes": changes,
            "comparison_reason": comparison_reason,
            "latest_report_selection_policy": (
                "prefer_bulk_current_when_both_variants_share_latest_report_date"
            ),
            "periods": list(periods.values()),
        },
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": providers,
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {
                "instrument_id": read.instrument_id,
                "start": read.start_date,
                "end": read.end_date,
                "report_variant": read.report_variant,
            },
            "source_contract": None,
            "period": {"report_dates": sorted({row.report_date for row in read.data})},
            "source_report_date": latest["report_date"] if latest else None,
            "report_variant": latest["report_variant"] if latest else None,
            "latest_report_selection_policy": (
                "prefer_bulk_current_when_both_variants_share_latest_report_date"
            ),
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {"share_count": "shares", "holder_count": "custody accounts", "percentage": "percentage points"},
            "dataset_coverage": [asdict(row) for row in read.coverage],
            "coverage_error_reasons": dict(read.coverage_error_reasons),
            "selected_instrument_presence": "present" if read.data else "unknown",
            "per_period_provenance": evidence_rows,
            "revision": None,
            "capture_id": None,
            "payload_sha256": None,
            "large_holding_rule": {
                "minimum_shares": 400001,
                "source_levels": list(_TDCC_LARGE_LEVELS),
                "denominator": "official total row shares; level 17 bulk_current / level 16 historical_html",
                "adjustment_policy": "bulk_current level 16 is not a bucket and is not added to the numerator",
            },
            "provenance_limit": "The shareholder-distribution response has no row-level receipt, revision, or source contract fields.",
        },
    )


def margin_read_error(exc: TwmdReadError, selectors: dict[str, str]) -> ResearchDataBlock:
    return _error_block(exc, "/api/v1/margin-short-sale", selectors)


def shareholder_read_error(exc: TwmdReadError, selectors: dict[str, str]) -> ResearchDataBlock:
    return _error_block(exc, "/api/v1/shareholder-distribution", selectors)
