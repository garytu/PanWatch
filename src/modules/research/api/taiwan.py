"""Protected, bounded official Taiwan issuer research endpoint."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request

from marketdata.errors import TwmdReadError

from src.modules.research.taiwan_research import (
    TaiwanResearchService,
    _requested_blocks,
    get_taiwan_research_service,
    serialize_taiwan_research,
)
from src.platform.marketdata.models import is_market_enabled

router = APIRouter()


@router.get("/taiwan")
def get_taiwan_research(
    instrument_id: str = Query(..., min_length=1, max_length=32),
    start_date: str | None = Query(None, min_length=10, max_length=10),
    end_date: str | None = Query(None, min_length=10, max_length=10),
    start_month: str | None = Query(None, min_length=7, max_length=7),
    end_month: str | None = Query(None, min_length=7, max_length=7),
    fiscal_year: int | None = Query(None, ge=2024),
    fiscal_quarter: int | None = Query(None, ge=1, le=4),
    statement: str | None = Query(None, pattern="^(balance_sheet|comprehensive_income|cash_flows)$"),
    expected_financial_revision: str | None = Query(None, min_length=1, max_length=128),
    blocks: list[str] | None = Query(None),
):
    if not is_market_enabled("TW"):
        raise HTTPException(status_code=404, detail="Taiwan market is disabled")
    try:
        selectors = dict(
            start_date=start_date,
            end_date=end_date,
            start_month=start_month,
            end_month=end_month,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            statement=statement,
        )
        # Omitting `blocks` preserves the legacy all-block request contract.
        if blocks is not None:
            selectors["blocks"] = _requested_blocks(blocks)
        if expected_financial_revision is not None:
            selectors["expected_financial_revision"] = expected_financial_revision
        payload = get_taiwan_research_service().collect(instrument_id, **selectors)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # provider errors are normally isolated into block results
        raise HTTPException(status_code=503, detail="Taiwan research is temporarily unavailable") from exc
    return serialize_taiwan_research(payload)


@router.get("/taiwan/financial-periods")
def get_taiwan_financial_periods(
    request: Request,
    instrument_id: str = Query(..., min_length=1, max_length=32),
    report_scope: str = Query("consolidated", pattern="^(consolidated|individual)$"),
    statement: str | None = Query(None, pattern="^(balance_sheet|comprehensive_income|cash_flows)$"),
    limit: int = Query(40, ge=1, le=40),
    cursor: str | None = Query(None, max_length=2048),
    refresh: bool = Query(False),
):
    """Read retained period metadata independently of the selected fact report."""
    if not is_market_enabled("TW"):
        raise HTTPException(status_code=404, detail="Taiwan market is disabled")
    allowed = {"instrument_id", "report_scope", "statement", "limit", "cursor", "refresh"}
    if set(request.query_params.keys()) - allowed:
        raise HTTPException(status_code=422, detail="Unsupported financial-period selector")
    if any(len(request.query_params.getlist(name)) != 1 for name in request.query_params.keys()):
        raise HTTPException(status_code=422, detail="Financial-period selectors must appear once")
    selectors = {
        "instrument_id": instrument_id,
        "report_scope": report_scope,
        "statement": statement,
        "limit": limit,
    }
    if cursor is not None:
        selectors["cursor"] = cursor
    try:
        read = get_taiwan_research_service().financial_statement_periods(
            instrument_id,
            report_scope=report_scope,
            statement=statement,
            limit=limit,
            cursor=cursor,
            **({"refresh": True} if refresh else {}),
        )
        if read.coverage.status == "complete" and read.qualification.status == "qualified":
            index_status = "available"
            reason = read.coverage.reason
        elif read.qualification.status == "unsupported":
            index_status = "unsupported"
            reason = read.qualification.reason
        else:
            index_status = read.coverage.status
            reason = read.coverage.reason
        return serialize_taiwan_research({
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "index_status": index_status,
            "reason": reason,
            "selectors": {
                **selectors,
                "instrument_id": read.instrument_id,
                "venue": read.venue,
                "source": read.source,
            },
            "index": asdict(read),
            "error": None,
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TwmdReadError as exc:
        raw_code = exc.reason_code or "provider_error"
        known_codes = {
            "http_404": "endpoint_unsupported",
            "http_409": "index_changed",
            "http_503": "index_unavailable",
            "timeout": "index_timeout",
            "invalid_response": "invalid_response",
            "transport_error": "transport_error",
            "concurrency_limit": "concurrency_limit",
        }
        if raw_code in known_codes:
            code = known_codes[raw_code]
        elif raw_code.startswith("index_"):
            code = raw_code
        elif raw_code.startswith("http_"):
            code = "index_unavailable"
        else:
            code = raw_code
        if code in {"endpoint_unsupported", "instrument_not_found"}:
            status = "unknown"
        elif code in {
            "unsupported_venue", "unsupported_report_scope", "catalog_security_type_not_equity",
            "profile_industry_not_24", "listing_after_period",
        }:
            status = "unsupported"
        else:
            status = "error"
        return serialize_taiwan_research({
            "instrument_id": instrument_id,
            "endpoint": "/api/v1/financial-statement-periods",
            "index_status": status,
            "reason": code,
            "selectors": selectors,
            "index": None,
            "error": {"code": code, "http_status": exc.status_code},
        })
    except LookupError:
        return serialize_taiwan_research({
            "instrument_id": instrument_id,
            "endpoint": "/api/v1/financial-statement-periods",
            "index_status": "unknown",
            "reason": "instrument_not_found",
            "selectors": selectors,
            "index": None,
            "error": {"code": "instrument_not_found", "http_status": None},
        })
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Financial-period index is temporarily unavailable") from exc


@router.get("/taiwan/material-information")
def get_taiwan_material_information(
    instrument_id: str = Query(..., min_length=1, max_length=32),
    start_date: str = Query(..., min_length=10, max_length=10),
    end_date: str = Query(..., min_length=10, max_length=10),
    source: str = Query(..., pattern="^(current|history)$"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Read one source family for a bounded issuer and inclusive date window."""
    if not is_market_enabled("TW"):
        raise HTTPException(status_code=404, detail="Taiwan market is disabled")
    try:
        block = get_taiwan_research_service().material_information(
            instrument_id,
            start_date=start_date,
            end_date=end_date,
            source=source,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Material information is temporarily unavailable") from exc
    return serialize_taiwan_research({
        "instrument_id": block.evidence.get("instrument_id") or instrument_id,
        "source_family": source,
        "block": asdict(block),
    })


@router.get("/taiwan/corporate-actions")
def get_taiwan_corporate_actions(
    instrument_id: str = Query(..., min_length=1, max_length=32),
    start_date: str = Query(..., min_length=10, max_length=10),
    end_date: str = Query(..., min_length=10, max_length=10),
):
    """Read the realized TWSE action annotations for one bounded completed-date range."""
    if not is_market_enabled("TW"):
        raise HTTPException(status_code=404, detail="Taiwan market is disabled")
    try:
        block = get_taiwan_research_service().corporate_actions(
            instrument_id,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Taiwan corporate actions are temporarily unavailable") from exc
    canonical_id = block.evidence.get("instrument_id") or instrument_id
    return serialize_taiwan_research({"instrument_id": canonical_id, "block": asdict(block)})
