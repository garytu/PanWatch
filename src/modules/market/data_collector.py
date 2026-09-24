"""統一資料來源管理器"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from marketdata import PACKAGE_VENDORS_BY_TYPE, capture_errors

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import DataSource
from src.platform.marketdata.models import MarketCode

# 資料來源測試的統一樣本。每個市場固定兩個穩定、容易識別的程式碼，避免新建資料來源
# 時只測到 A 股，導致港股/美股 provider 的市場路由問題直到生產才暴露。
DEFAULT_TEST_SYMBOLS_BY_MARKET: dict[str, tuple[str, str]] = {
    "CN": ("600519", "601127"),
    "HK": ("00700", "00386"),
    "US": ("AAPL", "NVDA"),
}
DEFAULT_TEST_SYMBOLS: tuple[str, ...] = tuple(
    symbol
    for symbols in DEFAULT_TEST_SYMBOLS_BY_MARKET.values()
    for symbol in symbols
)

logger = logging.getLogger(__name__)

# 資料來源"測試"最多測多少個配置的 test_symbols(上限,防使用者貼一大串把源打爆)。
# 取 10 覆蓋常見配置(此前 kline/capital_flow 寫死 [:3]、quote/events [:5],會把使用者
# 配的第 4/6 個悄悄切掉,造成"配了 N 個只返回前幾個"的意外)。
_TEST_SYMBOL_LIMIT = 10


@dataclass
class CollectorResult:
    """採集結果"""

    success: bool
    data: Any = None
    count: int = 0
    duration_ms: int = 0
    error: str = ""
    source_name: str = ""
    source_provider: str = ""
    # 本次實際執行的程式碼(包含未返回的資料),用於測試彈跳視窗回顯配置/預設值。
    test_symbols: list[str] = field(default_factory=list)
    # 部分成功時保留逐程式碼失敗原因，避免無資料程式碼(如 APPL)靜默消失。
    errors: list[dict[str, str]] = field(default_factory=list)


@dataclass
class CollectorLog:
    """採集日誌"""

    timestamp: datetime
    source_name: str
    source_type: str
    action: str  # "start" / "success" / "error"
    message: str
    duration_ms: int = 0
    count: int = 0


class DataCollectorManager:
    """
    統一資料來源管理器

    提供統一的資料採集介面，支援：
    - 從資料庫配置載入資料來源
    - 記錄採集日誌
    - 批次/單個採集
    """

    # 資料來源型別 -> (provider -> 採集器工廠)
    COLLECTOR_FACTORIES: dict[str, dict[str, Callable]] = {}

    def __init__(self):
        self.logs: list[CollectorLog] = []
        self._register_collectors()

    def _register_collectors(self):
        """註冊所有采集器"""
        from src.platform.marketdata.collectors.kline_collector import KlineCollector
        from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector
        from src.platform.marketdata.collectors.events_collector import EastMoneyEventsCollector

        self.COLLECTOR_FACTORIES = {
            "kline": {
                "tencent": lambda cfg: ("tencent", KlineCollector),
            },
            "capital_flow": {
                "eastmoney": lambda cfg: CapitalFlowCollector(MarketCode.CN),
            },
            "chart": {
                "xueqiu": lambda cfg: ("xueqiu", cfg),
                "eastmoney": lambda cfg: ("eastmoney", cfg),
            },
            "events": {
                "eastmoney": lambda cfg: EastMoneyEventsCollector(),
            },
        }

    def _log(
        self,
        source_name: str,
        source_type: str,
        action: str,
        message: str,
        duration_ms: int = 0,
        count: int = 0,
    ):
        """記錄日誌"""
        log = CollectorLog(
            timestamp=datetime.now(),
            source_name=source_name,
            source_type=source_type,
            action=action,
            message=message,
            duration_ms=duration_ms,
            count=count,
        )
        self.logs.append(log)

        # 同時輸出到 logger:error 走 WARNING；start/success 是底層心跳,降到 DEBUG。
        # UI 日誌板始終從 self.logs 讀完整記錄,不受這裡影響。
        if action == "error":
            logger.warning(f"[{source_name}] {message}")
        else:
            logger.debug(f"[{source_name}] {message}")

    def get_logs(self) -> list[dict]:
        """獲取日誌（用於 UI 展示）"""
        return [
            {
                "timestamp": log.timestamp.strftime("%H:%M:%S"),
                "source_name": log.source_name,
                "source_type": log.source_type,
                "action": log.action,
                "message": log.message,
                "duration_ms": log.duration_ms,
                "count": log.count,
            }
            for log in self.logs
        ]

    def clear_logs(self):
        """清空日誌"""
        self.logs = []

    def get_enabled_sources(self, source_type: str) -> list[DataSource]:
        """獲取指定型別的已啟用資料來源"""
        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == source_type, DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def get_source_by_id(self, source_id: int) -> DataSource | None:
        """根據 ID 獲取資料來源"""
        db = SessionLocal()
        try:
            return db.query(DataSource).filter(DataSource.id == source_id).first()
        finally:
            db.close()

    def _get_stock_names(self, symbols: list[str]) -> dict[str, str]:
        """獲取股票程式碼到名稱的對映"""
        from src.platform.persistence.models import Stock

        # 預設測試股票名稱對映
        default_names = {
            "601127": "賽力斯",
            "600519": "貴州茅臺",
            "000001": "平安銀行",
            "000858": "五糧液",
            "300750": "寧德時代",
        }

        db = SessionLocal()
        try:
            stocks = db.query(Stock).filter(Stock.symbol.in_(symbols)).all()
            result = {s.symbol: s.name for s in stocks}

            # 對於資料庫中沒有的股票，使用預設名稱
            for symbol in symbols:
                if symbol not in result and symbol in default_names:
                    result[symbol] = default_names[symbol]

            return result
        except Exception as e:
            logger.warning(f"獲取股票名稱失敗: {e}")
            # 返回預設名稱
            return {s: default_names.get(s, s) for s in symbols if s in default_names}
        finally:
            db.close()

    async def collect_news(
        self, symbols: list[str], hours: int = 12
    ) -> CollectorResult:
        """採集新聞（使用所有已啟用的新聞資料來源）"""
        from src.platform.marketdata.collectors.news_collector import NewsCollector

        start_time = datetime.now()
        self._log("新聞採集", "news", "start", f"開始採集 {len(symbols)} 只股票的新聞")

        try:
            collector = NewsCollector.from_database()
            news_list = await collector.fetch_all(symbols=symbols, since_hours=hours)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "新聞採集",
                "news",
                "success",
                f"採集完成，共 {len(news_list)} 條",
                duration_ms=duration_ms,
                count=len(news_list),
            )

            return CollectorResult(
                success=True,
                data=news_list,
                count=len(news_list),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("新聞採集", "news", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_kline(
        self, symbol: str, market: str = "CN", days: int = 60
    ) -> CollectorResult:
        """採集 K 線資料"""
        from src.platform.marketdata.collectors.kline_collector import KlineCollector
        from src.platform.marketdata.models import MarketCode

        start_time = datetime.now()
        self._log("K線資料", "kline", "start", f"獲取 {symbol} 的 K 線資料")

        try:
            market_code = MarketCode(market)
            collector = KlineCollector(market_code)
            summary = collector.get_kline_summary(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if summary.get("error"):
                self._log(
                    "K線資料",
                    "kline",
                    "error",
                    summary["error"],
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error=summary["error"], duration_ms=duration_ms
                )

            self._log(
                "K線資料",
                "kline",
                "success",
                f"獲取成功，最新收盤價 {summary.get('last_close', 'N/A')}",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=summary,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("K線資料", "kline", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_capital_flow(self, symbol: str) -> CollectorResult:
        """採集資金流向"""
        from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector

        start_time = datetime.now()
        self._log("資金流向", "capital_flow", "start", f"獲取 {symbol} 的資金流向")

        try:
            collector = CapitalFlowCollector(MarketCode.CN)
            data = collector.get_capital_flow(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if not data:
                self._log(
                    "資金流向",
                    "capital_flow",
                    "error",
                    "無資料",
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error="無資料", duration_ms=duration_ms
                )

            self._log(
                "資金流向",
                "capital_flow",
                "success",
                f"獲取成功，主力淨流入 {data.main_net_inflow / 10000:.2f}萬",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=data,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "資金流向", "capital_flow", "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_quote(self, symbols: list[str]) -> CollectorResult:
        """採集即時行情"""
        from src.platform.marketdata.marketdata_client import md_stock_data

        start_time = datetime.now()
        self._log("即時行情", "quote", "start", f"獲取 {len(symbols)} 只股票的行情")

        try:
            stocks = await asyncio.to_thread(md_stock_data, symbols, MarketCode.CN.value)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "即時行情",
                "quote",
                "success",
                f"獲取成功，共 {len(stocks)} 只",
                duration_ms=duration_ms,
                count=len(stocks),
            )

            return CollectorResult(
                success=True,
                data=stocks,
                count=len(stocks),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("即時行情", "quote", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_source(self, source: DataSource) -> CollectorResult:
        """測試單個資料源"""
        test_symbols = source.test_symbols or list(DEFAULT_TEST_SYMBOLS)

        start_time = datetime.now()
        self._log(
            source.name,
            source.type,
            "start",
            f"開始測試，測試股票: {','.join(test_symbols)}",
        )

        try:
            # 收集 vendor/market_get 的真實失敗原因,失敗時透到 UI(而不是籠統的"無資料")
            with capture_errors() as errs:
                result = await self._test_source_impl(source, test_symbols)
            result.test_symbols = list(test_symbols)
            if not result.success and errs:
                # 去重保序 + 截斷,拼成真因;若原本已有更具體的 error(如"provider 無對應 vendor")保留在前
                seen: dict[str, None] = {}
                for m in errs:
                    seen.setdefault(m, None)
                detail = "; ".join(list(seen)[:8])
                generic = {"", "無資料", "獲取行情失敗", "獲取 K 線資料失敗", "獲取資金流向失敗",
                           "未獲取到新聞資料", "未獲取到快訊資料", "未獲取到基本面資料",
                           "未獲取到龍虎榜資料", "未獲取到融資融券資料", "未獲取到股東資料",
                           "未獲取到分紅資料", "未獲取到北向資金資料"}
                result.error = detail if (result.error or "") in generic else f"{result.error};真因: {detail}"
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if result.success:
                self._log(
                    source.name,
                    source.type,
                    "success",
                    f"測試成功，獲取到 {result.count} 條資料",
                    duration_ms=duration_ms,
                    count=result.count,
                )
            else:
                self._log(
                    source.name,
                    source.type,
                    "error",
                    result.error,
                    duration_ms=duration_ms,
                )

            result.duration_ms = duration_ms
            result.source_name = source.name
            result.source_provider = source.provider
            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                source.name, source.type, "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                source_name=source.name,
                source_provider=source.provider,
                test_symbols=list(test_symbols),
            )

    async def _test_source_impl(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """測試資料來源的具體實現"""
        if source.type == "news":
            return await self._test_news_source(source, test_symbols)

        elif source.type == "kline":
            # 按 provider 路由到對應 Provider,而不是寫死走 tencent (KlineCollector)。
            # Tushare/YFinance 的 token 等配置從 source.config 注入。
            return await self._test_kline_source(source, test_symbols)

        elif source.type == "capital_flow":
            from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector

            collector = CapitalFlowCollector(MarketCode.CN)
            results = []
            for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
                data = collector.get_capital_flow(symbol)
                if data:
                    results.append(
                        {
                            "symbol": symbol,
                            "name": data.name,
                            "main_net": data.main_net_inflow,
                            "main_pct": data.main_net_inflow_pct,
                        }
                    )

            return CollectorResult(
                success=len(results) > 0,
                data=results,
                count=len(results),
                error="" if results else "獲取資金流向失敗",
            )

        elif source.type == "quote":
            # 按 provider 路由到對應 Provider,Tushare(暫無 quote)/YFinance 可正確測到。
            return await self._test_quote_source(source, test_symbols)

        elif source.type == "chart":
            from src.platform.marketdata.collectors.screenshot_collector import ScreenshotCollector
            import base64

            collector = ScreenshotCollector(config={"extra_wait_ms": 3000})
            try:
                symbol = test_symbols[0] if test_symbols else "601127"
                screenshot = await collector.capture(
                    symbol=symbol,
                    name="測試",
                    market="CN",
                    provider=source.provider,
                )
                if screenshot and screenshot.exists:
                    with open(screenshot.filepath, "rb") as f:
                        img_base64 = base64.b64encode(f.read()).decode("utf-8")
                    return CollectorResult(
                        success=True,
                        data={"image": f"data:image/png;base64,{img_base64}"},
                        count=1,
                    )
                return CollectorResult(success=False, error="截圖失敗")
            finally:
                await collector.close()

        elif source.type == "events":
            from src.platform.marketdata.collectors.events_collector import EastMoneyEventsCollector

            from datetime import timedelta

            # Use a longer window for tests to avoid "recently empty" false negatives.
            # This is only for connectivity/format validation, not for production logic.
            lookback_days = 365
            since = datetime.now() - timedelta(days=lookback_days)
            if source.provider == "eastmoney":
                cfg = source.config or {}
                collector = EastMoneyEventsCollector(
                    timeout_s=cfg.get("timeout_s", 10.0),
                    connect_timeout_s=cfg.get("connect_timeout_s"),
                    verify_ssl=cfg.get("verify_ssl", False),
                    proxy=cfg.get("proxy"),
                    retries=cfg.get("retries", 1),
                    backoff_s=cfg.get("backoff_s", 0.6),
                )
                items = await collector.fetch_events(
                    symbols=test_symbols[:_TEST_SYMBOL_LIMIT],
                    since=since,
                    page_size=100,
                )
                if not items and getattr(collector, "last_error", None):
                    return CollectorResult(
                        success=False,
                        data=[],
                        count=0,
                        error=str(collector.last_error),
                    )
                return CollectorResult(
                    success=len(items) > 0,
                    data=[
                        {
                            "title": i.title[:80],
                            "time": i.publish_time.strftime("%m-%d %H:%M"),
                            "event_type": i.event_type,
                        }
                        for i in items[:10]
                    ],
                    count=len(items),
                    error=""
                    if items
                    else f"未獲取到事件資料（lookback={lookback_days}d）",
                )

        elif source.type == "flash_news":
            return await self._test_flash_news_source(source)

        elif source.type == "fundamentals":
            return await self._test_fundamentals_source(source)

        elif source.type == "dragon_tiger":
            return await self._test_dragon_tiger_source(source)

        elif source.type == "margin":
            return await self._test_margin_source(source)

        elif source.type == "shareholders":
            return await self._test_shareholders_source(source)

        elif source.type == "dividend":
            return await self._test_dividend_source(source)

        elif source.type == "northbound":
            return await self._test_northbound_source(source)

        return CollectorResult(
            success=False, error=f"不支援的資料來源型別: {source.type}"
        )

    # 包內 kline/quote/flash_news/fundamentals Engine 各自只註冊了這些 vendor(權威來源見 marketdata.PACKAGE_VENDORS_BY_TYPE)。
    # provider 不在這個集合裡 = 包內沒實現該源,測試應給出明確 error,不能構造 Engine 硬跑。
    _NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["news"]
    _KLINE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["kline"]
    _QUOTE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["quote"]
    _FLASH_NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["flash_news"]
    _FUNDAMENTALS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["fundamentals"]
    _DRAGON_TIGER_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dragon_tiger"]
    _MARGIN_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["margin"]
    _SHAREHOLDERS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["shareholders"]
    _DIVIDEND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dividend"]
    _NORTHBOUND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["northbound"]

    async def _test_kline_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 測試 K 線源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        測試需要的是"這個 provider 自己工作正常",不是"整條主備鏈有 fallback 能跑通",
        所以用只含這一個 vendor 的 StaticConfigProvider 隔離測試指定源。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider, Symbol

        if source.provider not in self._KLINE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該 K 線源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"kline": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        results = []
        errors: list[dict[str, str]] = []
        first_error = ""
        for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
            market = Symbol.parse(symbol).market.value
            try:
                bars = md.klines(symbol, market=market, days=30)
                if bars:
                    last = bars[-1]
                    results.append(
                        {
                            "symbol": symbol,
                            "last_close": last.close,
                            "last_date": last.date,
                            "count": len(bars),
                        }
                    )
                elif not first_error:
                    first_error = "無資料"
                if not bars:
                    errors.append({"symbol": symbol, "market": market, "error": "無資料"})
            except Exception as e:
                errors.append({"symbol": symbol, "market": market, "error": str(e)})
                if not first_error:
                    first_error = str(e)

        return CollectorResult(
            success=len(results) > 0,
            data=results,
            count=len(results),
            error="" if results else (first_error or "獲取 K 線資料失敗"),
            errors=errors,
        )

    async def _test_quote_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 測試行情源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。"""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        from src.platform.marketdata.marketdata_client import _quote_to_row

        if source.provider not in self._QUOTE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該行情源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"quote": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            quotes = md.quotes(list(test_symbols[:_TEST_SYMBOL_LIMIT]))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        rows = [_quote_to_row(q) for q in quotes]
        return CollectorResult(
            success=len(rows) > 0,
            data=[
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "price": row["current_price"],
                    "change_pct": row["change_pct"],
                }
                for row in rows
            ],
            count=len(rows),
            error="" if rows else "獲取行情失敗",
        )

    async def _test_news_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 測試新聞源:走 marketdata 包的單源 Engine(僅該 vendor,不聚合其它源)。

        新聞是按 symbol 的資料;eastmoney_news 用股票名稱搜尋(效果遠好於程式碼搜尋),
        所以這裡取測試股票的名稱對映一併傳入。capture_errors 已在 test_source 外層
        包著,失敗時會自動透真因（含雪球 WAF 攔截）。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該新聞源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        names = self._get_stock_names(test_symbols)

        try:
            # 包內 news publish_time 是 aware(UTC),now 也須 aware,否則 since 過濾崩
            from datetime import timezone
            news = md.news(test_symbols, names=names, now=datetime.now(timezone.utc))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(news) > 0,
            data=[
                {
                    "title": n.title[:60],
                    "time": n.publish_time.strftime("%m-%d %H:%M"),
                }
                for n in news[:10]
            ],
            count=len(news),
            error="" if news else "未獲取到新聞資料",
        )

    async def _test_flash_news_source(self, source: DataSource) -> CollectorResult:
        """按 provider 測試快訊源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        快訊是市場級資料(7×24 電報),不按 symbols 過濾,所以不傳 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FLASH_NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該快訊源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"flash_news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.flash_news(limit=20)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "title": i.title[:80],
                    "time": i.publish_time.strftime("%m-%d %H:%M"),
                    "symbols": i.symbols,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未獲取到快訊資料",
        )

    async def _test_fundamentals_source(self, source: DataSource) -> CollectorResult:
        """按 provider 測試基本面源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        基本面是按 symbol 的資料(與市場級 flash_news 不同),測試必須顯式配置
        test_symbols,不套用全域性預設股票,配置缺失時直接給出明確 error。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FUNDAMENTALS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該基本面源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="請配置測試股票程式碼")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "fundamentals": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.fundamentals(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "pe_ttm": i.pe_ttm,
                    "pb": i.pb,
                    "roe": i.roe,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未獲取到基本面資料",
        )

    async def _test_dragon_tiger_source(self, source: DataSource) -> CollectorResult:
        """測試龍虎榜源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        龍虎榜是市場級資料(不按 symbols 過濾),但需要指定交易日。測試時優先取
        source.config.test_date,未配置則用當前日期佔位(僅用於驗證連通性，
        實抓以真實交易日為準）。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DRAGON_TIGER_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該龍虎榜源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "dragon_tiger": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        test_date = cfg.get("test_date") or datetime.now().strftime("%Y-%m-%d")

        try:
            items = md.dragon_tiger(date=test_date)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "net_buy": i.net_buy,
                }
                for i in items[:10]
            ],
            count=len(items),
            error=""
            if items
            else "未獲取到龍虎榜資料（需配置 test_date 或當日有榜）",
        )

    async def _test_margin_source(self, source: DataSource) -> CollectorResult:
        """測試融資融券源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        融資融券是按 symbol 的資料,測試必須顯式配置 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._MARGIN_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該融資融券源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="請配置測試股票程式碼")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"margin": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.margin(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "date": i.date,
                    "total_balance": i.total_balance,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未獲取到融資融券資料",
        )

    async def _test_shareholders_source(self, source: DataSource) -> CollectorResult:
        """測試股東戶數源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        股東戶數是按 symbol 的資料,測試必須顯式配置 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._SHAREHOLDERS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該股東戶數源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="請配置測試股票程式碼")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "shareholders": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.shareholders(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "report_date": i.report_date,
                    "holder_num": i.holder_num,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未獲取到股東戶數資料",
        )

    async def _test_dividend_source(self, source: DataSource) -> CollectorResult:
        """測試分紅源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        分紅是按 symbol 的資料,測試必須顯式配置 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DIVIDEND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該分紅源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="請配置測試股票程式碼")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"dividend": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.dividend(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "ex_date": i.ex_date,
                    "dividend_per_share": i.dividend_per_share,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未獲取到分紅資料",
        )

    async def _test_northbound_source(self, source: DataSource) -> CollectorResult:
        """測試北向資金源:走 marketdata 包的單源 Engine(僅該 vendor,不串備份鏈)。

        北向資金是市場級資料(7×24 資金流),不按 symbols 過濾,所以不傳 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NORTHBOUND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 無對應 vendor，包內未實現該北向資金源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "northbound": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.northbound()
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "date": i.date,
                    "hgt_net": i.hgt_net,
                    "total_net": i.total_net,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未獲取到北向資金資料",
        )


# 全域性單例
_manager: DataCollectorManager | None = None


def get_collector_manager() -> DataCollectorManager:
    """獲取全域性資料來源管理器"""
    global _manager
    if _manager is None:
        _manager = DataCollectorManager()
    return _manager
