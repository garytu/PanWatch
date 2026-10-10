"""物件式入口:注入 ConfigProvider(+可選 MetricsSink),對外提供 quotes()/health()。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.http import record_error
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.registry import build_vendors
from marketdata.symbol import Symbol
from marketdata.types import (
    CapitalFlow,
    DividendItem,
    DragonTigerItem,
    EventItem,
    FlashNews,
    Fundamentals,
    HotBoard,
    HotStock,
    MarginItem,
    NewsArticle,
    NorthboundItem,
    Quote,
    Request,
    ShareholderItem,
    TwmdCompanyProfileRead,
    TwmdBenchmarkBarsRead,
    TwmdBenchmarkDefinition,
    TwmdDailyBarsRead,
    TwmdMaterialInformationRead,
    TwmdFinancialStatementRead,
    TwmdFinancialStatementPeriodsRead,
    TwmdBrokerFlowCoverageRead,
    TwmdBrokerFlowPriceLevelsRead,
    TwmdBrokerFlowQuantityRead,
    TwmdMonthlyRevenueRead,
    TwmdMarginShortSaleRead,
    TwmdShareholderDistributionRead,
    TaiwanDiscoveryPool,
)
from marketdata.vendors.discovery import DiscoveryVendor
from marketdata.vendors.news import EastmoneyStockNewsVendor

# 指數 secid(東財):指數與個股 secid 字首規則不同,必須顯式對映,否則按個股規則會取錯標的。
# 美股指數東財K線不支援,未列入 → index_klines 返回空,fail-soft。
INDEX_SECID: dict[str, str] = {
    "000300": "1.000300",   # 滬深300
    "000001": "1.000001",   # 上證指數
    "399001": "0.399001",   # 深證成指
    "399006": "0.399006",   # 創業板指
    "HSI": "100.HSI",       # 恒生指數
}

# 指數的原始騰訊符號(index_klines 的騰訊兜底路徑;美股指數東財無 secid,只能走這裡,
# 騰訊對美股指數只返最近幾根,短但可用)。
INDEX_TENCENT: dict[str, str] = {
    "000001": "sh000001",   # 上證指數
    "399001": "sz399001",   # 深證成指
    "399006": "sz399006",   # 創業板指
    "000300": "sh000300",   # 滬深300
    "HSI": "hkHSI",         # 恒生指數
    "IXIC": "usIXIC",       # 納斯達克
    "DJI": "usDJI",         # 道瓊斯
    "INX": "usINX",         # 標普500
}

_TAIPEI = ZoneInfo("Asia/Taipei")
_TW_DISCOVERY_PRICE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tw-discovery-price")
_TW_DISCOVERY_PRICE_SLOTS = threading.BoundedSemaphore(4)
_TW_DISCOVERY_MAX_PRICE_IDS = 2_000
_TW_DISCOVERY_PRICE_BATCH_SIZE = 100
_TW_DISCOVERY_PRICE_SCAN_TIMEOUT_SEC = 15.0


def _canonical_json_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _completed_research_window(
    start_date: date | str | None,
    end_date: date | str | None,
    *,
    today_taipei: date,
) -> tuple[str, str]:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    if start_date is None:
        end = today_taipei - timedelta(days=1)
        start = end - timedelta(days=29)
    else:
        start = _research_date(start_date, "start_date")
        end = _research_date(end_date, "end_date")  # type: ignore[arg-type]
    if start > end:
        raise ValueError("start_date must not be after end_date")
    if end >= today_taipei:
        raise ValueError("end_date must be before the current Asia/Taipei date")
    return start.isoformat(), end.isoformat()


def _research_date(value: date | str, label: str) -> date:
    if isinstance(value, datetime):
        raise TypeError(f"{label} must be a calendar date")
    if isinstance(value, date):
        return value
    raw = str(value)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != raw:
        raise ValueError(f"{label} must use YYYY-MM-DD")
    return parsed


class MarketData:
    def __init__(self, config: ConfigProvider, metrics: MetricsSink | None = None):
        self.config = config
        self.metrics = metrics or InMemoryMetricsSink()
        self._quote_engine = Engine(
            datatype="quote",
            vendors=build_vendors("quote"),
            config=config,
            metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=5.0),
            default_ttl=5.0,
        )
        self._kline_engine = Engine(
            datatype="kline",
            vendors=build_vendors("kline"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        self._intraday_kline_engine = Engine(
            datatype="intraday_kline",
            vendors=build_vendors("intraday_kline"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=3.0), default_ttl=3.0,
        )
        self._capital_flow_engine = Engine(
            datatype="capital_flow",
            vendors=build_vendors("capital_flow"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        self._events_engine = Engine(
            datatype="events",
            vendors=build_vendors("events"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        # flash_news(快訊 7×24)是市場級(symbols 恆空),但仍走 Engine 做主備/快取/健康度,
        # 與 discovery(不進 Engine)的區別是:flash_news 有多源競爭、需要統一 TTL 快取。
        self._flash_news_engine = Engine(
            datatype="flash_news",
            vendors=build_vendors("flash_news"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=30.0), default_ttl=30.0,
        )
        # discovery(東財熱門榜)是市場級、單源、非 symbol 模型,不進 Engine/不進 DataSource
        # taxonomy —— md 直接委託給 DiscoveryVendor。
        self._discovery = DiscoveryVendor()
        # news(新聞資訊)是聚合語義(併發查所有已啟用源、結果合併去重),非失敗轉移
        # (找到一個就停),硬套 Engine 的主備模型是設計錯配,故不進 Engine —— 只借 registry
        # 的 build_vendors 複用 vendor 例項,合併/去重/排序/since 過濾邏輯在 news() 裡自己做。
        self._news_vendors = build_vendors("news")
        self._fundamentals_engine = Engine(
            datatype="fundamentals",
            vendors=build_vendors("fundamentals"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        # 龍虎榜/融資融券/股東戶數/分紅:資金面低頻快照,沿用 fundamentals 同款 300s TTL。
        # Taiwan margin still uses the compatibility Engine; its selected vendor may be TWMD or FinMind.
        self._dragon_tiger_engine = Engine(
            datatype="dragon_tiger",
            vendors=build_vendors("dragon_tiger"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._margin_engine = Engine(
            datatype="margin",
            vendors=build_vendors("margin"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._shareholders_engine = Engine(
            datatype="shareholders",
            vendors=build_vendors("shareholders"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._dividend_engine = Engine(
            datatype="dividend",
            vendors=build_vendors("dividend"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        # 北向資金(同花順 hexin 當日分鐘累計淨買入):市場級、單源,更新頻率為分鐘級
        # 但當日累計值短期內變化不大,沿用 flash_news 同款 60s TTL(比 300s 更貼合"盤中遞增")。
        self._northbound_engine = Engine(
            datatype="northbound",
            vendors=build_vendors("northbound"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=60.0), default_ttl=60.0,
        )

    def klines(self, symbol: str, *, market: str, days: int = 120, min_count: int = 1) -> list:
        """按 priority 主備取日K(不足則試下一個,全不足取最長)。返回 list[Bar]。
        不在包內快取(cache_ttl_sec=0);宿主自行快取。"""
        req = Request(symbols=(symbol,), market=market, timeframe="day", limit=days,
                      extra=(("days", days),))
        resp = self._kline_engine.fetch(req, min_count=min_count, cache_ttl_sec=0)
        return resp.data or []

    def intraday_klines(self, symbol: str, *, market: str = "TW", timeframe: str = "1m", limit: int = 270) -> list[Bar]:
        """按 priority 主備取盤中分K。返回 list[Bar]。"""
        req = Request(symbols=(symbol,), market=market, timeframe=timeframe, limit=limit,
                      extra=(("timeframe", timeframe), ("limit", limit)))
        resp = self._intraday_kline_engine.fetch(req, cache_ttl_sec=3.0)
        return resp.data or []

    def quotes(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Quote]:
        """批次報價。symbols 可跨市場:未顯式給 market 時按程式碼自動識別並分組。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Quote] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.identity for s in syms), market=mkt)
            resp = self._quote_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        """按原始騰訊指數符號(sh000001/hkHSI/usDJI…)取行情,不經 Symbol.parse。

        指數程式碼可能與個股程式碼撞號(如 000001 既是平安銀行又是上證指數),故走顯式符號路徑。
        返回 list[dict]。
        """
        from marketdata.vendors.tencent import fetch_raw
        return fetch_raw(list(tencent_symbols)) if tencent_symbols else []

    def index_klines(self, code: str, *, market: str, days: int = 120) -> list:
        """指數日K:東財 secid 主源;失敗/未對映(如美股指數)走騰訊原始符號兜底;都無 → []。

        騰訊兜底修兩類缺口:①東財 push2his 被代理/風控掐時 CN/HK 指數仍有數;
        ②美股指數(IXIC/DJI/INX)東財無 secid,騰訊可出(僅最近幾根,短但可用)。返回 list[Bar]。
        """
        c = str(code).strip()
        secid = INDEX_SECID.get(c) or INDEX_SECID.get(c.upper())
        if secid:
            from marketdata.vendors.kline import fetch_eastmoney_kline
            bars = fetch_eastmoney_kline(secid, days)
            if bars:
                return bars
        tsym = INDEX_TENCENT.get(c) or INDEX_TENCENT.get(c.upper())
        if tsym:
            from marketdata.vendors.kline import fetch_tencent_kline_raw
            return fetch_tencent_kline_raw(tsym, days)
        return []

    def capital_flow(
        self,
        symbol: str,
        *,
        market: str = "CN",
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> CapitalFlow | None:
        """單隻股票資金流向。不在包內快取(cache_ttl_sec=0);宿主自行快取。"""
        extras: tuple[tuple[str, str], ...] = ()
        requested_symbol = symbol
        if market == "TW":
            today_taipei = datetime.now(_TAIPEI).date()
            start, end = _completed_research_window(
                start_date, end_date, today_taipei=today_taipei
            )
            requested_symbol = Symbol.parse(symbol, market).identity
            extras = (("start_date", start), ("end_date", end),
                      ("today_taipei", today_taipei.isoformat()))
        elif start_date is not None or end_date is not None:
            raise ValueError("date bounds are supported only for Taiwan research reads")
        req = Request(symbols=(requested_symbol,), market=market, extra=extras)
        resp = self._capital_flow_engine.fetch(req, cache_ttl_sec=0)
        if not resp.ok and market == "TW" and self._uses_twmd("capital_flow", "TW"):
            from marketdata.errors import VendorError
            raise VendorError(resp.error or "twmd institutional-flow read failed")
        data = resp.data or []
        return data[0] if data else None

    def _uses_twmd(self, datatype: str, market: str) -> bool:
        sources = sorted(self.config.sources_for(datatype, market), key=lambda source: source.priority)
        return any(source.enabled and source.vendor == "twmd" for source in sources)

    def _twmd_research_client(self, datatype: str):
        """Return the explicit TWMD source configured for issuer research reads."""
        sources = [
            source for source in self.config.sources_for(datatype, "TW")
            if source.enabled
        ]
        if len(sources) != 1 or sources[0].vendor != "twmd":
            raise ValueError(f"{datatype} requires one explicitly configured twmd source")
        from marketdata.vendors.twmd import TwmdClient
        return TwmdClient(sources[0].config)

    def benchmark_definitions(self) -> tuple[TwmdBenchmarkDefinition, ...]:
        """Read official TWSE/TPEx index identities through the explicit source."""
        return self._twmd_research_client("benchmark").benchmark_definitions()

    def benchmark_bars(
        self,
        benchmark_id: str,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        limit: int = 120,
        timeout_sec: float | None = None,
    ) -> TwmdBenchmarkBarsRead:
        """Read official benchmark points and provenance without stock identity."""
        return self._twmd_research_client("benchmark").benchmark_bars(
            benchmark_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            timeout_sec=timeout_sec,
        )

    def raw_daily_bars(
        self,
        instrument_id: str,
        *,
        limit: int = 1000,
        timeout_sec: float | None = None,
    ) -> TwmdDailyBarsRead:
        """Read latest-N raw daily issuer bars from the official TW source."""
        return self._twmd_research_client("benchmark").daily_bars(
            instrument_id,
            limit=limit,
            timeout_sec=timeout_sec,
        )

    def company_profile(self, symbol: str) -> TwmdCompanyProfileRead:
        """Read the latest official Taiwan issuer profile as a separate typed block."""
        return self._twmd_research_client("company_profile").company_profile(symbol)

    def material_information(
        self,
        instrument_id: str,
        start_date: str | date,
        end_date: str | date,
        *,
        source: str = "current",
        limit: int = 100,
        today_taipei: date | None = None,
    ) -> TwmdMaterialInformationRead:
        """Read one bounded TWSE issuer event source without source-family merging."""
        return self._twmd_research_client("material_information").material_information(
            instrument_id, start_date, end_date, source=source, limit=limit,
            today_taipei=today_taipei,
        )

    def financial_statements(
        self,
        instrument_id: str,
        fiscal_year: int,
        fiscal_quarter: int,
        *,
        report_scope: str = "consolidated",
        statement: str | None = None,
        limit: int = 1000,
        today_taipei: date | None = None,
        timeout_sec: float | None = None,
    ) -> TwmdFinancialStatementRead:
        """Read one bounded official TWSE consolidated financial report."""
        return self._twmd_research_client("financial_statements").financial_statements(
            instrument_id, fiscal_year, fiscal_quarter, report_scope=report_scope,
            statement=statement, limit=limit, today_taipei=today_taipei,
            timeout_sec=timeout_sec,
        )

    def financial_statement_periods(
        self,
        instrument_id: str,
        *,
        report_scope: str = "consolidated",
        statement: str | None = None,
        limit: int = 40,
        cursor: str | None = None,
        timeout_sec: float | None = None,
    ) -> TwmdFinancialStatementPeriodsRead:
        """Read a bounded page of retained report periods for one issuer."""
        return self._twmd_research_client("financial_statements").financial_statement_periods(
            instrument_id,
            report_scope=report_scope,
            statement=statement,
            limit=limit,
            cursor=cursor,
            timeout_sec=timeout_sec,
        )

    def monthly_revenues(
        self,
        symbol: str,
        start_month: str,
        end_month: str,
        *,
        today_taipei: date | None = None,
    ) -> TwmdMonthlyRevenueRead:
        """Read an inclusive official Taiwan monthly-revenue range."""
        return self._twmd_research_client("monthly_revenue").monthly_revenues(
            symbol, start_month, end_month, today_taipei=today_taipei
        )

    def adjacent_monthly_revenues(
        self,
        symbol: str,
        month: str,
        *,
        today_taipei: date | None = None,
    ) -> TwmdMonthlyRevenueRead:
        """Read a caller-named month with exactly its preceding month."""
        return self._twmd_research_client("monthly_revenue").adjacent_monthly_revenues(
            symbol, month, today_taipei=today_taipei
        )

    def margin_short_sale(
        self,
        symbol: str,
        start_date: str | date,
        end_date: str | date,
        *,
        today_taipei: date | None = None,
    ) -> TwmdMarginShortSaleRead:
        """Read the selected official TWMD margin source with coverage evidence."""
        return self._twmd_research_client("margin").margin_short_sale(
            symbol, start_date, end_date, today_taipei=today_taipei
        )

    def shareholder_distribution(
        self,
        symbol: str,
        start_date: str | date,
        end_date: str | date,
        *,
        report_variant: str | None = None,
    ) -> TwmdShareholderDistributionRead:
        """Read dedicated TDCC holder-distribution rows for one canonical issuer."""
        return self._twmd_research_client("shareholder_distribution").shareholder_distribution(
            symbol, start_date, end_date, report_variant=report_variant
        )

    def broker_flow_quantities(
        self,
        symbol: str,
        start_date: str | date,
        end_date: str | date,
        *,
        today_taipei: date | None = None,
        timeout_sec: float | None = None,
    ) -> TwmdBrokerFlowQuantityRead:
        """Read canonical broker quantities from the explicitly configured TWMD source."""
        return self._twmd_research_client("broker_flow").broker_flow_quantities(
            symbol, start_date, end_date, today_taipei=today_taipei, timeout_sec=timeout_sec
        )

    def broker_flow_coverage(
        self,
        symbol: str,
        start_date: str | date,
        end_date: str | date,
        *,
        today_taipei: date | None = None,
        timeout_sec: float | None = None,
    ) -> TwmdBrokerFlowCoverageRead:
        """Read date-complete canonical broker-flow coverage."""
        return self._twmd_research_client("broker_flow").broker_flow_coverage(
            symbol, start_date, end_date, today_taipei=today_taipei, timeout_sec=timeout_sec
        )

    def broker_flow_price_levels(
        self,
        symbol: str,
        trade_date: str | date,
        *,
        today_taipei: date | None = None,
        timeout_sec: float | None = None,
    ) -> TwmdBrokerFlowPriceLevelsRead:
        """Read one already-materialized TWSE broker execution-price date."""
        return self._twmd_research_client("broker_flow").broker_flow_price_levels(
            symbol, trade_date, today_taipei=today_taipei, timeout_sec=timeout_sec
        )

    def events(self, symbols: list[str], *, market: str = "CN", since_days: int = 7) -> list[EventItem]:
        """結構化事件(東財公告)。批次 symbols。不在包內快取(cache_ttl_sec=0);宿主自行快取。"""
        req = Request(symbols=tuple(symbols), market=market, since_hours=since_days * 24,
                      extra=(("since_days", since_days),))
        resp = self._events_engine.fetch(req, cache_ttl_sec=0)
        return resp.data or []

    def flash_news(self, *, market: str = "CN", limit: int = 50, keyword: str | None = None) -> list[FlashNews]:
        """快訊(7×24)。市場級,symbols 恆空。不在包內快取額外一層——用 Engine 預設 30s TTL。"""
        req = Request(symbols=(), market=market, limit=limit)
        resp = self._flash_news_engine.fetch(req)
        data = resp.data or []
        if keyword:
            data = [x for x in data if keyword in (x.title or "") or keyword in (x.content or "")]
        return data

    def news(
        self,
        symbols: list[str],
        *,
        market: str = "CN",
        since_hours: int = 2,
        names: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> list[NewsArticle]:
        """新聞資訊(個股新聞 + 公告)—— 聚合語義,非失敗轉移:查詢所有已啟用源、結果合併去重,
        而非"找到一個就停"(這與 quotes()/klines() 的主備語義不同),故不經 Engine。

        對齊 PanWatch NewsCollector.fetch_all 的聚合語義:
        - 公告源(vendor="eastmoney")用 max(since_hours, 72) 更寬視窗(公告發布頻率低,
          視窗太窄容易一條都撈不到);其餘源用 since_hours。視窗值會透傳進 vendor 的
          config(當前 3 個 vendor 均未讀取——真正的 since 過濾在本方法做,vendor 內
          不允許呼叫無參 datetime.now())。
        - 合併後按 external_id 去重,保留先出現的(即優先順序更高的源優先保留)。
        - 按 publish_time 倒序排列。
        - since 過濾需要"當下"錨點:傳 now 才過濾(每條按其來源選視窗,規則同上);
          不傳 now 則不過濾,原樣返回全部合併結果(包內絕不偷偷調 datetime.now())。
        """
        syms = [Symbol.parse(s, market) for s in symbols]
        srcs = sorted(self.config.sources_for("news", market), key=lambda s: s.priority)

        all_articles: list[NewsArticle] = []
        for src in srcs:
            if not src.enabled:
                continue
            vendor = self._news_vendors.get(src.vendor)
            if vendor is None:
                continue
            if vendor.supports_markets and market not in vendor.supports_markets:
                continue

            window = max(since_hours, 72) if src.vendor == "eastmoney" else since_hours
            call_config = {**(src.config or {}), "symbol_names": names or {}, "since_hours": window}

            t0 = time.monotonic()
            try:
                articles = vendor.fetch(syms, call_config) or []
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error=str(e))
                record_error(f"{src.vendor}: {type(e).__name__}: {e}")
                continue

            latency = int((time.monotonic() - t0) * 1000)
            if articles:
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=True, count=len(articles), latency_ms=latency)
            else:
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error="empty")
            all_articles.extend(articles)

        seen: set[str] = set()
        deduped: list[NewsArticle] = []
        for a in all_articles:
            if a.external_id in seen:
                continue
            seen.add(a.external_id)
            deduped.append(a)

        deduped.sort(key=lambda a: a.publish_time, reverse=True)

        if now is not None:
            def _keep(a: NewsArticle) -> bool:
                w = max(since_hours, 72) if a.source == "eastmoney" else since_hours
                return a.publish_time >= now - timedelta(hours=w)
            deduped = [a for a in deduped if _keep(a)]

        return deduped

    def news_by_keyword(self, keyword: str, *, market: str = "CN") -> list[NewsArticle]:
        """按任意關鍵詞(行業/主題詞,如"新能源汽車")搜中文新聞,不限股票程式碼。
        直接複用東財搜尋 vendor 的 fetch_by_keyword,單一源、不經聚合/去重。
        market 目前未使用(該 vendor 只支援中文搜尋),保留引數位供未來擴充套件。
        """
        return EastmoneyStockNewsVendor.fetch_by_keyword(keyword)

    def fundamentals(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Fundamentals]:
        """批次基本面/財務(按 symbol)。symbols 可跨市場:未顯式給 market 時按程式碼自動識別並分組。
        照 quotes() 範式:按市場分組、每組建 Request、逐組 engine.fetch、合併結果。"""
        today_taipei = datetime.now(_TAIPEI).date() if market in (None, "TW") else None
        tw_window = (
            _completed_research_window(None, None, today_taipei=today_taipei)
            if today_taipei is not None else None
        )
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Fundamentals] = []
        for mkt, syms in groups.items():
            request_symbols = tuple(s.identity if mkt == "TW" else s.code for s in syms)
            extra = (
                ("start_date", tw_window[0]), ("end_date", tw_window[1]),
                ("today_taipei", today_taipei.isoformat()),
            ) if mkt == "TW" and tw_window and today_taipei else ()
            req = Request(symbols=request_symbols, market=mkt, extra=extra)
            resp = self._fundamentals_engine.fetch(req)
            if not resp.ok and mkt == "TW" and self._uses_twmd("fundamentals", "TW"):
                from marketdata.errors import VendorError
                raise VendorError(resp.error or "twmd valuation read failed")
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dragon_tiger(self, *, date: str | None = None, market: str = "CN") -> list[DragonTigerItem]:
        """龍虎榜(市場級,單日快照)。date 未給出時不猜測"今天",直接返回 []。"""
        req = Request(symbols=(), market=market, extra=(("date", date),))
        resp = self._dragon_tiger_engine.fetch(req)
        return resp.data or []

    def margin(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[MarginItem]:
        """Batch margin observations; TWMD rows retain their source trading-unit evidence."""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[MarginItem] = []
        for mkt, syms in groups.items():
            request_symbols = tuple(s.identity if mkt == "TW" else s.code for s in syms)
            req = Request(symbols=request_symbols, market=mkt)
            resp = self._margin_engine.fetch(req)
            if not resp.ok and mkt == "TW" and self._uses_twmd("margin", "TW"):
                from marketdata.errors import VendorError
                raise VendorError(resp.error or "twmd margin read failed")
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def shareholders(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[ShareholderItem]:
        """批次股東戶數(按 symbol,取每隻最新一期)。照 fundamentals() 分組範式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[ShareholderItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._shareholders_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dividend(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[DividendItem]:
        """批次分紅(按 symbol,返回每隻全部歷史)。照 fundamentals() 分組範式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[DividendItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._dividend_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def northbound(self, *, market: str = "CN") -> list[NorthboundItem]:
        """北向資金(市場級,symbols 恆空)。照 flash_news() 無 symbols 範式。"""
        req = Request(symbols=(), market=market)
        resp = self._northbound_engine.fetch(req)
        return resp.data or []

    def health(self) -> dict[str, dict]:
        """每個 vendor 的記憶體健康度快照(成功率 / p50 延遲 / 最近錯誤)。"""
        return self.metrics.snapshot()

    def hot_stocks(self, **kw) -> list[HotStock]:
        """熱門/異動股(東財榜單,市場級、不經 Engine)。"""
        if kw.get("market") == "TW":
            pool = self.taiwan_discovery_pool(
                mode=kw.get("mode", "turnover"),
                limit=kw.get("limit", 20),
                max_universe_size=None,
            )
            return pool.items
        return self._discovery.hot_stocks(**kw)

    def taiwan_discovery_pool(
        self,
        *,
        mode: str = "turnover",
        limit: int = 20,
        max_universe_size: int | None = _TW_DISCOVERY_MAX_PRICE_IDS,
        deadline_monotonic: float | None = None,
    ) -> TaiwanDiscoveryPool:
        """Return a venue-aware, current-price ranked pool for TW research.

        The bounded default examines at most 2,000 canonical catalog IDs. IDs
        are selected in canonical order before ranking, so any truncation is
        deterministic and must be reported by callers. The legacy hot_stocks
        path passes ``None`` to keep its existing whole-catalog ranking scope.
        """
        from marketdata.vendors import twmd
        from marketdata.vendors.twmd import TwmdClient, number

        requested_top_n = int(limit)
        requested_max_universe_size = max_universe_size
        selected_limit = max(1, min(requested_top_n, 100))
        if mode not in {"turnover", "gainers"}:
            raise ValueError("Taiwan discovery mode must be turnover or gainers")
        if max_universe_size is not None:
            if isinstance(max_universe_size, bool) or not isinstance(max_universe_size, int):
                raise ValueError("max_universe_size must be a positive integer or None")
            max_universe_size = max(1, min(max_universe_size, _TW_DISCOVERY_MAX_PRICE_IDS))

        sources = [source for source in self.config.sources_for("quote", "TW") if source.enabled]
        if len(sources) != 1 or sources[0].vendor != "twmd":
            missing_at = datetime.now(timezone.utc).isoformat()
            scan_limits = {
                "requested_top_n": requested_top_n,
                "top_n": selected_limit,
                "requested_max_universe_size": requested_max_universe_size,
                "max_universe_size": max_universe_size,
                "price_snapshot_batch_size": _TW_DISCOVERY_PRICE_BATCH_SIZE,
                "max_concurrent_price_requests": 4,
                "deadline_budget_sec": _TW_DISCOVERY_PRICE_SCAN_TIMEOUT_SEC,
            }
            selection_config = {
                "version": "tw-discovery-v1",
                "mode": mode,
                "requested_market": "TW",
                "requested_venues": ["TWSE", "TPEX"],
                **scan_limits,
            }
            return TaiwanDiscoveryPool(
                items=[], status="quote_source_disabled", catalog_count=0,
                eligible_catalog_count=0, scanned_instrument_count=0,
                price_snapshot_count=0, catalog_request_count=0,
                price_snapshot_request_count=0,
                catalog_source="twmd:/api/v1/instruments",
                catalog_authority_status="UNKNOWN",
                scan_coverage_state="MISSING",
                coverage_scope="UNKNOWN",
                requested_market="TW",
                requested_venues=["TWSE", "TPEX"],
                scan_limits=scan_limits,
                selection_config=selection_config,
                selection_config_hash=_canonical_json_hash(selection_config),
                stage_id_sets={
                    "catalog": {"unique": [], "eligible": [], "excluded": []},
                    "universe_bound": {"requested": [], "not_requested_due_to_limit": []},
                    "dispatch": {"attempted": [], "unattempted": []},
                    "price_validation": {"valid": [], "invalid": [], "missing_or_failed": [], "pending": []},
                    "ranking": {"eligible": [], "excluded": []},
                    "pool_selection": {"selected": [], "ranked_not_selected": []},
                    "candidate_selection": {"selected": [], "excluded": [], "not_selected": []},
                },
                stage_reason_id_sets={"catalog": {"quote_source_disabled": []}},
                stage_reason_counts={"catalog": {"quote_source_disabled": 1}},
                run_started_at_utc=missing_at,
                run_completed_at_utc=missing_at,
            )

        run_started_at = datetime.now(timezone.utc)
        source = sources[0]
        client = TwmdClient(source.config)
        scan_deadline = time.monotonic() + _TW_DISCOVERY_PRICE_SCAN_TIMEOUT_SEC
        if deadline_monotonic is not None:
            scan_deadline = min(scan_deadline, deadline_monotonic)
        deadline_budget_sec = max(0.0, scan_deadline - time.monotonic())
        catalog_key = (client.base_url, client.config.get("token"))
        cached_catalog = twmd._catalog_cache.get(catalog_key)
        raw_cached_catalog = getattr(twmd, "_catalog_response_cache", None)
        raw_cached_catalog = (
            raw_cached_catalog.get(catalog_key)
            if raw_cached_catalog is not None and cached_catalog is not None
            else None
        )
        catalog_cached = raw_cached_catalog is not None or cached_catalog is not None
        legacy_filtered_cache_only = cached_catalog is not None and raw_cached_catalog is None
        catalog_error = None
        catalog_receipt_cache = getattr(twmd, "_catalog_response_receipts", None)
        catalog_receipt = (
            catalog_receipt_cache.get(catalog_key)
            if catalog_receipt_cache is not None else None
        )
        if raw_cached_catalog is not None:
            raw_catalog = raw_cached_catalog
            catalog_request_count = 0
        elif cached_catalog is not None:
            # An older adapter may have populated only the compatibility-filtered
            # cache. Preserve it as UNKNOWN completeness instead of refetching.
            raw_catalog = cached_catalog
            catalog_request_count = 0
        else:
            remaining = scan_deadline - time.monotonic()
            if remaining <= 0:
                raw_catalog, catalog_error, catalog_request_count = [], "timeout", 0
            else:
                catalog_request_count = 1
                try:
                    configured_timeout = min(float(source.config.get("timeout_sec") or 5), 5.0)
                    catalog_payload, _headers = client.get_response(
                        "instruments",
                        timeout_sec=min(configured_timeout, remaining),
                        retries=0,
                    )
                    if not isinstance(catalog_payload, list):
                        raise ValueError("invalid_instruments_response")
                    raw_catalog = catalog_payload
                    if raw_catalog:
                        raw_response_cache = getattr(twmd, "_catalog_response_cache", None)
                        if raw_response_cache is not None:
                            raw_response_cache.set(catalog_key, raw_catalog)
                        twmd._catalog_cache.set(
                            catalog_key,
                            [row for row in raw_catalog if isinstance(row, dict)
                             and isinstance(row.get("venue"), str)
                             and row.get("venue") in {"TWSE", "TPEX"}],
                        )
                        catalog_receipt = datetime.now(timezone.utc).isoformat()
                        if catalog_receipt_cache is not None:
                            catalog_receipt_cache.set(catalog_key, catalog_receipt)
                except Exception as exc:
                    raw_catalog, catalog_error = [], getattr(exc, "reason_code", None) or "catalog_error"
        if not isinstance(raw_catalog, list):
            raw_catalog = []

        catalog = [row for row in raw_catalog if isinstance(row, dict)
                   and isinstance(row.get("venue"), str)
                   and row.get("venue") in {"TWSE", "TPEX"}]
        catalog_hash = _canonical_json_hash(raw_catalog)
        receipt_value = catalog_receipt
        if receipt_value:
            try:
                receipt_value = datetime.fromisoformat(receipt_value.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                ).isoformat()
            except (TypeError, ValueError):
                receipt_value = None

        canonical_rows: dict[str, dict] = {}
        compatibility_eligible_rows: dict[str, dict] = {}
        conflicting_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        invalid_catalog_record_count = 0
        duplicate_catalog_record_count = 0
        for row in raw_catalog:
            if not isinstance(row, dict):
                invalid_catalog_record_count += 1
                continue
            venue = row.get("venue")
            symbol = row.get("symbol")
            instrument_id = row.get("instrument_id")
            if (not isinstance(venue, str) or venue not in {"TWSE", "TPEX"} or not isinstance(symbol, str)
                    or not isinstance(instrument_id, str)
                    or instrument_id != f"{venue}:{symbol}"
                    or re.fullmatch(r"(?:TWSE|TPEX):[0-9][0-9A-Z]{3,5}", instrument_id) is None):
                invalid_catalog_record_count += 1
                continue
            if (row.get("is_active") is True and isinstance(row.get("security_type"), str)
                    and row["security_type"] in {"EQUITY", "ETF"}):
                # The list API historically retained the last qualifying row,
                # even if a later duplicate was inactive or unsupported.
                compatibility_eligible_rows[instrument_id] = row
            previous = canonical_rows.get(instrument_id)
            if previous is not None:
                duplicate_catalog_record_count += 1
                duplicate_ids.add(instrument_id)
                if previous != row:
                    conflicting_ids.add(instrument_id)
                    # Capture the received final row and mark its authority
                    # ambiguous; request eligibility is preserved separately.
                    canonical_rows[instrument_id] = row
                continue
            canonical_rows[instrument_id] = row

        authority_by_id: dict[str, dict] = {}
        eligible: dict[str, dict] = {}
        excluded_types: dict[str, int] = {}
        catalog_excluded: dict[str, str] = {}
        for instrument_id, row in canonical_rows.items():
            venue = row.get("venue")
            instrument_id = row.get("instrument_id")
            security_type = row.get("security_type")
            if not isinstance(security_type, str):
                security_type = "UNKNOWN"
            authority_status = "UNKNOWN" if legacy_filtered_cache_only else "LATEST_ONLY"
            if instrument_id in conflicting_ids:
                authority_status = "CONFLICTING_CATALOG_ROWS"
            if instrument_id in compatibility_eligible_rows:
                eligible[instrument_id] = compatibility_eligible_rows[instrument_id]
            elif row.get("is_active") is not True:
                catalog_excluded[instrument_id] = "inactive"
            elif security_type not in {"EQUITY", "ETF"}:
                catalog_excluded[instrument_id] = "unsupported_or_unknown_security_type"
            if catalog_excluded.get(instrument_id) == "unsupported_or_unknown_security_type":
                type_name = str(security_type or "UNKNOWN")
                excluded_types[type_name] = excluded_types.get(type_name, 0) + 1

            authority_by_id[instrument_id] = {
                "instrument_id": instrument_id,
                "venue": venue,
                "security_type": str(security_type or "UNKNOWN"),
                "is_active": row.get("is_active") is True,
                "taxonomy_subtype": "UNKNOWN",
                "tax_category": "UNKNOWN",
                "catalog_source": "twmd:/api/v1/instruments",
                "catalog_source_as_of": None,
                "catalog_captured_at_utc": receipt_value,
                "catalog_hash": catalog_hash,
                "authority_status": authority_status,
                "point_in_time_status": "UNVERIFIABLE",
            }

        ids = sorted(eligible)
        total_eligible = len(ids)
        if max_universe_size is not None:
            ids = ids[:max_universe_size]
        partial_scan = len(ids) < total_eligible
        requested_ids = set(ids)
        not_requested_ids = set(eligible) - requested_ids
        chunks = [ids[offset:offset + _TW_DISCOVERY_PRICE_BATCH_SIZE]
                  for offset in range(0, len(ids), _TW_DISCOVERY_PRICE_BATCH_SIZE)]
        attempts_lock = threading.Lock()
        request_attempts = 0
        requested_ids_count = 0
        attempted_ids: set[str] = set()
        pending_batch_count = 0
        failed_request_count = 1 if catalog_error else 0
        snapshots: list[dict] = []
        returned_by_id: dict[str, dict] = {}
        invalid_return_ids: set[str] = set()
        invalid_price_reasons: dict[str, str] = {}
        failed_price_ids: set[str] = set()
        missing_price_ids: set[str] = set()
        pending_ids: set[str] = set()
        pending_submitted_ids: set[str] = set()
        attempts_frozen = False
        partial_scan = partial_scan or bool(catalog_error)

        try:
            request_timeout = min(float(source.config.get("timeout_sec") or 5), 5.0)
        except (TypeError, ValueError):
            request_timeout = 5.0
        if request_timeout <= 0:
            request_timeout = 5.0

        def fetch_batch(chunk: list[str]):
            nonlocal request_attempts, requested_ids_count
            remaining = scan_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("discovery request deadline exceeded")
            with attempts_lock:
                if attempts_frozen:
                    raise TimeoutError("discovery run capture already frozen")
                request_attempts += 1
                requested_ids_count += len(chunk)
                attempted_ids.update(chunk)
            return client.get_response(
                "price-snapshots",
                instrument_ids=",".join(chunk),
                timeout_sec=min(request_timeout, remaining),
                retries=0,
            )

        for wave_start in range(0, len(chunks), 4):
            if time.monotonic() >= scan_deadline:
                partial_scan = True
                break
            wave = chunks[wave_start:wave_start + 4]
            futures = {}
            for chunk in wave:
                if not _TW_DISCOVERY_PRICE_SLOTS.acquire(blocking=False):
                    partial_scan = True
                    break

                def make_release():
                    lock = threading.Lock()
                    released = False

                    def release_once():
                        nonlocal released
                        with lock:
                            if released:
                                return
                            released = True
                        _TW_DISCOVERY_PRICE_SLOTS.release()

                    return release_once

                release_once = make_release()

                def run_batch(selected_chunk=chunk, release=release_once):
                    try:
                        return fetch_batch(selected_chunk)
                    finally:
                        release()

                try:
                    future = _TW_DISCOVERY_PRICE_POOL.submit(run_batch)
                except Exception:
                    release_once()
                    partial_scan = True
                    failed_request_count += 1
                    break
                future.add_done_callback(lambda done, release=release_once: release() if done.cancelled() else None)
                futures[future] = (chunk, release_once)
            if not futures:
                break

            done, pending = wait(futures, timeout=max(0.0, scan_deadline - time.monotonic()))
            pending_batch_count += len(pending)
            for future in pending:
                future.cancel()
                partial_scan = True
                chunk = futures[future][0]
                pending_submitted_ids.update(chunk)
                with attempts_lock:
                    pending_ids.update(set(chunk) & attempted_ids)
            for future in done:
                chunk, release_once = futures[future]
                try:
                    payload, _headers = future.result()
                except Exception:
                    partial_scan = True
                    failed_request_count += 1
                    failed_price_ids.update(chunk)
                    continue
                batch_rows = payload.get("snapshots") if isinstance(payload, dict) else None
                if not isinstance(batch_rows, list):
                    partial_scan = True
                    failed_request_count += 1
                    failed_price_ids.update(chunk)
                    continue
                expected_ids = set(chunk)
                returned_ids: set[str] = set()
                malformed = False
                for row in batch_rows:
                    if not isinstance(row, dict):
                        malformed = True
                        continue
                    instrument_id = row.get("instrument_id")
                    if not isinstance(instrument_id, str):
                        malformed = True
                        continue
                    if instrument_id not in expected_ids or instrument_id in returned_ids:
                        malformed = True
                        if instrument_id in expected_ids:
                            invalid_return_ids.add(instrument_id)
                        continue
                    returned_ids.add(instrument_id)
                    snapshots.append(row)
                    returned_by_id[instrument_id] = row
                if malformed or returned_ids != expected_ids:
                    partial_scan = True
                    failed_request_count += 1
                missing_price_ids.update(expected_ids - returned_ids - invalid_return_ids)
            if pending:
                break

        # Futures can race with cancellation at the deadline. Freeze the exact
        # observed attempt set before deriving stage partitions; a late worker
        # must never mutate the returned immutable run evidence.
        with attempts_lock:
            attempts_frozen = True
            final_request_attempts = request_attempts
            final_requested_ids_count = requested_ids_count
            final_attempted_ids = set(attempted_ids)

        pending_ids.update(pending_submitted_ids & final_attempted_ids)
        failed_price_ids.intersection_update(final_attempted_ids)

        unattempted_ids = requested_ids - final_attempted_ids
        valid_price_ids: set[str] = set()
        invalid_price_ids: set[str] = set()
        missing_failed_ids: set[str] = set()
        for instrument_id in final_attempted_ids:
            if instrument_id in pending_ids:
                continue
            if instrument_id in failed_price_ids:
                missing_failed_ids.add(instrument_id)
                continue
            row = returned_by_id.get(instrument_id)
            if instrument_id in invalid_return_ids:
                invalid_price_ids.add(instrument_id)
                invalid_price_reasons[instrument_id] = "malformed_or_duplicate_snapshot"
                continue
            if row is None:
                missing_failed_ids.add(instrument_id)
                missing_price_ids.add(instrument_id)
                continue
            availability = row.get("availability") if isinstance(row.get("availability"), dict) else {}
            freshness = availability.get("freshness") if isinstance(availability.get("freshness"), dict) else {}
            availability_status = str(availability.get("status") or "").lower()
            freshness_status = str(freshness.get("status") or "").lower()
            if availability_status == "pending" or freshness_status == "pending":
                pending_ids.add(instrument_id)
                continue
            close = number(row.get("close"))
            if close is None or close <= 0:
                invalid_price_ids.add(instrument_id)
                invalid_price_reasons[instrument_id] = "malformed_or_missing_close"
                continue
            if availability_status != "available":
                invalid_price_ids.add(instrument_id)
                invalid_price_reasons[instrument_id] = "unavailable_price"
                continue
            if freshness_status != "current":
                invalid_price_ids.add(instrument_id)
                invalid_price_reasons[instrument_id] = "stale_or_unknown_freshness"
                continue
            valid_price_ids.add(instrument_id)

        rows = [returned_by_id[instrument_id] for instrument_id in valid_price_ids]
        field_name = "value" if mode == "turnover" else "change_pct"
        ranking_excluded_ids = {
            row["instrument_id"] for row in rows if number(row.get(field_name)) is None
        }
        rows = [row for row in rows if number(row.get(field_name)) is not None]
        rows.sort(key=lambda row: (-number(row[field_name]), str(row["instrument_id"])))
        ranked_price_count = len(rows)
        ranking_eligible_ids = {str(row["instrument_id"]) for row in rows}
        selected_rows = rows[:selected_limit]
        pool_selected_ids = {str(row["instrument_id"]) for row in selected_rows}
        ranked_not_selected_ids = ranking_eligible_ids - pool_selected_ids
        items = [
            HotStock(
                symbol=row["instrument_id"], market="TW",
                name=row.get("name") or eligible[row["instrument_id"]].get("name") or row["instrument_id"],
                price=number(row.get("close")), change_pct=number(row.get("change_pct")),
                turnover=number(row.get("value")), volume=number(row.get("volume")),
                price_kind="eod", trade_date=row.get("trade_date"),
                freshness=(row.get("availability") or {}).get("freshness") or {},
                provider=row.get("provider"),
                adjustment_mode=row.get("adjustment_mode"),
                change_basis=row.get("change_basis"),
                units=row.get("units") or {},
                availability=row.get("availability"),
                instrument_id=row["instrument_id"],
                venue=authority_by_id[row["instrument_id"]]["venue"],
                security_type=authority_by_id[row["instrument_id"]]["security_type"],
                is_active=authority_by_id[row["instrument_id"]]["is_active"],
            )
            for row in selected_rows
        ]
        unattempted_batches = max(0, len(chunks) - final_request_attempts)
        if catalog_error:
            status = "catalog_error"
        elif unattempted_batches:
            status = "partial_price_scan"
        elif not catalog:
            status = "catalog_unavailable_or_empty"
        elif not items:
            status = "no_current_eligible_prices"
        else:
            status = "partial" if partial_scan else "available"
        data_dates = sorted({
            returned_by_id[instrument_id]["trade_date"] for instrument_id in valid_price_ids
            if isinstance(returned_by_id[instrument_id].get("trade_date"), str)
            and returned_by_id[instrument_id]["trade_date"]
        })
        catalog_ids = set(canonical_rows)
        excluded_catalog_ids = set(catalog_excluded)
        catalog_incomplete = bool(
            catalog_error or invalid_catalog_record_count or conflicting_ids or legacy_filtered_cache_only
        )
        if legacy_filtered_cache_only:
            coverage_state = "UNKNOWN"
        elif not catalog_ids and (catalog_error or not raw_catalog):
            coverage_state = "MISSING"
        elif not valid_price_ids:
            coverage_state = "MISSING"
        elif catalog_incomplete or (final_attempted_ids - valid_price_ids) or unattempted_ids or failed_request_count:
            coverage_state = "PARTIAL"
        else:
            coverage_state = "COMPLETE"
        coverage_scope = (
            "BOUNDED_REQUESTED_UNIVERSE" if not_requested_ids
            else "FULL_ELIGIBLE_CATALOG"
        )
        if legacy_filtered_cache_only:
            coverage_scope = "UNKNOWN"
        stage_id_sets = {
            "catalog": {
                "unique": sorted(catalog_ids),
                "eligible": sorted(set(eligible)),
                "excluded": sorted(excluded_catalog_ids),
            },
            "universe_bound": {
                "requested": sorted(requested_ids),
                "not_requested_due_to_limit": sorted(not_requested_ids),
            },
            "dispatch": {
                "attempted": sorted(final_attempted_ids),
                "unattempted": sorted(unattempted_ids),
            },
            "price_validation": {
                "valid": sorted(valid_price_ids),
                "invalid": sorted(invalid_price_ids),
                "missing_or_failed": sorted(missing_failed_ids),
                "pending": sorted(pending_ids),
            },
            "ranking": {
                "eligible": sorted(ranking_eligible_ids),
                "excluded": sorted(ranking_excluded_ids),
            },
            "pool_selection": {
                "selected": sorted(pool_selected_ids),
                "ranked_not_selected": sorted(ranked_not_selected_ids),
            },
            # Candidate selection is completed by the strategy adapter after its
            # existing merge/cap and entry-plan gate; retain the pool IDs so its
            # final partition can be reconciled before the run transaction commits.
            "candidate_selection": {
                "selected": [],
                "excluded": [],
                "not_selected": sorted(pool_selected_ids),
            },
        }
        stage_reason_counts = {
            "catalog": {
                "inactive": sum(1 for x in excluded_catalog_ids if catalog_excluded[x] == "inactive"),
                "unsupported_or_unknown_security_type": sum(
                    1 for x in excluded_catalog_ids
                    if catalog_excluded[x] == "unsupported_or_unknown_security_type"
                ),
            },
            "price_validation": {
                "valid_price": len(valid_price_ids),
                **{
                    reason: sum(1 for value in invalid_price_reasons.values() if value == reason)
                    for reason in sorted(set(invalid_price_reasons.values()))
                },
                "failed_price_request": len(failed_price_ids),
                "missing_returned_price": len(missing_price_ids),
                "pending_price": len(pending_ids),
            },
            "ranking": {
                "missing_ranking_metric": len(ranking_excluded_ids),
            },
            "universe_bound": {
                "not_requested_due_to_limit": len(not_requested_ids),
            },
            "dispatch": {
                "unattempted_deadline_or_capacity": len(unattempted_ids),
            },
        }
        stage_reason_id_sets = {
            "catalog": {
                "inactive": sorted(x for x in excluded_catalog_ids if catalog_excluded[x] == "inactive"),
                "unsupported_or_unknown_security_type": sorted(
                    x for x in excluded_catalog_ids
                    if catalog_excluded[x] == "unsupported_or_unknown_security_type"
                ),
            },
            "catalog_diagnostics": {
                "duplicate_catalog_record": sorted(duplicate_ids),
                "conflicting_catalog_authority": sorted(conflicting_ids),
            },
            "universe_bound": {
                "not_requested_due_to_limit": sorted(not_requested_ids),
            },
            "dispatch": {
                "unattempted_deadline_or_capacity": sorted(unattempted_ids),
            },
            "price_validation": {
                "valid_price": sorted(valid_price_ids),
                **{
                    reason: sorted(
                        instrument_id
                        for instrument_id, value in invalid_price_reasons.items()
                        if value == reason
                    )
                    for reason in sorted(set(invalid_price_reasons.values()))
                },
            } | {
                "failed_price_request": sorted(failed_price_ids),
                "missing_returned_price": sorted(missing_price_ids),
                "pending_price": sorted(pending_ids),
            },
            "ranking": {"missing_ranking_metric": sorted(ranking_excluded_ids)},
            "pool_selection": {"ranked_not_selected": sorted(ranked_not_selected_ids)},
        }
        for instrument_id, authority in authority_by_id.items():
            authority["catalog_hash"] = catalog_hash
            authority["catalog_captured_at_utc"] = receipt_value
        scan_limits = {
            "requested_top_n": requested_top_n,
            "top_n": selected_limit,
            "requested_max_universe_size": requested_max_universe_size,
            "max_universe_size": max_universe_size,
            "price_snapshot_batch_size": _TW_DISCOVERY_PRICE_BATCH_SIZE,
            "max_concurrent_price_requests": 4,
            "deadline_budget_sec": round(deadline_budget_sec, 3),
        }
        selection_config = {
            "version": "tw-discovery-v1",
            "mode": mode,
            "requested_market": "TW",
            "requested_venues": ["TWSE", "TPEX"],
            **scan_limits,
        }
        return TaiwanDiscoveryPool(
            items=items, status=status, catalog_count=len(catalog),
            eligible_catalog_count=total_eligible,
            scanned_instrument_count=final_requested_ids_count,
            price_snapshot_count=len(snapshots),
            catalog_request_count=catalog_request_count,
            price_snapshot_request_count=final_request_attempts,
            price_universe_selected_count=len(ids),
            price_snapshot_batches_planned=len(chunks),
            unattempted_price_snapshot_batches=unattempted_batches,
            price_snapshot_batches_pending_count=pending_batch_count,
            ranked_price_count=ranked_price_count,
            failed_request_count=failed_request_count,
            cache_hits=1 if catalog_cached else 0,
            partial_scan=partial_scan or unattempted_batches > 0,
            excluded_security_type_counts=excluded_types,
            security_type_by_instrument_id={
                item.symbol: str(eligible[item.symbol]["security_type"]) for item in items
            },
            price_data_dates=data_dates,
            provider_scope="twmd",
            error_reason=catalog_error,
            stage_id_sets=stage_id_sets,
            stage_reason_id_sets=stage_reason_id_sets,
            stage_reason_counts=stage_reason_counts,
            authority_by_instrument_id=authority_by_id,
            catalog_response_row_count=len(raw_catalog),
            catalog_unique_count=len(catalog_ids),
            duplicate_catalog_record_count=duplicate_catalog_record_count,
            invalid_catalog_record_count=invalid_catalog_record_count,
            catalog_source="twmd:/api/v1/instruments",
            catalog_source_as_of=None,
            catalog_captured_at_utc=receipt_value,
            catalog_hash=catalog_hash,
            catalog_authority_status=(
                "UNKNOWN" if legacy_filtered_cache_only
                else "LATEST_ONLY" if catalog_ids else "UNKNOWN"
            ),
            scan_coverage_state=coverage_state,
            coverage_scope=coverage_scope,
            price_data_snapshot_count=len(snapshots),
            run_started_at_utc=run_started_at.isoformat(),
            run_completed_at_utc=datetime.now(timezone.utc).isoformat(),
            requested_market="TW",
            requested_venues=["TWSE", "TPEX"],
            scan_limits=scan_limits,
            selection_config=selection_config,
            selection_config_hash=_canonical_json_hash(selection_config),
        )

    def hot_boards(self, **kw) -> list[HotBoard]:
        """熱門板塊(東財榜單,市場級、不經 Engine)。"""
        return self._discovery.hot_boards(**kw)

    def board_stocks(self, **kw) -> list[HotStock]:
        """板塊成分股榜單(東財,市場級、不經 Engine)。"""
        return self._discovery.board_stocks(**kw)
