"""市場指數 API - 公共資料，無需認證"""
import asyncio
import logging
import time
from fastapi import APIRouter

from src.platform.marketdata.collectors.kline_collector import get_index_klines
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()


def get_market_data():
    """惰性 import,避免包未裝/迴圈 import 影響本模組載入。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g

    return _g()

# 主要市場指數配置
# response_symbol: 騰訊 API 返回的 symbol（用於匹配）
MARKET_INDICES = [
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


def clear_indices_cache() -> None:
    """清空指數回應/spark 快取(測試隔離用)。"""
    _INDICES_CACHE.clear()
    _SPARK_CACHE.clear()


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


@router.get("/indices")
async def get_market_indices():
    """獲取主要市場指數（公共資料，無需認證）"""
    now = time.time()
    cached = _INDICES_CACHE.get("indices")
    if cached and now - cached[0] < _INDICES_CACHE_TTL_S:
        return cached[1]

    tencent_symbols = [idx["tencent_symbol"] for idx in MARKET_INDICES]

    try:
        quotes = get_market_data().index_quotes(tencent_symbols)
    except Exception as e:
        logger.error(f"獲取市場指數失敗: {e}")
        return []

    # 構建 response_symbol -> quote 對映
    quote_map = {}
    for q in quotes:
        quote_map[q["symbol"]] = q

    # spark 並行取(快取未過期時零成本;冷啟動=最慢單個≈1s,而非 6 個序列累加)
    sparks = await asyncio.gather(
        *[asyncio.to_thread(_spark_for, idx) for idx in MARKET_INDICES],
        return_exceptions=True,
    )
    spark_map = {
        idx["symbol"]: (sp if isinstance(sp, list) else [])
        for idx, sp in zip(MARKET_INDICES, sparks)
    }

    result = []
    for idx in MARKET_INDICES:
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

    _INDICES_CACHE["indices"] = (now, result)
    return result
