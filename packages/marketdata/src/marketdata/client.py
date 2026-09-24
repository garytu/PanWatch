"""物件式入口:注入 ConfigProvider(+可選 MetricsSink),對外提供 quotes()/health()。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

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
        # 龍虎榜/融資融券/股東戶數/分紅:市場/資金面,均走東財 datacenter 同構介面,
        # 更新頻率低(日頻/期頻),沿用 fundamentals 同款 300s TTL。
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
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
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

    def capital_flow(self, symbol: str, *, market: str = "CN") -> CapitalFlow | None:
        """單隻股票資金流向。不在包內快取(cache_ttl_sec=0);宿主自行快取。"""
        req = Request(symbols=(symbol,), market=market)
        resp = self._capital_flow_engine.fetch(req, cache_ttl_sec=0)
        data = resp.data or []
        return data[0] if data else None

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
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Fundamentals] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._fundamentals_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dragon_tiger(self, *, date: str | None = None, market: str = "CN") -> list[DragonTigerItem]:
        """龍虎榜(市場級,單日快照)。date 未給出時不猜測"今天",直接返回 []。"""
        req = Request(symbols=(), market=market, extra=(("date", date),))
        resp = self._dragon_tiger_engine.fetch(req)
        return resp.data or []

    def margin(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[MarginItem]:
        """批次融資融券(按 symbol,取每隻最新一條快照)。照 fundamentals() 分組範式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[MarginItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._margin_engine.fetch(req)
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
        return self._discovery.hot_stocks(**kw)

    def hot_boards(self, **kw) -> list[HotBoard]:
        """熱門板塊(東財榜單,市場級、不經 Engine)。"""
        return self._discovery.hot_boards(**kw)

    def board_stocks(self, **kw) -> list[HotStock]:
        """板塊成分股榜單(東財,市場級、不經 Engine)。"""
        return self._discovery.board_stocks(**kw)
