"""tw-market-data's stored prices, live quotes and instrument identity contracts."""

from __future__ import annotations

import math
from datetime import datetime

from marketdata.cache import TTLCache
from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import Bar, Quote
from marketdata.vendors.base import KlineVendor, QuoteVendor

# Instrument reference data changes much less often than quotes. Cache only the catalog.
_catalog_cache = TTLCache(default_ttl_sec=300)


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


class TwmdClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = (config.get("base_url") or "http://127.0.0.1:8000").rstrip("/")

    def get(self, path: str, **params):
        token = self.config.get("token")
        return market_get(
            f"{self.base_url}/api/v1/{path}", host_key="twmd", params=params or None,
            headers={"Authorization": f"Bearer {token}"} if token else None,
            timeout=float(self.config.get("timeout_sec") or 5), retries=1, parse="json",
            proxy=self.config.get("proxy"), log_label="twmd",
        )

    def instruments(self) -> list[dict]:
        key = (self.base_url, self.config.get("token"))
        cached = _catalog_cache.get(key)
        if cached is not None:
            return cached
        rows = self.get("instruments")
        if not isinstance(rows, list):
            return []
        rows = [row for row in rows if row.get("venue") in {"TWSE", "TPEX"}]
        if rows:
            _catalog_cache.set(key, rows)
        return rows

    def resolve(self, symbol: Symbol) -> str:
        if symbol.venue:
            return symbol.identity
        matches = [row for row in self.instruments() if row.get("symbol") == symbol.code]
        active = [row for row in matches if row.get("is_active")]
        matches = active or matches
        identities = {row["instrument_id"] for row in matches}
        if len(identities) != 1:
            raise ValueError(f"Taiwan symbol {symbol.code} needs an explicit TWSE:/TPEX: venue")
        return identities.pop()

    def bars(self, symbol: Symbol, *, timeframe="day", limit=120, **params) -> dict:
        payload = self.get("bars", instrument_id=self.resolve(symbol), timeframe=timeframe,
                           limit=limit, **params)
        return payload if isinstance(payload, dict) else {}


class TwmdQuoteVendor(QuoteVendor):
    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        client = TwmdClient(config)
        resolved = {}
        out = []
        for symbol in symbols:
            try:
                resolved[symbol.identity] = client.resolve(symbol)
            except ValueError as exc:
                out.append(Quote(symbol=symbol.identity, market="TW", current_price=None,
                                 timestamp=None, availability="unknown_instrument",
                                 usable_for_trading=False, freshness={"status": "unknown", "reason": str(exc)}))
        ids = list(dict.fromkeys(resolved.values()))
        quotes = {}
        for offset in range(0, len(ids), 100):
            payload = client.get("quotes", instrument_ids=",".join(ids[offset:offset + 100]),
                                 include_eod="true") or {}
            for row in payload.get("quotes", []):
                quotes[row["instrument_id"]] = row
        for requested, instrument_id in resolved.items():
            live = quotes.get(instrument_id) or {}
            fallback = live.get("eod_fallback")
            has_live = number(live.get("last_price")) is not None
            price = live if has_live else (fallback or {})
            kind = "live" if has_live else ("eod" if fallback else None)
            timestamp = None
            if has_live and live.get("observed_at"):
                try:
                    timestamp = datetime.fromisoformat(live["observed_at"])
                except ValueError:
                    pass
            freshness = live.get("freshness") or {}
            previous = price.get("previous_observation") or {}
            out.append(Quote(
                symbol=requested, market="TW", instrument_id=instrument_id,
                venue=instrument_id.split(":")[0], name=live.get("name") or price.get("name") or "",
                current_price=number(price.get("last_price") if has_live else price.get("close")),
                prev_close=number(live.get("previous_close") if has_live else previous.get("close")),
                reference_price=number(live.get("reference_price")),
                open_price=number(price.get("open")), high_price=number(price.get("high")),
                low_price=number(price.get("low")), change_amount=number(price.get("change")),
                change_pct=number(price.get("change_pct")), volume=number(price.get("volume")),
                turnover=number(price.get("turnover") if has_live else price.get("value")),
                timestamp=timestamp, price_kind=kind, provider=price.get("provider"),
                trade_date=price.get("trade_date"), change_basis=price.get("change_basis"),
                adjustment_mode=price.get("adjustment_mode"), availability=live.get("availability", "unavailable"),
                freshness=freshness, collection_health=live.get("collection_health") or {},
                usable_for_trading=bool(has_live and timestamp and live.get("availability") == "available"
                                        and freshness.get("status") == "fresh" and freshness.get("usable_for_trading")),
                units=price.get("units") or {"currency": "TWD", "volume": "shares"},
                volume_semantics=live.get("volume_semantics") if has_live else "daily",
                eod_fallback=fallback,
            ))
        return out


class TwmdKlineVendor(KlineVendor):
    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        payload = TwmdClient(config).bars(symbols[0], limit=min(1000, int(config.get("days") or 120)))
        return _observed_bars(payload)


class TwmdIntradayVendor(KlineVendor):
    name = "twmd"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        params = {key: config[key] for key in ("start_date", "end_date") if config.get(key)}
        payload = TwmdClient(config).bars(symbols[0], timeframe=config.get("timeframe", "1m"),
                                         limit=int(config.get("limit") or 270), **params)
        return _observed_bars(payload)


def _observed_bars(payload: dict) -> list[Bar]:
    out = []
    for row in payload.get("bars", []):
        values = [number(row.get(key)) for key in ("open", "close", "high", "low", "volume")]
        if any(value is None for value in values) or row.get("finalized") is False:
            continue
        if row.get("status", "observed") != "observed":
            continue
        out.append(Bar(date=row.get("timestamp") or row["trade_date"], open=values[0], close=values[1],
                       high=values[2], low=values[3], volume=values[4], provider=payload.get("provider"),
                       adjustment_mode=payload.get("adjustment_mode"), volume_unit="shares"))
    return out
