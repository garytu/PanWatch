"""Source-preserving adapter for retained TWMD financial-statement facts."""

from __future__ import annotations

from dataclasses import asdict

from marketdata.types import TwmdFinancialStatementRead

from src.modules.research.twmd_profile_revenue import ResearchDataBlock


def financial_statement_block(read: TwmdFinancialStatementRead) -> ResearchDataBlock:
    report = asdict(read.report) if read.report is not None else None
    facts = [asdict(fact) for fact in read.facts]
    coverage = asdict(read.coverage)
    qualification = asdict(read.qualification)
    return ResearchDataBlock(
        data={
            "instrument_id": read.instrument_id,
            "fiscal_year": read.fiscal_year,
            "fiscal_quarter": read.fiscal_quarter,
            "report_scope": read.report_scope,
            "statement": read.statement,
            "limit": read.limit,
            "qualification": qualification,
            "coverage": coverage,
            "report": report,
            "facts": facts,
            "total_fact_count": read.total_fact_count,
            "returned_fact_count": read.returned_fact_count,
            "truncated": read.truncated,
        },
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {
                "instrument_id": read.instrument_id,
                "fiscal_year": read.fiscal_year,
                "fiscal_quarter": read.fiscal_quarter,
                "report_scope": read.report_scope,
                "statement": read.statement,
                "limit": read.limit,
            },
            "source_contract": report["source_contract"] if report else None,
            "period": {
                "fiscal_year": read.fiscal_year,
                "fiscal_quarter": read.fiscal_quarter,
                "fact_periods": [asdict(fact.context.period) for fact in read.facts],
            },
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": report["original_received_at_utc"] if report else None,
            "report_original_received_at_utc": report["original_received_at_utc"] if report else None,
            "latest_discovery_received_at_utc": coverage["original_received_at_utc"],
            "served_at": None,
            "units": {"fact_values": "exact decimal strings; unit is preserved per fact"},
            "dataset_coverage": coverage["status"],
            "selected_instrument_presence": coverage["latest_discovery_presence"],
            "report_provenance": report,
            "latest_discovery": coverage,
            "qualification": qualification,
            "total_fact_count": read.total_fact_count,
            "returned_fact_count": read.returned_fact_count,
            "truncated": read.truncated,
        },
    )


def financial_statement_unsupported_block(
    instrument_id: str,
    fiscal_year: int,
    fiscal_quarter: int,
    reason: str,
    *,
    security_type: str | None = None,
    statement: str | None = None,
) -> ResearchDataBlock:
    qualification = {
        "status": "unsupported",
        "reason": reason,
        "industry_code": None,
        "catalog_evidence": {
            "instrument_id": instrument_id,
            "security_type": security_type,
        } if security_type else None,
        "profile_evidence": None,
    }
    coverage = {
        "status": "MISSING",
        "reason": reason,
        "latest_discovery_presence": "missing",
        "capture_id": None,
        "original_received_at_utc": None,
    }
    return ResearchDataBlock(
        data={
            "instrument_id": instrument_id,
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "report_scope": "consolidated",
            "statement": statement,
            "limit": 1000,
            "qualification": qualification,
            "coverage": coverage,
            "report": None,
            "facts": [],
            "total_fact_count": 0,
            "returned_fact_count": 0,
            "truncated": False,
        },
        status="unsupported",
        reason=reason,
        evidence={
            "provider": "twmd",
            "instrument_id": instrument_id,
            "endpoint": "/api/v1/financial-statements",
            "selectors": {
                "instrument_id": instrument_id,
                "fiscal_year": fiscal_year,
                "fiscal_quarter": fiscal_quarter,
                "report_scope": "consolidated",
                "statement": statement,
                "limit": 1000,
            },
            "source_contract": None,
            "period": {"fiscal_year": fiscal_year, "fiscal_quarter": fiscal_quarter},
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {},
            "dataset_coverage": "MISSING",
            "selected_instrument_presence": "missing",
            "qualification": qualification,
            "latest_discovery": coverage,
        },
    )


__all__ = ["financial_statement_block", "financial_statement_unsupported_block"]
