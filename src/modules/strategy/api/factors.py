"""因子權重 API(M5):只讀列表 + 手動覆蓋(pin / 設權重 / 開關自動標定)。

回應由 ResponseWrapperMiddleware 統一包成 {code,data,message},路由直接返回原始資料。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.strategy.factor_weights import get_all_factor_weights, set_factor_weight
from src.platform.persistence.database import get_db

router = APIRouter()


class FactorWeightUpdate(BaseModel):
    """手動覆蓋入參,均可選(只傳要改的欄位)。"""

    weight: float | None = None
    is_pinned: bool | None = None
    auto_calibrate: bool | None = None


@router.get("/weights")
def list_weights(db: Session = Depends(get_db)):
    """列出所有市場 × 因子的權重 + 最近 IC/IR 觀測。"""
    return {"items": get_all_factor_weights(db=db)}


@router.post("/weights/{factor_code}/{market}")
def update_weight(
    factor_code: str, market: str, payload: FactorWeightUpdate,
    db: Session = Depends(get_db),
):
    """手動覆蓋某因子權重 / pin / 開關自動標定(權重變化寫 manual 審計)。"""
    try:
        return set_factor_weight(
            factor_code, market,
            weight=payload.weight, is_pinned=payload.is_pinned,
            auto_calibrate=payload.auto_calibrate, db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
