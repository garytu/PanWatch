"""建議池 API"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db
from src.modules.automation.suggestion_pool import (
    get_suggestions_for_stock,
    get_latest_suggestions,
    cleanup_expired_suggestions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{symbol}")
def get_stock_suggestions(
    symbol: str,
    market: str = Query("", description="市場程式碼: CN/HK/US"),
    include_expired: bool = Query(False, description="是否包含已過期建議"),
    limit: int = Query(10, description="返回數量限制"),
    db: Session = Depends(get_db),
):
    """
    獲取某隻股票的所有建議

    返回該股票的建議列表，按時間倒序排列
    """
    suggestions = get_suggestions_for_stock(
        stock_symbol=symbol,
        stock_market=(market or "").strip().upper() or None,
        include_expired=include_expired,
        limit=limit,
    )
    return suggestions


@router.get("/", name="get_suggestions")
@router.get("", include_in_schema=False)  # 同時處理無斜槓的情況
def get_all_latest_suggestions(
    symbols: str = Query(None, description="股票程式碼列表，逗號分隔"),
    stock_keys: str = Query(
        None, description="市場+程式碼列表，格式 CN:600519,HK:00700,US:AAPL"
    ),
    include_expired: bool = Query(False, description="是否包含已過期建議"),
    db: Session = Depends(get_db),
):
    """
    獲取所有股票的最新建議

    每隻股票只返回最新的一條有效建議
    用於持倉頁面快速展示各股票的最新建議
    """
    symbol_list = None
    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

    key_list = None
    if stock_keys:
        parsed: list[tuple[str, str]] = []
        for part in stock_keys.split(","):
            text = (part or "").strip()
            if not text:
                continue
            if ":" not in text:
                parsed.append((text.strip().upper(), "CN"))
                continue
            market, symbol = text.split(":", 1)
            mkt = (market or "CN").strip().upper() or "CN"
            sym = (symbol or "").strip().upper()
            if sym:
                parsed.append((sym, mkt))
        key_list = parsed or None

    suggestions = get_latest_suggestions(
        stock_symbols=symbol_list,
        stock_keys=key_list,
        include_expired=include_expired,
    )
    return suggestions


@router.delete("/cleanup")
def cleanup_suggestions(
    days: int = Query(7, description="清理多少天前的記錄"),
    db: Session = Depends(get_db),
):
    """
    清理過期的建議記錄

    預設清理 7 天前的記錄
    """
    count = cleanup_expired_suggestions(days=days)
    return {"deleted": count}
