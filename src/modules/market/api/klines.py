from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException
from datetime import datetime

from pydantic import BaseModel, Field

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.models import MarketCode

router = APIRouter()


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US")
    days: int | None = Field(default=60, description="K線天數")
    interval: str | None = Field(default="1d", description="週期: 1d/1w/1m")


class KlineBatchRequest(BaseModel):
    items: list[KlineItem]


class KlineSummaryItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US")


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
            )
        )
    out.sort(key=lambda k: k.date)
    return out


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
