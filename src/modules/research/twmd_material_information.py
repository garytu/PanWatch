"""Typed adaptation for the independent TWSE current and MOPS history feeds."""

from __future__ import annotations

from dataclasses import asdict
import re

from marketdata.types import TwmdMaterialInformationRead

from src.modules.research.twmd_profile_revenue import ResearchDataBlock


def material_information_source_reference(event: dict) -> dict | None:
    """Rebuild only the official MOPS detail selector encoded in provider_key."""
    provider_key = event.get("provider_key")
    match = re.fullmatch(r"sii:([0-9]{4}):([0-9]{7}):([0-9]+)", provider_key or "")
    instrument_id = event.get("instrument_id")
    if not match or instrument_id != f"TWSE:{match.group(1)}":
        return None
    params = {
        "serialNumber": match.group(3),
        "enterDate": match.group(2),
        "marketKind": "sii",
        "companyId": match.group(1),
    }
    return {
        "label": "MOPS detail request",
        "method": "POST",
        "url": "https://mops.twse.com.tw/mops/api/t05st01_detail",
        "selectors": params,
        "is_navigable_permalink": False,
    }


def material_information_block(read: TwmdMaterialInformationRead) -> ResearchDataBlock:
    """Keep source-family identity, capture evidence, and exact text together."""
    rows = [asdict(row) for row in read.data]
    for row in rows:
        row["source_reference"] = material_information_source_reference(row)
    current = read.source_family == "current"
    if read.unsupported_reason:
        status, reason = "unsupported", read.unsupported_reason
    elif not read.schema_ready:
        status, reason = "missing", "optional_material_information_schema_missing"
    elif read.truncated:
        status, reason = "partial", "result_truncated"
    elif current:
        # A current snapshot never proves that an issuer had no event in a date window.
        status = "partial" if rows else "missing"
        reason = "current_snapshot_observations_only" if rows else (
            "no_current_capture" if read.latest_capture is None else "no_retained_current_observation"
        )
    else:
        status_by_coverage = {
            "AVAILABLE": "available",
            "EMPTY": "empty",
            "PARTIAL": "partial",
            "MISSING": "missing",
        }
        status = status_by_coverage[read.coverage_status]
        reason = {
            "available": "complete_selected_history_window_with_events",
            "empty": "complete_selected_history_window_without_events",
            "partial": "selected_history_window_has_missing_coverage",
            "missing": "selected_history_window_has_no_complete_acquisition",
        }[status]
        if read.truncated:
            status, reason = "partial", "result_truncated"

    return ResearchDataBlock(
        data={
            "instrument_id": read.instrument_id,
            "source_family": read.source_family,
            "events": rows,
        },
        status=status,
        reason=reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": "/api/v1/material-information",
            "source_contract": read.source_contract,
            "source_family": read.source_family,
            "selectors": {
                "instrument_id": read.instrument_id,
                "start_date": read.start_date,
                "end_date": read.end_date,
                "source": read.source_family,
                "limit": read.limit,
            },
            "query_date_filter": {"start_date": read.start_date, "end_date": read.end_date},
            "dataset_coverage": read.coverage_status,
            "history_complete": read.history_complete,
            "partial_current_day": read.partial_current_day,
            "retained_count": read.retained_count,
            "returned_count": read.returned_count,
            "truncated": read.truncated,
            "latest_capture": asdict(read.latest_capture) if read.latest_capture else None,
            # For history, query_year and coverage_through describe acquisition scope,
            # while start_date/end_date above select announcement dates.
            "acquisitions": [asdict(item) for item in read.acquisitions],
            "missing_dates": list(read.missing_dates),
            "source_received_at_utc": (
                read.latest_capture.received_at_utc if read.latest_capture else None
            ),
            "served_at": None,
            "publication_time": None,
            "source_link_policy": (
                "The response does not provide a per-event source URL."
                if current else
                "MOPS detail references are reconstructed only from the validated provider_key."
            ),
            "response_headers": dict(read.response_headers),
            "history_note": read.history_note,
        },
    )
