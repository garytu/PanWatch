import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.platform.marketdata.marketdata_client import md_quote_rows, QUOTE_METADATA
from src.platform.marketdata.models import MarketCode
from src.platform.marketdata.twmd_control import TwmdControlClient, TwmdControlError

router = APIRouter()


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US/TW")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支援的市場: {market}")


def _quote_to_response(symbol: str, market: MarketCode, quote: dict | None) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market.value,
            "name": None,
            "current_price": None,
            "change_pct": None,
            "change_amount": None,
            "prev_close": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "pe_ratio": None,
            "total_market_value": None,
            "circulating_market_value": None,
            "timestamp": None,
            "availability": "unavailable",
            "usable_for_trading": False,
        }

    return {
        "symbol": symbol,
        "market": market.value,
        "name": quote.get("name"),
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": quote.get("prev_close"),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "turnover_rate": quote.get("turnover_rate"),
        "pe_ratio": quote.get("pe_ratio"),
        "total_market_value": quote.get("total_market_value"),
        "circulating_market_value": quote.get("circulating_market_value"),
        "timestamp": quote.get("timestamp"),
        **{key: quote.get(key) for key in QUOTE_METADATA},
    }


@router.get("/taiwan/status")
def get_taiwan_feed_status():
    """Readiness and watchlist subscription coverage; never mutates twmd configuration."""
    from concurrent.futures import ThreadPoolExecutor
    from marketdata.symbol import Symbol
    from marketdata.vendors.twmd import TwmdClient
    from src.platform.marketdata.marketdata_client import twmd_config
    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import Stock
    from src.platform.scheduling.trading_calendar import calendar_status
    control = TwmdControlClient()
    try:
        subscription_state = control.subscriptions()
        control_error = None
    except TwmdControlError as exc:
        subscription_state = None
        control_error = str(exc)
    client = TwmdClient(twmd_config())
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.get, path) for path in ("quotes/capabilities", "bars/capabilities", "readiness")]
        quotes, bars, storage = [future.result() or {} for future in futures]
    # Usage/account information is unnecessary for this UI response.
    quotes.pop("account_usage", None)
    catalog = client.instruments()
    active_counts = {venue: sum(row.get("venue") == venue and bool(row.get("is_active"))
                               and row.get("security_type") in {"EQUITY", "ETF", "PREFERRED"} for row in catalog)
                     for venue in ("TWSE", "TPEX")}
    requested, unresolved = [], []
    with SessionLocal() as db:
        symbols = [row.symbol for row in db.query(Stock).filter(Stock.market == "TW").all()]
    for symbol in symbols:
        try:
            requested.append(client.resolve(Symbol.parse(symbol, "TW")))
        except ValueError:
            unresolved.append(symbol)
    confirmed = set(quotes.get("confirmed_instrument_ids") or [])
    desired = set(subscription_state.get("instrument_ids") or []) if subscription_state else set()
    watchlist = set(requested)
    return {"quotes": quotes, "intraday": bars, "storage": storage, "calendar": calendar_status("TW"),
            "watchlist_instrument_ids": sorted(watchlist), "unresolved_symbols": unresolved,
            "subscription_control": {"available": subscription_state is not None,
                                     "error": control_error,
                                     "instrument_ids": sorted(desired),
                                     "count": subscription_state.get("count") if subscription_state else None,
                                     "limit": subscription_state.get("limit") if subscription_state else None},
            "unsubscribed_instrument_ids": sorted(watchlist - desired) if subscription_state else [],
            "pending_subscription_instrument_ids": sorted((watchlist & desired) - confirmed) if subscription_state else [],
            "active_instrument_counts": active_counts,
            "subscription_coverage_complete": subscription_state is not None and not unresolved and watchlist <= confirmed}


def _validated_instrument_id(instrument_id: str, *, require_active: bool) -> str:
    from marketdata.symbol import Symbol
    from marketdata.vendors.twmd import TwmdClient
    from src.platform.marketdata.marketdata_client import twmd_config
    try:
        parsed = Symbol.parse(instrument_id)
    except ValueError:
        raise HTTPException(400, "請使用 TWSE: 或 TPEX: 股票代碼")
    if parsed.identity != instrument_id or parsed.venue not in {"TWSE", "TPEX"}:
        raise HTTPException(400, "請使用 TWSE: 或 TPEX: 股票代碼")
    if require_active:
        catalog = TwmdClient(twmd_config()).instruments()
        if not catalog:
            raise HTTPException(503, "twmd 標的目錄暫時無法連線")
        if not any(row.get("instrument_id") == instrument_id and row.get("is_active")
                   and row.get("security_type") in {"EQUITY", "ETF", "PREFERRED"} for row in catalog):
            raise HTTPException(400, "標的不是有效的台股現貨證券")
    return instrument_id


def _change_subscription(method: str, instrument_id: str) -> dict:
    canonical = _validated_instrument_id(instrument_id, require_active=method == "PUT")
    try:
        return TwmdControlClient().subscriptions(method, canonical)
    except TwmdControlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.put("/taiwan/subscriptions/{instrument_id}")
def subscribe_taiwan_quote(instrument_id: str):
    return _change_subscription("PUT", instrument_id)


@router.delete("/taiwan/subscriptions/{instrument_id}")
def unsubscribe_taiwan_quote(instrument_id: str):
    return _change_subscription("DELETE", instrument_id)


@router.get("/{symbol}")
async def get_quote(symbol: str, market: str = "CN"):
    """獲取單隻股票即時行情"""
    market_code = _parse_market(market)
    rows = await asyncio.to_thread(md_quote_rows, [symbol], market_code.value)
    if not rows:
        raise HTTPException(404, "行情不存在")
    quote_map = {item.get("symbol"): item for item in rows}
    quote = quote_map.get(symbol)
    if not quote:
        raise HTTPException(404, "行情不存在")
    return _quote_to_response(symbol, market_code, quote)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批次獲取股票即時行情"""
    if not payload.items:
        return []

    market_items: dict[MarketCode, list[str]] = {}
    for item in payload.items:
        market_code = _parse_market(item.market)
        market_items.setdefault(market_code, []).append(item.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        rows = await asyncio.to_thread(md_quote_rows, symbols, market_code.value)
        quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        quote = quotes_by_market.get(market_code, {}).get(item.symbol)
        results.append(_quote_to_response(item.symbol, market_code, quote))

    return results
