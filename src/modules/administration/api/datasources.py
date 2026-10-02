"""資料來源管理 API"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import DataSource

logger = logging.getLogger(__name__)

router = APIRouter()


# 資料來源型別說明
TYPE_LABELS = {
    "news": "新聞資訊",
    "kline": "K線資料",
    "capital_flow": "資金流向",
    "quote": "即時行情",
    "events": "事件日曆",
    "chart": "K線截圖",
    "flash_news": "快訊",
    "fundamentals": "基本面",
    "dragon_tiger": "龍虎榜",
    "margin": "融資融券",
    "shareholders": "股東戶數",
    "dividend": "分紅",
    "northbound": "北向資金",
}


class DataSourceCreate(BaseModel):
    name: str
    type: str  # news / kline / capital_flow / quote / events / chart / flash_news
    provider: str
    config: dict = {}
    enabled: bool = True
    priority: int = 0
    supports_batch: bool = False
    test_symbols: list[str] = []


class DataSourceUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    provider: str | None = None
    config: dict | None = None
    enabled: bool | None = None
    priority: int | None = None
    supports_batch: bool | None = None
    test_symbols: list[str] | None = None


class DataSourceResponse(BaseModel):
    id: int
    name: str
    type: str
    type_label: str = ""
    provider: str
    config: dict
    enabled: bool
    priority: int
    supports_batch: bool = False
    test_symbols: list[str] = []

    class Config:
        from_attributes = True


# 已接入 marketdata 新引擎的資料型別(隨各型別逐步遷移擴充)
_ENGINE_ATTACHED_TYPES = {
    "news",
    "quote",
    "kline",
    "capital_flow",
    "events",
    "flash_news",
    "fundamentals",
    "dragon_tiger",
    "margin",
    "shareholders",
    "dividend",
    "northbound",
}


def _is_orphan(type_: str, provider: str) -> bool:
    """判定 (type, provider) 是否為孤兒資料來源:不在包內引擎 vendor 集合、也不在當前 seed 列表裡。

    與 server.reconcile_data_sources 的孤兒判定保持一致(legal = 包內集合 | seed 集合)。
    """
    from marketdata import PACKAGE_VENDORS_BY_TYPE
    from server import _seed_providers_by_type

    legal = PACKAGE_VENDORS_BY_TYPE.get(type_, frozenset()) | _seed_providers_by_type().get(type_, set())
    return provider not in legal


def _to_response(source: DataSource, health_map: dict | None = None) -> dict:
    """轉換為回應格式。health_map: {provider: 指標快照};缺失則 health=None。"""
    health = (health_map or {}).get(source.provider)
    return {
        "id": source.id,
        "name": source.name,
        "type": source.type,
        "type_label": TYPE_LABELS.get(source.type, source.type),
        "provider": source.provider,
        "config": source.config or {},
        "enabled": source.enabled,
        "priority": source.priority,
        "supports_batch": source.supports_batch or False,
        "test_symbols": source.test_symbols or [],
        "engine_attached": source.type in _ENGINE_ATTACHED_TYPES,
        "health": health,
        "is_orphan": _is_orphan(source.type, source.provider),
    }


@router.get("")
def list_datasources(type: str | None = None, db: Session = Depends(get_db)):
    """獲取資料來源列表，可按型別篩選"""
    query = db.query(DataSource)
    if type:
        query = query.filter(DataSource.type == type)
    sources = query.order_by(DataSource.type, DataSource.priority, DataSource.id).all()
    from src.platform.marketdata.marketdata_client import get_market_data
    health_map = get_market_data().health()
    return [_to_response(s, health_map) for s in sources]


@router.get("/types")
def get_datasource_types():
    """獲取資料來源型別列表"""
    return [{"type": k, "label": v} for k, v in TYPE_LABELS.items()]


@router.post("/reset-to-seed")
def reset_datasources_to_seed(db: Session = Depends(get_db)):
    """恢復內建資料來源預設值:補齊/刪除孤兒並重置測試股票,保留使用者配置與憑證。"""
    from server import reconcile_data_sources

    summary = reconcile_data_sources(db, reset_test_symbols=True)
    logger.info(f"資料來源手動對帳完成: {summary}")
    return summary


@router.get("/{source_id}")
def get_datasource(source_id: int, db: Session = Depends(get_db)):
    """獲取單個資料源"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="資料來源不存在")
    return _to_response(source)


@router.post("")
def create_datasource(data: DataSourceCreate, db: Session = Depends(get_db)):
    """建立資料來源"""
    source = DataSource(
        name=data.name,
        type=data.type,
        provider=data.provider,
        config=data.config,
        enabled=data.enabled,
        priority=data.priority,
        supports_batch=data.supports_batch,
        test_symbols=data.test_symbols,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    logger.info(f"建立資料來源: {source.name} ({source.provider})")
    return _to_response(source)


@router.put("/{source_id}")
def update_datasource(
    source_id: int, data: DataSourceUpdate, db: Session = Depends(get_db)
):
    """更新資料來源"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="資料來源不存在")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(source, key, value)

    db.commit()
    db.refresh(source)
    logger.info(f"更新資料來源: {source.name}")
    return _to_response(source)


@router.delete("/{source_id}")
def delete_datasource(source_id: int, db: Session = Depends(get_db)):
    """刪除資料來源"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="資料來源不存在")

    db.delete(source)
    db.commit()
    logger.info(f"刪除資料來源: {source.name}")
    return {"ok": True, "message": f"已刪除 {source.name}"}


@router.post("/{source_id}/test")
async def test_datasource(source_id: int, db: Session = Depends(get_db)):
    """測試資料來源連線"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="資料來源不存在")

    from src.modules.market.data_collector import get_collector_manager

    manager = get_collector_manager()
    manager.clear_logs()

    result = await manager.test_source(source)

    # 不用 success / data 作為頂層欄位,避免被 ResponseWrapperMiddleware 當成業務回應
    # 拆解後導致 metadata 丟失(詳見 src/web/response.py:59 的特殊分支)。
    return {
        "test_passed": result.success,
        "source_name": source.name,
        "source_type": source.type,
        "type_label": TYPE_LABELS.get(source.type, source.type),
        "provider": source.provider,
        "supports_batch": source.supports_batch or False,
        "test_symbols": result.test_symbols or source.test_symbols or [],
        "count": result.count,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "items": result.data,
        "errors": result.errors,
        "logs": manager.get_logs(),
    }
