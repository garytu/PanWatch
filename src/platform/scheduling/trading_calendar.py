"""交易日曆:回答「這一天開不開市」。

與 `MarketDef.is_trading_time()`(回答「當下是否在交易時段內」)互補 ——
盤前計劃、日終摘要這類定時任務本身就發生在交易時段之外,只能用「是不是交易日」
來守衛,用時段判斷會把它們永久攔死。

資料來源
- **A 股**:akshare 交易日曆(`tool_trade_date_hist_sina`),含法定節假日,權威。
  結果快取在記憶體,由 `refresh()` 更新(啟動預熱 + 每日凌晨重新整理)。
- **港股 / 美股**:沒有等價的公開日曆源,只判週末(誠實降級,不假裝支援節假日)。

降級原則
拿不到日曆時退回「只判週末」—— 寧可多發一條通知,也不能把交易日誤判為休市。
少發一條是遺憾,漏發一整天是事故。

併發安全
同步介面只讀記憶體快取,**永不發起網路請求**;網路拉取集中在 `refresh()`
(內部 `asyncio.to_thread`)和 `refresh_blocking()`,避免阻塞事件迴圈。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# A 股交易日集合;None = 尚未載入或載入失敗(此時降級為只判週末)
_CN_TRADING_DATES: frozenset[date] | None = None
# 日曆覆蓋區間,用於判斷查詢日期是否落在可信範圍內(跨年未重新整理時會超出)
_CN_RANGE: tuple[date, date] | None = None

_FALLBACK_TZ = "Asia/Shanghai"


def reset_cache() -> None:
    """清空日曆快取(配置變更或測試用)。"""
    global _CN_TRADING_DATES, _CN_RANGE
    _CN_TRADING_DATES = None
    _CN_RANGE = None


def _fetch_cn_trading_dates() -> frozenset[date]:
    """阻塞拉取 A 股交易日曆。僅由 `refresh_blocking()` 呼叫。"""
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    out: set[date] = set()
    for raw in df["trade_date"]:
        if isinstance(raw, datetime):
            out.add(raw.date())
        elif isinstance(raw, date):
            out.add(raw)
        else:
            out.add(date.fromisoformat(str(raw)[:10]))
    return frozenset(out)


def refresh_blocking() -> bool:
    """同步重新整理 A 股交易日曆。返回是否成功;失敗不拋異常(保持降級行為)。"""
    global _CN_TRADING_DATES, _CN_RANGE
    try:
        dates = _fetch_cn_trading_dates()
    except Exception as e:
        logger.warning("[交易日曆] A股日曆拉取失敗,降級為只判週末: %s", e)
        return False
    if not dates:
        logger.warning("[交易日曆] A股日曆為空,降級為只判週末")
        return False
    _CN_TRADING_DATES = dates
    _CN_RANGE = (min(dates), max(dates))
    logger.info(
        "[交易日曆] A股日曆已載入: %s 個交易日 (%s ~ %s)",
        len(dates),
        _CN_RANGE[0],
        _CN_RANGE[1],
    )
    return True


async def refresh() -> bool:
    """非同步重新整理日曆(走執行緒池,不阻塞事件迴圈)。"""
    return await asyncio.to_thread(refresh_blocking)


def _to_market_code(market):
    """把 MarketCode / 字串歸一化為 MarketCode;無法識別返回 None。"""
    from src.platform.marketdata.models import MarketCode

    if isinstance(market, MarketCode):
        return market
    try:
        return MarketCode(str(market).strip().upper())
    except ValueError:
        return None


def _market_tz(code) -> ZoneInfo:
    from src.platform.marketdata.models import MARKETS

    md = MARKETS.get(code) if code else None
    return md.get_tz() if md else ZoneInfo(_FALLBACK_TZ)


def _now_in_market_tz(code) -> datetime:
    """該市場時區的當前時間。獨立成函式便於測試注入。"""
    return datetime.now(_market_tz(code))


def _resolve_date(code, d: date | datetime | None) -> date:
    """把入參歸一化為「該市場當地日期」。"""
    if d is None:
        return _now_in_market_tz(code).date()
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            d = d.astimezone(_market_tz(code))
        return d.date()
    return d


def is_trading_day(market, d: date | datetime | None = None) -> bool:
    """給定市場的某一天是否開市。

    Args:
        market: `MarketCode` 或市場碼字串(CN/HK/US)。
        d: 目標日期;`None` 表示該市場時區的今天。帶時區的 `datetime`
           會先換算到市場時區再取日期。
    """
    from src.platform.marketdata.models import MarketCode

    code = _to_market_code(market)
    target = _resolve_date(code, d)

    # 週末:三個市場都不開。零依賴、永遠準確,放在最前面。
    if target.weekday() >= 5:
        return False

    # A 股:日曆已載入且覆蓋該日期時按日曆判(含法定節假日)。
    if code == MarketCode.CN and _CN_TRADING_DATES and _CN_RANGE:
        if _CN_RANGE[0] <= target <= _CN_RANGE[1]:
            return target in _CN_TRADING_DATES
        logger.debug("[交易日曆] %s 超出A股日曆覆蓋範圍,降級為只判週末", target)

    # 港美股、日曆缺失、超出覆蓋範圍:只判週末。
    return True


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    """CN/HK/US 任一為交易日即 `True`。全市場休市(如週末)返回 `False`。"""
    from src.platform.marketdata.models import MarketCode

    return any(
        is_trading_day(m, d) for m in (MarketCode.CN, MarketCode.HK, MarketCode.US)
    )
