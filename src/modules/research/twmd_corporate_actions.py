"""Source-preserving blocks for bounded realized TWSE corporate-action results."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from marketdata.errors import TwmdReadError
from marketdata.types import TwmdCapitalReductionRead, TwmdExRightDividendRead

from src.modules.research.twmd_profile_revenue import ResearchDataBlock


_EX_RIGHT_ENDPOINT = "/api/v1/ex-right-dividend-results"
_CAPITAL_ENDPOINT = "/api/v1/capital-reduction-results"


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, TwmdReadError):
        status = exc.status_code if type(exc.status_code) is int and 100 <= exc.status_code <= 599 else None
        reason = exc.reason_code or (f"http_{status}" if status else "provider_error")
        if reason not in {"timeout", "transport_error", "invalid_response", "provider_error"} and not re.fullmatch(r"http_\d{3}", reason):
            reason = f"http_{status}" if status else "provider_error"
    elif isinstance(exc, TimeoutError):
        reason, status = "timeout", None
    elif isinstance(exc, (TypeError, ValueError)):
        reason, status = "invalid_response", None
    else:
        reason, status = "provider_error", None
    return {"status": "error", "reason": reason, "http_status": status, "data": []}


def _read_part(read, error: Exception | None, endpoint: str, selectors: dict[str, str], *, product_floor: str) -> dict[str, Any]:
    if error is not None:
        return {**_error(error), "endpoint": endpoint, "selectors": selectors, "dataset_coverage": "unknown", "product_history_floor": product_floor}
    if read is None:
        return {
            "status": "unknown",
            "reason": "outside_product_history",
            "http_status": None,
            "data": [],
            "endpoint": endpoint,
            "selectors": selectors,
            "dataset_coverage": "unknown",
            "product_history_floor": product_floor,
        }
    rows = [asdict(item) for item in read.data]
    return {
        "status": read.status,
        "reason": read.reason,
        "http_status": None,
        "data": rows,
        "endpoint": read.endpoint,
        "selectors": {"instrument_id": read.instrument_id, "start": read.start_date, "end": read.end_date},
        "dataset_coverage": "unknown",
        "product_history_floor": product_floor,
    }


def corporate_actions_block(
    instrument_id: str,
    start_date: str,
    end_date: str,
    *,
    ex_right: TwmdExRightDividendRead | None = None,
    ex_right_error: Exception | None = None,
    capital_reduction: TwmdCapitalReductionRead | None = None,
    capital_reduction_error: Exception | None = None,
) -> ResearchDataBlock:
    """Combine two independent list reads without upgrading absent rows to coverage."""
    selectors = {"instrument_id": instrument_id, "start_date": start_date, "end_date": end_date}
    ex_start = max(start_date, "2003-05-05")
    cap_start = max(start_date, "2011-01-01")
    ex_applicable = ex_start <= end_date
    cap_applicable = cap_start <= end_date
    ex_part = _read_part(
        ex_right if ex_applicable else None,
        ex_right_error,
        _EX_RIGHT_ENDPOINT,
        {"instrument_id": instrument_id, "start": ex_start, "end": end_date},
        product_floor="2003-05-05",
    )
    cap_part = _read_part(
        capital_reduction if cap_applicable else None,
        capital_reduction_error,
        _CAPITAL_ENDPOINT,
        {"instrument_id": instrument_id, "start": cap_start, "end": end_date},
        product_floor="2011-01-01",
    )
    event_rows = [
        {"date": row["effective_date"], "kind": row["action_kind"], "dataset": "TWT49U"}
        for row in ex_part["data"]
    ] + [
        {"date": row["recovery_date"], "kind": row["reduction_reason"], "dataset": "TWTAUU"}
        for row in cap_part["data"]
    ]
    event_rows.sort(key=lambda item: (item["date"], item["dataset"], item["kind"]))
    errors = [part for part in (ex_part, cap_part) if part["status"] == "error"]
    has_rows = bool(ex_part["data"] or cap_part["data"])
    if errors:
        status = "partial" if len(errors) == 1 else "error"
        reason = "one_corporate_action_source_failed" if len(errors) == 1 else "corporate_action_sources_failed"
    elif has_rows:
        status, reason = "available", "realized_events_returned_coverage_unknown"
    else:
        status, reason = "unknown", "coverage_not_returned"
    data = {
        "instrument_id": instrument_id,
        "ex_right_dividend": ex_part,
        "capital_reduction": cap_part,
        "known_event_dates": event_rows,
        "price_interpretation": {
            "prior_close_and_reference_price_are_not_cash_dividend_amounts": True,
            "rights_dividend_value_is_combined_adjustment_not_cash_dividend": True,
            "announcement_time": None,
            "payment_time": None,
            "raw_daily_bars_are_not_adjusted_by_these_annotations": True,
        },
    }
    return ResearchDataBlock(
        data=data,
        status=status,
        reason=reason,
        evidence={
            "provider": "twmd",
            "instrument_id": instrument_id,
            "endpoint": f"{_EX_RIGHT_ENDPOINT}, {_CAPITAL_ENDPOINT}",
            "selectors": selectors,
            "source_contract": "TWSE TWT49U and TWTAUU realized results",
            "period": {"known_event_dates": event_rows},
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {
                "prices": "TWD per share",
                "rights_dividend_value": "TWD per share; combined rights/dividend adjustment, not a cash payment",
            },
            "dataset_coverage": "unknown",
            "selected_instrument_presence": "present" if has_rows else "unknown",
            "coverage_reason": "list endpoints expose no coverage, acquisition receipt, or revision; an empty list is unknown",
            "ex_right_dividend": {key: value for key, value in ex_part.items() if key != "data"},
            "capital_reduction": {key: value for key, value in cap_part.items() if key != "data"},
            "revision": None,
            "capture_id": None,
            "payload_sha256": None,
            "interpretation": (
                "These are retained realized TWSE calculation results, not a complete announcement, payment, or adjustment stream. "
                "The reported prior close, reference prices, and combined rights/dividend adjustment are price fields, not cash dividends. "
                "Announcement and payment times are unknown. Missing or empty results do not establish that no corporate action occurred. "
                "Daily bars remain raw and are not adjusted by this block."
            ),
        },
    )
