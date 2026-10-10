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


def twmd_config() -> dict:
    from src.platform.runtime.config import Settings
    settings = Settings()
    return {"base_url": settings.twmd_base_url, "token": settings.twmd_api_token,
            "timeout_sec": settings.twmd_timeout_sec,
            "profile_timeout_sec": settings.twmd_profile_timeout_sec}


QUOTE_METADATA = (
    "instrument_id", "venue", "price_kind", "provider", "trade_date", "reference_price",
    "change_basis", "adjustment_mode", "availability", "freshness", "collection_health",
    "usable_for_trading", "units", "volume_semantics", "eod_fallback",
)


def quote_usable_for_trading(quote: dict | None, market: str, *, now=None) -> bool:
    """TW requires live freshness evidence, including expiry while cached by consumers."""
    from datetime import datetime, timezone
    from marketdata.vendors.twmd import number
    quote = quote or {}
    price = number(quote.get("current_price"))
    if price is None or price <= 0:
        return False
    if market != "TW":
        return quote.get("usable_for_trading") is not False
    freshness = quote.get("freshness") or {}
    if (quote.get("price_kind") != "live" or quote.get("usable_for_trading") is not True
            or quote.get("availability") != "available" or freshness.get("status") != "fresh"):
        return False
    try:
        timestamp = datetime.fromisoformat(str(quote.get("timestamp")))
        if timestamp.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - timestamp).total_seconds()
        max_age = number(freshness.get("max_price_age_seconds")) or 30
        return -5 <= age <= max_age
    except (ValueError, TypeError):
        return False


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
            from src.platform.runtime.config import Settings
            settings = Settings()
            if datatype in {"company_profile", "monthly_revenue", "shareholder_distribution", "broker_flow", "financial_statements", "benchmark"}:
                return [SourceConfig(vendor="twmd", priority=0, enabled=True,
                                     config=twmd_config(), supports_batch=False)]
            if datatype in {"fundamentals", "capital_flow", "margin"}:
                provider = (
                    settings.tw_fundamentals_provider
                    if datatype == "fundamentals"
                    else settings.tw_capital_flow_provider
                    if datatype == "capital_flow"
                    else settings.tw_margin_provider
                )
                config = (
                    twmd_config()
                    if provider == "twmd"
                    else {"token": settings.finmind_api_token}
                )
                return [SourceConfig(vendor=provider, priority=0, enabled=True,
                                     config=config, supports_batch=datatype == "fundamentals")]
            if datatype in {"quote", "kline", "intraday_kline"} and settings.tw_data_provider != "external":
                return [SourceConfig(vendor="twmd", priority=0, enabled=True,
                                     config=twmd_config(), supports_batch=datatype == "quote")]
            # 1. 盤中即時行情/五檔: 僅支援單一外部 Provider，配置取自 .env
            if datatype == "quote":
                feed_url = settings.external_quote_feed_url
                feed_token = settings.external_quote_feed_token
                feed_timeout = (os.environ.get("EXTERNAL_QUOTE_FEED_TIMEOUT_SEC")
                                or os.environ.get("TW_QUOTE_FEED_TIMEOUT_SEC")
                                or settings.external_quote_feed_timeout_sec or "5")
                return [
                    SourceConfig(
                        vendor="external_quote",
                        priority=0,
                        enabled=True,
                        config={"base_url": feed_url, "token": feed_token, "timeout_sec": feed_timeout},
                        supports_batch=True,
                    )
                ]

            # 2. 盤中分K: 僅支援單一外部 Provider，配置取自 .env
            if datatype == "intraday_kline":
                feed_url = settings.external_quote_feed_url
                feed_token = settings.external_quote_feed_token
                return [
                    SourceConfig(
                        vendor="external_kline",
                        priority=0,
                        enabled=True,
                        config={"base_url": feed_url, "token": feed_token},
                        supports_batch=False,
                    )
                ]

            # 3. 台股新聞: Yahoo 股市 RSS 與 FinMind 聚合；後者仍使用設定的 token。
            if datatype == "news":
                return [
                    SourceConfig(vendor="yahoo_tw", priority=0, enabled=True),
                    SourceConfig(vendor="finmind", priority=10, enabled=True,
                                 config={"token": settings.finmind_api_token}),
                ]

            # 4. 日K、基本面、三大法人、融資融券、除權息: FinMind Provider，配置取自 .env
            if datatype in {"kline", "fundamentals", "capital_flow", "margin", "dividend"}:
                fm_token = settings.finmind_api_token
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
    row = {
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
        "timestamp": q.timestamp.isoformat() if q.timestamp else None,
        **{key: getattr(q, key) for key in QUOTE_METADATA},
    }
    if q.market == "TW":
        row["usable_for_trading"] = quote_usable_for_trading(row, "TW")
    return row


def md_quote_rows(symbols: list[str], market: str) -> list[dict]:
    """批次報價,返回 list[dict](與舊 orchestrator 輸出同形)。

    同步函式;async 呼叫方用 `await asyncio.to_thread(md_quote_rows, ...)`。
    """
    from src.platform.marketdata.models import is_market_enabled

    syms = list(symbols)
    if not syms or not is_market_enabled(market):
        return []
    quotes = get_market_data().quotes(syms, market=market)
    rows = [_quote_to_row(q) for q in quotes]
    if market == "TW":
        from marketdata.symbol import Symbol
        by_identity = {row["symbol"]: row for row in rows}
        rows = [{**by_identity[Symbol.parse(raw, market).identity], "symbol": raw}
                for raw in syms if Symbol.parse(raw, market).identity in by_identity]
    return rows


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
    symbols: list[str], since_hours: int = 2, names: dict[str, str] | None = None, *, market: str = "CN"
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
        list(symbols or []), market=market, since_hours=since_hours, names=names,
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
    from datetime import datetime
    rows = md_quote_rows(list(symbols), market)
    return [StockData(
        symbol=row["symbol"], name=row.get("name") or "", market=MarketCode(row["market"]),
        current_price=row.get("current_price"), change_pct=row.get("change_pct"),
        change_amount=row.get("change_amount"), volume=row.get("volume"),
        turnover=row.get("turnover"), open_price=row.get("open_price"),
        high_price=row.get("high_price"), low_price=row.get("low_price"),
        prev_close=row.get("prev_close"), timestamp=datetime.fromisoformat(row["timestamp"]) if row.get("timestamp") else None,
        **{key: row.get(key) for key in QUOTE_METADATA}) for row in rows]
