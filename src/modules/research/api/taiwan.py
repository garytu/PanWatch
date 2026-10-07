"""Protected, bounded official Taiwan issuer research endpoint."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from src.modules.research.taiwan_research import (
    TaiwanResearchService,
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
):
    if not is_market_enabled("TW"):
        raise HTTPException(status_code=404, detail="Taiwan market is disabled")
    try:
        payload = get_taiwan_research_service().collect(
            instrument_id,
            start_date=start_date,
            end_date=end_date,
            start_month=start_month,
            end_month=end_month,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            statement=statement,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # provider errors are normally isolated into block results
        raise HTTPException(status_code=503, detail="Taiwan research is temporarily unavailable") from exc
    return serialize_taiwan_research(payload)


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
