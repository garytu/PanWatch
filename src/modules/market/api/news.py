"""新聞 API - 基於資料來源配置"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock, DataSource
from src.platform.marketdata.collectors.news_collector import NewsCollector, NewsItem

router = APIRouter()

# 來源顯示名稱
SOURCE_LABELS = {
    "xueqiu": "雪球",
    "eastmoney_news": "東財資訊",
    "eastmoney": "東財公告",
}


class NewsItemResponse(BaseModel):
    source: str
    source_label: str
    external_id: str
    title: str
    content: str
    publish_time: str
    symbols: list[str]
    importance: int
    url: str = ""


@router.get("", response_model=list[NewsItemResponse])
async def get_news(
    symbols: str = Query(default="", description="股票程式碼，逗號分隔"),
    names: str = Query(default="", description="股票名稱，逗號分隔（優先使用，比 symbols 更穩定）"),
    hours: int = Query(default=168, ge=1, le=720, description="時間範圍（小時，預設7天）"),
    limit: int = Query(default=50, ge=1, le=200, description="返回數量"),
    filter_related: bool = Query(default=True, description="只顯示相關新聞"),
    source: str = Query(default="", description="來源過濾，逗號分隔：xueqiu/eastmoney_news/eastmoney"),
    db: Session = Depends(get_db),
):
    """
    獲取新聞列表（基於資料來源配置）

    - symbols: 股票程式碼過濾，逗號分隔，空則獲取所有自選股相關新聞
    - names: 股票名稱過濾，逗號分隔（前端直接傳遞名稱，更穩定）
    - hours: 時間範圍
    - limit: 返回數量限制
    - filter_related: 是否只顯示與自選股相關的新聞
    """
    # 獲取所有自選股（用於匹配）
    all_stocks = db.query(Stock).all()
    stock_map = {s.symbol: s.name for s in all_stocks}
    name_to_symbol = {s.name: s.symbol for s in all_stocks}

    # 解析股票 - 優先使用 names 引數
    if names:
        # 前端直接傳遞股票名稱
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        # 轉換為 symbol 列表（用於匹配和返回）
        symbol_list = [name_to_symbol.get(n) for n in name_list if name_to_symbol.get(n)]
        # 直接使用傳入的名稱構建 symbol_names
        passed_symbol_names = {name_to_symbol.get(n, ""): n for n in name_list if name_to_symbol.get(n)}
    elif symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        passed_symbol_names = {s: stock_map.get(s, s) for s in symbol_list}
    else:
        symbol_list = list(stock_map.keys())
        passed_symbol_names = stock_map

    if not symbol_list:
        return []

    source_filters = {s.strip() for s in source.split(",") if s.strip()} if source else set()

    # 構建匹配關鍵詞（股票程式碼 + 股票名稱）
    keywords = set(symbol_list)
    for sym in symbol_list:
        if sym in stock_map:
            keywords.add(stock_map[sym])

    # 基於資料來源配置構建採集器，直接傳遞股票名稱對映避免重複查庫
    collector = NewsCollector.from_database()
    news_items = await collector.fetch_all(
        symbols=symbol_list,
        since_hours=hours,
        symbol_names=passed_symbol_names,  # 直接傳遞已有的股票名稱對映
    )

    def is_related(item: NewsItem) -> bool:
        """判斷新聞是否與自選股相關"""
        # 公告類天然與股票相關
        if item.source == "eastmoney":
            return True
        # 已標記相關股票
        if item.symbols and any(s in symbol_list for s in item.symbols):
            return True
        # 標題或內容包含關鍵詞
        text = item.title + (item.content or "")
        return any(kw in text for kw in keywords)

    result = []
    for item in news_items:
        if source_filters and item.source not in source_filters:
            continue
        # 過濾不相關的新聞
        if filter_related and not is_related(item):
            continue

        # 標記匹配的股票
        matched_symbols = []
        text = item.title + (item.content or "")
        for sym, name in stock_map.items():
            if sym in symbol_list and (sym in text or name in text):
                matched_symbols.append(sym)

        result.append(NewsItemResponse(
            source=item.source,
            source_label=SOURCE_LABELS.get(item.source, item.source),
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            publish_time=item.publish_time.strftime("%Y-%m-%d %H:%M"),
            symbols=matched_symbols or item.symbols,
            importance=item.importance,
            url=item.url,
        ))

        if len(result) >= limit:
            break

    return result


@router.get("/sources")
def get_news_sources(db: Session = Depends(get_db)):
    """獲取已配置的新聞資料來源列表"""
    data_sources = (
        db.query(DataSource)
        .filter(DataSource.type == "news")
        .order_by(DataSource.priority)
        .all()
    )

    return [
        {
            "id": ds.provider,
            "name": ds.name,
            "enabled": ds.enabled,
            "priority": ds.priority,
        }
        for ds in data_sources
    ]
