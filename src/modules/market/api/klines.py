from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime

from pydantic import BaseModel, Field

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.models import MarketCode

router = APIRouter()


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US/TW")
    days: int | None = Field(default=60, description="K線天數")
    interval: str | None = Field(default="1d", description="週期: 1d/1w/1m")


class KlineBatchRequest(BaseModel):
    items: list[KlineItem]


class KlineSummaryItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US/TW")


class KlineSummaryBatchRequest(BaseModel):
    items: list[KlineSummaryItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支援的市場: {market}")


def _serialize_klines(klines) -> list[dict]:
    return [
        {
            "date": k.date,
            "open": k.open,
            "close": k.close,
            "high": k.high,
            "low": k.low,
            "volume": k.volume,
            "provider": getattr(k, "provider", None),
            "adjustment_mode": getattr(k, "adjustment_mode", None),
            "volume_unit": getattr(k, "volume_unit", None),
        }
        for k in klines
    ]


def _aggregate_klines(klines, interval: str) -> list:
    """Aggregate daily klines to week/month."""

    iv = (interval or "1d").lower()
    if iv in ("1d", "day", "d"):
        return klines
    if iv not in ("1w", "1m", "week", "month", "w", "m"):
        return klines

    parsed = []
    for k in klines or []:
        try:
            dt = datetime.strptime(k.date, "%Y-%m-%d")
        except Exception:
            continue
        parsed.append((dt, k))

    parsed.sort(key=lambda x: x[0])
    buckets: dict[str, list] = {}
    for dt, k in parsed:
        if iv in ("1w", "week", "w"):
            y, w, _ = dt.isocalendar()
            key = f"{y:04d}-W{w:02d}"
        else:
            key = f"{dt.year:04d}-{dt.month:02d}"
        buckets.setdefault(key, []).append((dt, k))

    out = []
    for _, items in buckets.items():
        items.sort(key=lambda x: x[0])
        first = items[0][1]
        last = items[-1][1]
        high = max(it[1].high for it in items)
        low = min(it[1].low for it in items)
        vol = sum(it[1].volume for it in items)
        out.append(
            type(first)(
                date=items[-1][0].strftime("%Y-%m-%d"),
                open=first.open,
                close=last.close,
                high=high,
                low=low,
                volume=vol,
                provider=getattr(first, "provider", None),
                adjustment_mode=getattr(first, "adjustment_mode", None),
                volume_unit=getattr(first, "volume_unit", None),
            )
        )
    out.sort(key=lambda k: k.date)
    return out


@router.get("/{symbol}/intraday")
def get_intraday_klines(symbol: str, market: str = "TW", timeframe: str = "1m",
                       limit: int = Query(270, ge=1, le=1000),
                       start_date: str | None = None, end_date: str | None = None):
    """Stored minute bars. Missing/no-trade slots retain null prices and their status."""
    from marketdata.symbol import Symbol
    from marketdata.vendors.twmd import TwmdClient, number
    from src.platform.marketdata.marketdata_client import twmd_config
    if market != "TW" or timeframe not in {"1m", "5m"}:
        raise HTTPException(400, "台股歷史分K支援 1m/5m")
    params = {}
    if bool(start_date) != bool(end_date):
        raise HTTPException(400, "start_date/end_date 必須一起提供")
    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(400, "日期必須為 YYYY-MM-DD") from exc
        if (end - start).days < 0 or (end - start).days >= 30:
            raise HTTPException(400, "分K日期範圍最多 30 天")
        params = {"start_date": start_date, "end_date": end_date}
    try:
        payload = TwmdClient(twmd_config()).bars(Symbol.parse(symbol, "TW"), timeframe=timeframe,
                                               limit=limit, **params)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not payload:
        raise HTTPException(503, "twmd 分K資料來源不可用")
    bars = [{**bar, "date": bar["timestamp"],
             **{field: number(bar.get(field)) for field in ("open", "close", "high", "low", "volume", "turnover")}}
            for bar in payload.get("bars", [])]
    valid = [bar for bar in bars if bar.get("finalized") and bar.get("status") == "observed"
             and all(bar.get(field) is not None for field in ("open", "close", "high", "low", "volume"))]
    summary = None
    if payload.get("coverage_complete") and len(valid) >= 20:
        from src.platform.marketdata.collectors.kline_collector import KlineData
        data = [KlineData(date=bar["date"], open=bar["open"], close=bar["close"],
                          high=bar["high"], low=bar["low"], volume=bar["volume"]) for bar in valid]
        from dataclasses import asdict
        summary = asdict(KlineCollector(MarketCode.TW).get_technical_indicators(klines=data))
    return {**payload, "symbol": symbol, "market": "TW", "klines": bars, "summary": summary,
            "live_collection": False, "usable_for_trading": False}


@router.get("/{symbol}")
def get_klines(symbol: str, market: str = "CN", days: int = 60, interval: str = "1d"):
    """獲取單隻股票K線資料"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    klines = collector.get_klines(symbol, days=days)
    klines = _aggregate_klines(klines, interval)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "days": days,
        "interval": interval,
        "klines": _serialize_klines(klines),
    }


@router.post("/batch")
def get_klines_batch(payload: KlineBatchRequest):
    """批次獲取K線資料"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        collector = KlineCollector(market_code)
        days = item.days or 60
        interval = item.interval or "1d"
        klines = collector.get_klines(item.symbol, days=days)
        klines = _aggregate_klines(klines, interval)
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "days": days,
                "interval": interval,
                "klines": _serialize_klines(klines),
            }
        )

    return results


@router.get("/{symbol}/summary")
def get_kline_summary(symbol: str, market: str = "CN"):
    """獲取單隻股票K線摘要"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    summary = collector.get_kline_summary(symbol)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "summary": summary,
    }


@router.post("/summary/batch")
def get_kline_summary_batch(payload: KlineSummaryBatchRequest):
    """批次獲取K線摘要"""
    if not payload.items:
        return []

    market_codes = [_parse_market(item.market) for item in payload.items]

    def load_one(index: int):
        item = payload.items[index]
        market_code = market_codes[index]
        try:
            summary = KlineCollector(market_code).get_kline_summary(item.symbol)
        except Exception as exc:
            summary = {"error": str(exc)}
        return {
            "symbol": item.symbol,
            "market": market_code.value,
            "summary": summary,
        }

    # 與前端原先的併發上限保持一致，減少批次介面對資料來源的瞬時壓力。
    with ThreadPoolExecutor(max_workers=min(5, len(payload.items))) as executor:
        futures = [executor.submit(load_one, index) for index in range(len(payload.items))]
        return [future.result() for future in futures]
