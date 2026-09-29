"""交易日曆:回答「這一天開不開市」。

與 `MarketDef.is_trading_time()`(回答「當下是否在交易時段內」)互補 ——
盤前計劃、日終摘要這類定時任務本身就發生在交易時段之外,只能用「是不是交易日」
來守衛,用時段判斷會把它們永久攔死。

資料來源
- **A 股**:akshare 交易日曆(`tool_trade_date_hist_sina`),含法定節假日,權威。
  結果快取在記憶體,由 `refresh()` 更新(啟動預熱 + 每日凌晨重新整理)。
- **港股 / 美股**:沒有等價的公開日曆源,只判週末(誠實降級,不假裝支援節假日)。
- **台股**:證交所年度休市日曆,使用當年度磁碟快取;額外休市日可由配置補充。

降級原則
A 股拿不到日曆時退回「只判週末」。台股日曆未知或超出年度覆蓋時停止交易時段任務,
避免僅憑平日判斷就允許提醒或模擬成交。

併發安全
同步介面只讀記憶體快取,**永不發起網路請求**;網路拉取集中在 `refresh()`
(內部 `asyncio.to_thread`)和 `refresh_blocking()`,避免阻塞事件迴圈。
"""

from __future__ import annotations

from src.platform.marketdata.models import enabled_market_codes

import asyncio
import logging
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# A 股交易日集合;None = 尚未載入或載入失敗(此時降級為只判週末)
_CN_TRADING_DATES: frozenset[date] | None = None
_CN_RANGE: tuple[date, date] | None = None

# 台股交易日集合;None = 未知，此時不允許交易時段任務
_TW_TRADING_DATES: frozenset[date] | None = None
_TW_RANGE: tuple[date, date] | None = None
_TW_CALENDAR_CACHE = Path(__file__).resolve().parents[3] / "data" / "tw_trading_calendar.json"

_FALLBACK_TZ = "Asia/Shanghai"


def reset_cache() -> None:
    """清空日曆快取(配置變更或測試用)。"""
    global _CN_TRADING_DATES, _CN_RANGE, _TW_TRADING_DATES, _TW_RANGE
    _CN_TRADING_DATES = None
    _CN_RANGE = None
    _TW_TRADING_DATES = None
    _TW_RANGE = None


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


def _parse_tw_calendar(payload: dict, year: int) -> frozenset[date]:
    if (not isinstance(payload, dict) or payload.get("stat") != "ok"
            or str(payload.get("queryYear")) != str(year) or not payload.get("data")):
        raise ValueError("TWSE calendar unavailable or year mismatch")
    closed = set()
    for row in payload["data"]:
        if not isinstance(row, list) or len(row) < 2:
            raise ValueError("Invalid TWSE calendar row")
        day = date.fromisoformat(row[0])
        if day.year != year:
            raise ValueError("TWSE calendar year mismatch")
        label = str(row[1])
        # These rows announce trading dates, rather than holidays.
        if "開始交易" in label or "最後交易" in label:
            continue
        closed.add(day)
    start = date(year, 1, 1)
    return frozenset(start + timedelta(days=i) for i in range((date(year + 1, 1, 1) - start).days)
                     if (start + timedelta(days=i)).weekday() < 5 and start + timedelta(days=i) not in closed)


def _fetch_tw_trading_dates() -> frozenset[date]:
    """Validate the official annual schedule before replacing a usable cached copy."""
    from marketdata.http import market_get
    year = datetime.now(ZoneInfo("Asia/Taipei")).year
    payload = market_get("https://www.twse.com.tw/holidaySchedule/holidaySchedule", host_key="twse_calendar",
                         params={"response": "json", "queryYear": year}, parse="json", timeout=10, retries=0)
    try:
        dates = _parse_tw_calendar(payload, year)
    except (ValueError, TypeError, KeyError):
        try:
            return _parse_tw_calendar(json.loads(_TW_CALENDAR_CACHE.read_text()), year)
        except (OSError, ValueError, TypeError, KeyError):
            return frozenset()
    try:
        _TW_CALENDAR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temp = _TW_CALENDAR_CACHE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(_TW_CALENDAR_CACHE)
    except OSError:
        logger.warning("Unable to persist Taiwan calendar cache", exc_info=True)
    return dates


def refresh_blocking() -> bool:
    """同步重新整理交易日曆(A股 + 台股)。返回是否至少有一個成功;失敗不拋異常(保持降級行為)。"""
    global _CN_TRADING_DATES, _CN_RANGE, _TW_TRADING_DATES, _TW_RANGE

    cn_ok = False
    tw_ok = False
    try:
        dates = _fetch_cn_trading_dates()
        if dates:
            _CN_TRADING_DATES = dates
            _CN_RANGE = (min(dates), max(dates))
            logger.info(
                "[交易日曆] A股日曆已載入: %s 個交易日 (%s ~ %s)",
                len(dates),
                _CN_RANGE[0],
                _CN_RANGE[1],
            )
            cn_ok = True
    except Exception as e:
        logger.warning("[交易日曆] A股日曆拉取失敗,降級為只判週末: %s", e)

    try:
        tw_dates = _fetch_tw_trading_dates()
        if tw_dates:
            _TW_TRADING_DATES = tw_dates
            year = min(tw_dates).year
            _TW_RANGE = (date(year, 1, 1), date(year, 12, 31))
            tw_ok = True
            logger.info(
                "[交易日曆] 台股日曆已載入: %s 個交易日 (%s ~ %s)",
                len(tw_dates),
                _TW_RANGE[0],
                _TW_RANGE[1],
            )
    except Exception as e:
        logger.warning("[交易日曆] 台股日曆拉取失敗，覆蓋未知時暫停交易: %s", e)

    return cn_ok or tw_ok



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

    # 台股:日曆已載入且覆蓋該日期時按日曆判(含法定節假日)。
    if code == MarketCode.TW:
        from src.platform.runtime.config import Settings
        extra_closed = Settings().tw_extra_closed_dates.split(",")
        if target.isoformat() in {day.strip() for day in extra_closed}:
            return False
        if _TW_TRADING_DATES and _TW_RANGE and _TW_RANGE[0] <= target <= _TW_RANGE[1]:
            return target in _TW_TRADING_DATES
        return False

    # 港美股、日曆缺失、超出覆蓋範圍:只判週末。
    return True


def calendar_status(market, d: date | datetime | None = None) -> dict:
    from src.platform.marketdata.models import MarketCode
    code = _to_market_code(market)
    target = _resolve_date(code, d)
    covered = code != MarketCode.TW or bool(_TW_RANGE and _TW_RANGE[0] <= target <= _TW_RANGE[1])
    return {"status": "known" if covered else "unknown", "date": target.isoformat(),
            "source": "TWSE annual schedule" if code == MarketCode.TW and covered else None,
            "is_trading_day": is_trading_day(code, target),
            "coverage_start": _TW_RANGE[0].isoformat() if code == MarketCode.TW and _TW_RANGE else None,
            "coverage_end": _TW_RANGE[1].isoformat() if code == MarketCode.TW and _TW_RANGE else None}


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    """已啟用市場任一為交易日即 `True`。"""

    return any(
        is_trading_day(m, d) for m in enabled_market_codes()
    )
