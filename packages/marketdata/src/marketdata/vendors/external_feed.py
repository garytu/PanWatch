"""外部行情提供方(External Quote Provider)適配層。

解耦上游長連線與分K推導，透過本地高速 HTTP REST / SSE 對接 sidecar 服務。
"""

from __future__ import annotations

import logging
from datetime import datetime

from marketdata.http import market_get, record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import Bar, Quote
from marketdata.vendors.base import KlineVendor, QuoteVendor

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://127.0.0.1:8088"


class ExternalQuoteVendor(QuoteVendor):
    """外部即時行情 Vendor (TW 專用,支援 五檔與實時報價)。"""

    name = "external_quote"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []

        base_url = (config.get("base_url") or _DEFAULT_ENDPOINT).rstrip("/")
        timeout = float(config.get("timeout_sec") or 2.0)
        token = config.get("token")
        proxy = config.get("proxy")

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        sym_codes = [s.code for s in symbols if s.market == Market.TW]
        if not sym_codes:
            return []

        url = f"{base_url}/v1/quotes"
        payload = market_get(
            url,
            host_key="external_quote",
            params={"symbols": ",".join(sym_codes), "market": "TW"},
            headers=headers if headers else None,
            timeout=timeout,
            retries=1,
            parse="json",
            proxy=proxy,
            log_label="外部即時行情",
        )

        if not isinstance(payload, dict) or not payload.get("ok"):
            err = payload.get("error", "未知錯誤") if isinstance(payload, dict) else "連線失敗或回應非 JSON"
            record_error(f"external_quote: {err}")
            return []

        data = payload.get("data") or []
        out: list[Quote] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                ts_str = item.get("timestamp")
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
            except Exception:
                ts = datetime.now()

            out.append(
                Quote(
                    symbol=item.get("symbol", ""),
                    market=item.get("market", "TW"),
                    current_price=float(item.get("current_price") or 0.0),
                    name=item.get("name", ""),
                    prev_close=float(item["prev_close"]) if item.get("prev_close") is not None else None,
                    open_price=float(item["open_price"]) if item.get("open_price") is not None else None,
                    high_price=float(item["high_price"]) if item.get("high_price") is not None else None,
                    low_price=float(item["low_price"]) if item.get("low_price") is not None else None,
                    change_amount=float(item["change_amount"]) if item.get("change_amount") is not None else None,
                    change_pct=float(item["change_pct"]) if item.get("change_pct") is not None else None,
                    volume=float(item["volume"]) if item.get("volume") is not None else None,
                    turnover=float(item["turnover"]) if item.get("turnover") is not None else None,
                    turnover_rate=float(item["turnover_rate"]) if item.get("turnover_rate") is not None else None,
                    volume_ratio=float(item["volume_ratio"]) if item.get("volume_ratio") is not None else None,
                    timestamp=ts,
                )
            )
        return out


class ExternalKlineVendor(KlineVendor):
    """外部盤中分K Vendor (1m/5m 衍生K線)。"""

    name = "external_kline"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []

        sym = symbols[0]
        if sym.market != Market.TW:
            return []

        base_url = (config.get("base_url") or _DEFAULT_ENDPOINT).rstrip("/")
        timeout = float(config.get("timeout_sec") or 3.0)
        token = config.get("token")
        proxy = config.get("proxy")
        timeframe = config.get("timeframe") or "1m"
        limit = int(config.get("limit") or config.get("days") or 270)

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{base_url}/v1/intraday_klines"
        payload = market_get(
            url,
            host_key="external_kline",
            params={"symbol": sym.code, "timeframe": timeframe, "limit": limit, "market": "TW"},
            headers=headers if headers else None,
            timeout=timeout,
            retries=1,
            parse="json",
            proxy=proxy,
            log_label="外部盤中分K",
            symbol=sym.code,
        )

        if not isinstance(payload, dict) or not payload.get("ok"):
            err = payload.get("error", "未知錯誤") if isinstance(payload, dict) else "連線失敗或回應非 JSON"
            record_error(f"external_kline {sym.code}: {err}")
            return []

        bars_raw = payload.get("data") or []
        out: list[Bar] = []
        for row in bars_raw:
            if not isinstance(row, dict):
                continue
            try:
                out.append(
                    Bar(
                        date=str(row.get("date", "")),
                        open=float(row.get("open") or 0.0),
                        close=float(row.get("close") or 0.0),
                        high=float(row.get("high") or 0.0),
                        low=float(row.get("low") or 0.0),
                        volume=float(row.get("volume") or 0.0),
                    )
                )
            except Exception:
                continue
        return out
