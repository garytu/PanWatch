"""PanWatch ↔ marketdata 接線:DB 配置埠 + 單例 + flag 門控的報價相容層。

- DbConfigProvider:把 DataSource 表對映成 marketdata 的 SourceConfig(實現 ConfigProvider 埠)。
- get_market_data():程式級單例(無狀態 vendor + 現查 DB 的配置埠)。
- md_quote_rows():新包 MarketData.quotes 轉 dict,返回 list[dict](與舊 orchestrator 輸出同形)。
- md_news()/md_news_by_keyword():新包 MarketData.news/news_by_keyword 轉 host NewsItem。
"""

from __future__ import annotations

import logging

from marketdata import MarketData, Quote, SourceConfig

logger = logging.getLogger(__name__)


class DbConfigProvider:
    """ConfigProvider 埠實現:從 DataSource 表按 priority 讀某型別的啟用源。"""

    def _query_rows(self, datatype: str) -> list:
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import DataSource

        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == datatype, DataSource.enabled == True)  # noqa: E712
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        import os
        market_code = (market or "").strip().upper()

        # 台股市場專用路由 (TW)
        if market_code == "TW":
            # 1. 盤中即時行情/五檔: 僅支援單一外部 Provider，配置取自 .env
            if datatype == "quote":
                feed_url = os.environ.get("EXTERNAL_QUOTE_FEED_URL") or os.environ.get("TW_QUOTE_FEED_URL") or "http://127.0.0.1:8088"
                feed_token = os.environ.get("EXTERNAL_QUOTE_FEED_TOKEN") or os.environ.get("TW_QUOTE_FEED_TOKEN") or ""
                return [
                    SourceConfig(
                        vendor="external_quote",
                        priority=0,
                        enabled=True,
                        config={"base_url": feed_url, "token": feed_token},
                        supports_batch=True,
                    )
                ]

            # 2. 盤中分K: 僅支援單一外部 Provider，配置取自 .env
            if datatype == "intraday_kline":
                feed_url = os.environ.get("EXTERNAL_QUOTE_FEED_URL") or os.environ.get("TW_QUOTE_FEED_URL") or "http://127.0.0.1:8088"
                feed_token = os.environ.get("EXTERNAL_QUOTE_FEED_TOKEN") or os.environ.get("TW_QUOTE_FEED_TOKEN") or ""
                return [
                    SourceConfig(
                        vendor="external_kline",
                        priority=0,
                        enabled=True,
                        config={"base_url": feed_url, "token": feed_token},
                        supports_batch=False,
                    )
                ]

            # 3. 日K、基本面、三大法人、融資融券、除權息、新聞: FinMind Provider，配置取自 .env
            if datatype in {"kline", "fundamentals", "capital_flow", "margin", "dividend", "news"}:
                fm_token = os.environ.get("FINMIND_API_TOKEN") or ""
                return [
                    SourceConfig(
                        vendor="finmind",
                        priority=0,
                        enabled=True,
                        config={"token": fm_token},
                        supports_batch=False,
                    )
                ]

        rows = self._query_rows(datatype)
        has_us_fallback = any(
            row.provider in {"stooq", "yahoo"} for row in rows
        )
        sources = []
        for row in rows:
            # 騰訊美股介面在當前網路出口穩定返回 501；A/HK 仍保留騰訊作為主源。
            if (
                datatype == "kline"
                and market_code == "US"
                and row.provider == "tencent"
                and has_us_fallback
            ):
                continue
            sources.append(
                SourceConfig(
                    vendor=row.provider,
                    priority=row.priority,
                    enabled=True,
                    config=row.config or {},
                    supports_batch=bool(row.supports_batch),
                )
            )
        return sources



_md: MarketData | None = None


def get_market_data() -> MarketData:
    """程式級單例。vendor 無狀態、配置現查 DB,故無需失效鉤子。"""
    global _md
    if _md is None:
        _md = MarketData(config=DbConfigProvider())
    return _md


def reset_market_data() -> None:
    """測試或熱過載時重置單例。"""
    global _md
    _md = None


def _quote_to_row(q: Quote) -> dict:
    """marketdata.Quote → 舊 orchestrator 同形 dict。"""
    return {
        "symbol": q.symbol,
        "name": q.name,
        "market": q.market,
        "current_price": q.current_price,
        "change_pct": q.change_pct,
        "change_amount": q.change_amount,
        "prev_close": q.prev_close,
        "open_price": q.open_price,
        "high_price": q.high_price,
        "low_price": q.low_price,
        "volume": q.volume,
        "turnover": q.turnover,
        "turnover_rate": q.turnover_rate,
        "volume_ratio": q.volume_ratio,
        "pe_ratio": q.pe_ratio,
        "circulating_market_value": q.circulating_market_value,
        "total_market_value": q.total_market_value,
    }


def md_quote_rows(symbols: list[str], market: str) -> list[dict]:
    """批次報價,返回 list[dict](與舊 orchestrator 輸出同形)。

    同步函式;async 呼叫方用 `await asyncio.to_thread(md_quote_rows, ...)`。
    """
    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [_quote_to_row(q) for q in quotes]


def _article_to_newsitem(a):
    """marketdata.NewsArticle → host NewsItem(同名欄位直拷)。

    lazy import 避免與 news_collector 的模組級迴圈引用(news_collector 會
    在模組級 import 本模組的 md_news)。
    """
    from src.platform.marketdata.collectors.news_collector import NewsItem

    return NewsItem(
        source=a.source,
        external_id=a.external_id,
        title=a.title,
        content=a.content,
        publish_time=a.publish_time,
        symbols=a.symbols,
        importance=a.importance,
        url=a.url,
    )


def md_news(
    symbols: list[str], since_hours: int = 2, names: dict[str, str] | None = None
) -> list:
    """聚合新聞(個股新聞 + 公告),返回 list[NewsItem](與舊 NewsCollector.fetch_all 同形)。

    host 側可以用 datetime.now() 做 since 過濾(包內不允許偷偷調 datetime.now(),
    必須由呼叫方顯式傳 now)。

    同步函式;async 呼叫方用 `await asyncio.to_thread(md_news, ...)`。
    """
    from datetime import datetime, timezone

    # 包內 news vendor 的 publish_time 是 aware(UTC);這裡的 now 也必須 aware,
    # 否則 since 過濾會 "can't compare offset-naive and offset-aware datetimes"。
    arts = get_market_data().news(
        list(symbols or []), since_hours=since_hours, names=names,
        now=datetime.now(timezone.utc),
    )
    return [_article_to_newsitem(a) for a in arts]


def md_news_by_keyword(keyword: str) -> list:
    """按關鍵詞(行業/主題詞)搜中文新聞,返回 list[NewsItem]。同步。"""
    arts = get_market_data().news_by_keyword(keyword)
    return [_article_to_newsitem(a) for a in arts]


def md_stock_data(symbols: list[str], market: str) -> list:
    """返回 list[StockData](舊 AkshareCollector.get_stock_data 同形)。同步。"""
    from src.platform.marketdata.models import MarketCode, StockData

    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [StockData(
        symbol=q.symbol, name=q.name or "", market=MarketCode(q.market),
        current_price=q.current_price or 0.0, change_pct=q.change_pct or 0.0,
        change_amount=q.change_amount or 0.0, volume=q.volume or 0.0,
        turnover=q.turnover or 0.0, open_price=q.open_price or 0.0,
        high_price=q.high_price or 0.0, low_price=q.low_price or 0.0,
        prev_close=q.prev_close or 0.0) for q in quotes]
