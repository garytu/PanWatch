"""市場指數 API - 公共資料，無需認證"""
import asyncio
import copy
import hashlib
import logging
import time
from decimal import Decimal, localcontext
from fastapi import APIRouter

from src.platform.marketdata.collectors.kline_collector import get_index_klines
from src.platform.marketdata.models import MarketCode, enabled_market_codes, is_market_enabled

logger = logging.getLogger(__name__)
router = APIRouter()


def get_market_data():
    """惰性 import,避免包未裝/迴圈 import 影響本模組載入。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g

    return _g()

# 主要市場指數配置
# response_symbol: 騰訊 API 返回的 symbol（用於匹配）
MARKET_INDICES = [
    # Taiwan uses official persisted EOD index bars, never a stock/live-quote identity.
    {"symbol": "TAIEX", "name": "加權指數", "market": "TW", "benchmark_id": "TAIEX"},
    {"symbol": "TPEX", "name": "櫃買指數", "market": "TW", "benchmark_id": "TPEX"},
    # A股指數
    {"symbol": "000001", "name": "上證指數", "market": "CN", "tencent_symbol": "sh000001", "response_symbol": "000001"},
    {"symbol": "399001", "name": "深證成指", "market": "CN", "tencent_symbol": "sz399001", "response_symbol": "399001"},
    {"symbol": "399006", "name": "創業板指", "market": "CN", "tencent_symbol": "sz399006", "response_symbol": "399006"},
    # 港股指數
    {"symbol": "HSI", "name": "恒生指數", "market": "HK", "tencent_symbol": "hkHSI", "response_symbol": "HSI"},
    # 美股指數 (騰訊返回的 symbol 帶點號字首: .IXIC, .DJI)
    {"symbol": "IXIC", "name": "納斯達克", "market": "US", "tencent_symbol": "usIXIC", "response_symbol": ".IXIC"},
    {"symbol": "DJI", "name": "道瓊斯", "market": "US", "tencent_symbol": "usDJI", "response_symbol": ".DJI"},
]

# 指數回應記憶體快取:60s(行情價格要新鮮)。
_INDICES_CACHE: dict[str, tuple[float, list[dict]]] = {}
_INDICES_CACHE_TTL_S = 60

# spark(近20日收盤)獨立快取:日線一天才變,30 分鐘足夠新鮮。
# 沒有它,回應快取每 60s 過期就要重付一輪 6×指數K線(部分環境東財先失敗再騰訊兜底,
# 序列約 4s)——這曾是首頁快車道最大的延遲來源。空結果也快取(壞源別反覆重拉)。
_SPARK_CACHE: dict[str, tuple[float, list[float]]] = {}
_SPARK_TTL_S = 1800
_OFFICIAL_BENCHMARK_CACHE: dict[tuple[str, str, str, str], tuple[float, dict]] = {}


def clear_indices_cache() -> None:
    """清空指數回應/spark 快取(測試隔離用)。"""
    _INDICES_CACHE.clear()
    _SPARK_CACHE.clear()
    _OFFICIAL_BENCHMARK_CACHE.clear()


def _spark_for(idx: dict) -> list[float]:
    """近 20 日收盤價,供首頁指數走勢 sparkline 用(帶 30min 獨立快取)。

    fail-soft:市場碼非法/取數異常/無對映一律吞掉,返回空列表,絕不影響 quote 主體。
    """
    now = time.time()
    hit = _SPARK_CACHE.get(idx["symbol"])
    if hit and now - hit[0] < _SPARK_TTL_S:
        return hit[1]
    try:
        market_code = MarketCode(idx["market"])
        klines = get_index_klines(idx["symbol"], market_code, days=20)
        spark = [k.close for k in klines] if klines else []
    except Exception as e:
        logger.debug(f"指數 spark 獲取失敗 {idx['symbol']}: {e}")
        spark = []
    _SPARK_CACHE[idx["symbol"]] = (now, spark)
    return spark


def _official_benchmark_for(idx: dict) -> dict:
    """Build a home index item only from true publisher-dated EOD observations."""
    from src.platform.marketdata.marketdata_client import twmd_config

    config = twmd_config()
    scope = (
        str(config.get("base_url") or "").rstrip("/"),
        hashlib.sha256(str(config.get("token") or "").encode()).hexdigest(),
        idx["benchmark_id"],
        "latest:20",
    )
    now = time.time()
    cached = _OFFICIAL_BENCHMARK_CACHE.get(scope)
    if cached and now - cached[0] < _SPARK_TTL_S:
        return copy.deepcopy(cached[1])
    try:
        read = get_market_data().benchmark_bars(idx["benchmark_id"], limit=20, timeout_sec=5)
        bars = list(read.bars)
        if read.benchmark_id != idx["benchmark_id"] or read.venue != idx["expected_venue"]:
            raise ValueError("benchmark response does not match the configured venue")
        if read.unit != "index_points" or read.basis != "raw_price_index" or read.price_kind != "benchmark_index":
            raise ValueError("benchmark response has an invalid index-point contract")
        if read.returned_count != len(bars):
            raise ValueError("benchmark response count does not match its rows")
        if any(bars[offset - 1].trade_date >= bars[offset].trade_date for offset in range(1, len(bars))):
            raise ValueError("benchmark dates are not unique and ascending")
        points = [item for item in bars if item.close.is_finite() and item.close > 0]
        latest = points[-1] if points else None
        previous = points[-2] if len(points) >= 2 else None
        change_amount = change_pct = None
        if latest is not None and previous is not None:
            change_amount = float(latest.close - previous.close)
            with localcontext() as decimal_context:
                decimal_context.prec = 48
                change_pct = float((latest.close / previous.close - Decimal(1)) * Decimal(100))
        result = {
            "symbol": idx["symbol"],
            "name": idx["name"],
            "market": "TW",
            "current_price": float(latest.close) if latest else None,
            "change_pct": change_pct,
            "change_amount": change_amount,
            "prev_close": float(previous.close) if previous else None,
            "spark": [float(item.close) for item in points],
            "spark_dates": [item.trade_date for item in points],
            "price_kind": "eod",
            "trade_date": latest.trade_date if latest else None,
            "change_start_date": previous.trade_date if previous else None,
            "change_end_date": latest.trade_date if latest else None,
            "provider": read.provider,
            "source_alias": read.source_alias,
            "unit": "index_points",
            "basis": "raw_price_index",
            "availability": "available" if latest else "missing",
            "source_partial": read.partial,
            "source_truncated": read.truncated,
        }
        # Calendar gaps and a bounded latest-20 sample stay visible in the payload;
        # valid observations remain useful for the home sparkline.
        if latest is not None:
            _OFFICIAL_BENCHMARK_CACHE[scope] = (now, copy.deepcopy(result))
        return result
    except Exception as exc:
        logger.debug("官方台股基準讀取失敗 %s: %s", idx["benchmark_id"], exc)
        return {
            "symbol": idx["symbol"], "name": idx["name"], "market": "TW",
            "current_price": None, "change_pct": None, "change_amount": None,
            "prev_close": None, "spark": [], "spark_dates": [],
            "price_kind": "eod", "trade_date": None,
            "change_start_date": None, "change_end_date": None,
            "provider": idx["expected_venue"], "source_alias": None,
            "unit": "index_points", "basis": "raw_price_index",
            "availability": "unavailable", "source_partial": None,
            "source_truncated": None,
        }


@router.get("/indices")
async def get_market_indices():
    """獲取主要市場指數（公共資料，無需認證）"""
    active_indices = [idx for idx in MARKET_INDICES if is_market_enabled(idx["market"])]
    if not active_indices:
        return []
    cache_key = ",".join(enabled_market_codes())
    if any(idx["market"] == "TW" for idx in active_indices):
        from src.platform.marketdata.marketdata_client import twmd_config
        config = twmd_config()
        credential_scope = hashlib.sha256(str(config.get("token") or "").encode()).hexdigest()
        provider_scope = hashlib.sha256(
            f"{config.get('base_url') or ''}|{credential_scope}".encode()
        ).hexdigest()
        cache_key = f"{cache_key}|twmd:{provider_scope}"
    now = time.time()
    cached = _INDICES_CACHE.get(cache_key)
    if cached and now - cached[0] < _INDICES_CACHE_TTL_S:
        return copy.deepcopy(cached[1])

    tencent_indices = [idx for idx in active_indices if not idx.get("benchmark_id")]
    tencent_symbols = [idx["tencent_symbol"] for idx in tencent_indices]

    quotes_failed = False
    try:
        quotes = get_market_data().index_quotes(tencent_symbols) if tencent_symbols else []
    except Exception as e:
        logger.error(f"獲取市場指數失敗: {e}")
        quotes = []
        quotes_failed = True

    # 構建 response_symbol -> quote 對映
    quote_map = {}
    for q in quotes:
        quote_map[q["symbol"]] = q

    # spark 並行取(快取未過期時零成本;冷啟動=最慢單個≈1s,而非 6 個序列累加)
    sparks = await asyncio.gather(
        *[asyncio.to_thread(_spark_for, idx) for idx in tencent_indices],
        return_exceptions=True,
    )
    spark_map = {
        idx["symbol"]: (sp if isinstance(sp, list) else [])
        for idx, sp in zip(tencent_indices, sparks)
    }
    taiwan_results = await asyncio.gather(*[
        asyncio.to_thread(_official_benchmark_for, {
            **idx,
            "expected_venue": "TWSE" if idx["benchmark_id"] == "TAIEX" else "TPEX",
        })
        for idx in active_indices if idx.get("benchmark_id")
    ])
    taiwan_map = {item["symbol"]: item for item in taiwan_results}

    result = []
    for idx in active_indices:
        if idx.get("benchmark_id"):
            result.append(taiwan_map.get(idx["symbol"]) or {
                "symbol": idx["symbol"], "name": idx["name"], "market": "TW",
                "current_price": None, "change_pct": None, "change_amount": None,
                "prev_close": None, "spark": [], "spark_dates": [],
                "price_kind": "eod", "trade_date": None,
                "availability": "unavailable", "unit": "index_points",
                "basis": "raw_price_index",
            })
            continue
        # 使用 response_symbol 匹配
        quote = quote_map.get(idx["response_symbol"])
        spark = spark_map.get(idx["symbol"], [])

        if quote:
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": quote["current_price"],
                "change_pct": quote["change_pct"],
                "change_amount": quote["change_amount"],
                "prev_close": quote["prev_close"],
                "spark": spark,
            })
        else:
            # 即使沒有行情也返回基本資訊
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": None,
                "change_pct": None,
                "change_amount": None,
                "prev_close": None,
                "spark": spark,
            })

    if not quotes_failed and all(item.get("availability") == "available" for item in taiwan_results):
        _INDICES_CACHE[cache_key] = (now, copy.deepcopy(result))
    return result
