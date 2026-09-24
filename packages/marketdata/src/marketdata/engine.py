"""資料來源主備排程器:按 ConfigProvider 的優先順序鏈取數,首個非空即返回。

- 快取(唯一一層,vendor 內不再各自快取)。
- 每次取數經 MetricsSink 記錄 (vendor, ok, latency, error)。
- 透過 ConfigProvider 拿源、透過注入 vendors 取例項:不依賴 web/DB。
"""

from __future__ import annotations

import logging
import time

from marketdata.cache import TTLCache
from marketdata.http import record_error
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.symbol import Market, Symbol
from marketdata.types import Request, Response
from marketdata.vendors.base import Vendor

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, *, datatype: str, vendors: dict[str, Vendor],
                 config: ConfigProvider, metrics: MetricsSink,
                 cache: TTLCache, default_ttl: float):
        self.datatype = datatype
        self.vendors = vendors
        self.config = config
        self.metrics = metrics
        self.cache = cache
        self.default_ttl = default_ttl

    def fetch(self, req: Request, *, cache_ttl_sec: float | None = None, min_count: int = 1) -> Response:
        key = req.cache_key(self.datatype)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        market = req.market
        syms = [Symbol(Market(market), c) for c in req.symbols]
        sources = sorted(self.config.sources_for(self.datatype, market), key=lambda s: s.priority)

        last_err = ""
        best: Response | None = None
        for src in sources:
            if not src.enabled:
                continue
            vendor = self.vendors.get(src.vendor)
            if vendor is None:
                continue
            if vendor.supports_markets and market not in vendor.supports_markets:
                continue

            t0 = time.monotonic()
            try:
                call_config = {**(src.config or {}), "days": req.limit, **dict(req.extra)}
                data = vendor.fetch(syms, call_config)
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                self.metrics.record(vendor=src.vendor, datatype=self.datatype, market=market,
                                    ok=False, count=0, latency_ms=latency, error=str(e))
                last_err = str(e)
                logger.warning(f"[marketdata/{self.datatype}] vendor={src.vendor} raised: {e}")
                record_error(f"{src.vendor}: {type(e).__name__}: {e}")
                continue

            latency = int((time.monotonic() - t0) * 1000)
            if data:
                self.metrics.record(vendor=src.vendor, datatype=self.datatype, market=market,
                                    ok=True, count=len(data), latency_ms=latency)
                resp = Response(ok=True, data=data, vendor=src.vendor, latency_ms=latency)
                if len(data) >= min_count:
                    ttl = cache_ttl_sec if cache_ttl_sec is not None else self.default_ttl
                    self.cache.set(key, resp, ttl_sec=ttl)
                    return resp
                # 非空但不足:記為候選,繼續試更優
                if best is None or len(data) > len(best.data):
                    best = resp
            else:
                self.metrics.record(vendor=src.vendor, datatype=self.datatype, market=market,
                                    ok=False, count=0, latency_ms=latency, error="empty")
                last_err = "empty"

        if best is not None:
            ttl = cache_ttl_sec if cache_ttl_sec is not None else self.default_ttl
            self.cache.set(key, best, ttl_sec=ttl)
            return best
        return Response(ok=False, data=None, error=last_err or "no enabled provider")
