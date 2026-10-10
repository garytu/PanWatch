"""Typed TWMD profile and monthly-revenue blocks for later research merging."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from marketdata.errors import TwmdReadError
from marketdata.types import TwmdCompanyProfileRead, TwmdMonthlyRevenueRead


@dataclass(frozen=True)
class ResearchDataBlock:
    """A source-specific result that can be merged without a shared latest date."""

    data: dict[str, Any] | None
    status: str
    reason: str
    evidence: dict[str, Any]


def _uniform(values: list[Any]) -> Any:
    if not values:
        return None
    first = values[0]
    return first if all(value == first for value in values) else "mixed"


def _read_error(exc: TwmdReadError, endpoint: str, selectors: dict[str, str]) -> ResearchDataBlock:
    reason = exc.reason_code or (f"http_{exc.status_code}" if exc.status_code else "provider_error")
    return ResearchDataBlock(
        data=None,
        status="error",
        reason=reason,
        evidence={
            "provider": "twmd",
            "endpoint": endpoint,
            "selectors": selectors,
            "http_status": exc.status_code,
            "source_contract": None,
            "period": None,
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {},
            "dataset_coverage": None,
            "selected_instrument_presence": None,
            "revision": None,
            "capture_id": None,
            "payload_sha256": None,
        },
    )


def company_profile_block(read: TwmdCompanyProfileRead) -> ResearchDataBlock:
    """Adapt a typed latest-only profile read into the shared research block shape."""
    profile = read.profile
    snapshot = read.latest_snapshot
    return ResearchDataBlock(
        data={
            "instrument_id": read.instrument_id,
            "qualification": read.qualification,
            "qualification_reason": read.qualification_reason,
            "latest_snapshot_presence": read.latest_snapshot_presence,
            "latest_snapshot": asdict(snapshot) if snapshot is not None else None,
            "profile": asdict(profile) if profile is not None else None,
            "units": dict(read.units),
        },
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {"instrument_id": read.instrument_id},
            "source_contract": read.source_contract,
            "period": {
                "profile_report_date": profile.report_date if profile else None,
                "latest_snapshot_report_date": snapshot.report_date if snapshot else None,
            },
            "source_report_date": profile.report_date if profile else None,
            "publication_time": None,
            "source_received_at_utc": profile.original_received_at_utc if profile else None,
            "profile_received_at_utc": profile.original_received_at_utc if profile else None,
            "latest_snapshot_received_at_utc": snapshot.received_at_utc if snapshot else None,
            "retained_profile": {
                "report_date": profile.report_date,
                "received_at_utc": profile.original_received_at_utc,
                "revision": profile.revision,
                "capture_id": profile.snapshot_capture_id,
                "payload_sha256": profile.payload_sha256,
            } if profile else None,
            "latest_snapshot": asdict(snapshot) if snapshot else None,
            "served_at": None,
            "units": dict(read.units),
            "dataset_coverage": read.coverage_status,
            "selected_instrument_presence": read.latest_snapshot_presence,
            "revision": profile.revision if profile else None,
            "capture_id": profile.snapshot_capture_id if profile else (
                snapshot.capture_id if snapshot else None
            ),
            "payload_sha256": profile.payload_sha256 if profile else (
                snapshot.payload_sha256 if snapshot else None
            ),
        },
    )


def monthly_revenue_block(read: TwmdMonthlyRevenueRead) -> ResearchDataBlock:
    """Adapt a typed monthly series while keeping per-month receipts and revisions."""
    rows = [month.row for month in read.months if month.row is not None]
    source_contracts = [row.source_contract for row in rows]
    report_dates = [row.report_date for row in rows]
    receipts = [row.received_at_utc for row in rows]
    revisions = [row.revision for row in rows]
    coverage_contracts = [item.source_contract for item in read.coverage]
    coverage_report_dates = [item.report_date for item in read.coverage]
    coverage_receipts = [item.received_at_utc for item in read.coverage]
    source_contract = _uniform(source_contracts + coverage_contracts)
    source_report_date = _uniform(report_dates + coverage_report_dates)
    source_received_at = _uniform(receipts + coverage_receipts)
    provenance_by_month = [
        {
            "data_month": month.data_month,
            "presence": month.presence,
            "retained_row": ({
                "source": month.row.source,
                "source_contract": month.row.source_contract,
                "report_date": month.row.report_date,
                "acquisition_date": month.row.acquisition_date,
                "received_at_utc": month.row.received_at_utc,
                "revision": month.row.revision,
                "capture_id": month.row.capture_id,
                "content_hash": month.row.content_hash,
                "payload_sha256": month.row.payload_sha256,
                "request_scope": month.row.request_scope,
                "source_url": month.row.source_url,
            } if month.row else None),
        }
        for month in read.months
    ]
    return ResearchDataBlock(
        data={
            "instrument_id": read.instrument_id,
            "dataset": read.dataset,
            "qualification": read.qualification,
            "qualification_reason": read.qualification_reason,
            "coverage_status": read.coverage_status,
            "current_catalog_evidence": dict(read.current_catalog_evidence),
            "units": dict(read.units),
            "coverage": [asdict(item) for item in read.coverage],
            "months": [asdict(item) for item in read.months],
        },
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {
                "instrument_id": read.instrument_id,
                "start_month": read.start_month[:7],
                "end_month": read.end_month[:7],
            },
            "source_contract": source_contract,
            "period": {"data_months": [month.data_month for month in read.months]},
            "source_report_date": source_report_date,
            "publication_time": None,
            "source_received_at_utc": source_received_at,
            "per_month_coverage": [asdict(item) for item in read.coverage],
            "per_month_presence_and_provenance": provenance_by_month,
            "served_at": read.served_at,
            "units": dict(read.units),
            "dataset_coverage": read.coverage_status,
            "selected_instrument_presence": _uniform([month.presence for month in read.months]),
            "revision": _uniform(revisions),
            "capture_id": _uniform([row.capture_id for row in rows]),
            "payload_sha256": _uniform([row.payload_sha256 for row in rows]),
        },
    )


class TwmdProfileRevenueResearch:
    """Small backend adapter; a later research service can merge its blocks."""

    def __init__(self, market_data=None):
        self._market_data = market_data

    @property
    def market_data(self):
        if self._market_data is None:
            from src.platform.marketdata.marketdata_client import get_market_data

            self._market_data = get_market_data()
        return self._market_data

    def company_profile(self, instrument_id: str) -> ResearchDataBlock:
        try:
            read = self.market_data.company_profile(instrument_id)
        except TwmdReadError as exc:
            return _read_error(exc, "/api/v1/company-profiles", {"instrument_id": instrument_id})
        return company_profile_block(read)

    def monthly_revenues(
        self,
        instrument_id: str,
        start_month: str,
        end_month: str,
        *,
        today_taipei=None,
    ) -> ResearchDataBlock:
        selectors = {
            "instrument_id": instrument_id,
            "start_month": start_month,
            "end_month": end_month,
        }
        try:
            read = self.market_data.monthly_revenues(
                instrument_id, start_month, end_month, today_taipei=today_taipei
            )
        except TwmdReadError as exc:
            return _read_error(exc, "/api/v1/monthly-revenues", selectors)
        return monthly_revenue_block(read)

    def adjacent_monthly_revenues(
        self,
        instrument_id: str,
        month: str,
        *,
        today_taipei=None,
    ) -> ResearchDataBlock:
        selectors = {"instrument_id": instrument_id, "month": month}
        try:
            read = self.market_data.adjacent_monthly_revenues(
                instrument_id, month, today_taipei=today_taipei
            )
        except TwmdReadError as exc:
            return _read_error(exc, "/api/v1/monthly-revenues", selectors)
        block = monthly_revenue_block(read)
        return ResearchDataBlock(
            data=block.data,
            status=block.status,
            reason=block.reason,
            evidence={**block.evidence, "selectors": selectors},
        )
