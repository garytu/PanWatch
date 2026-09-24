"""K 線 vendors:騰訊(全市場)/ Stooq(US)/ 東財(CN/HK)/ Yahoo(US/HK)。移植自 PanWatch kline_collector 抓取核。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import Bar
from marketdata.vendors.base import KlineVendor

logger = logging.getLogger(__name__)

_TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_STOOQ_URL = "https://stooq.com/q/d/l/"
_YAHOO_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}"


def _days(config: dict, default: int = 60) -> int:
    try:
        return int(config.get("days") or default)
    except Exception:
        return default


# 騰訊 fqkline 對 count 有上限:實測 ≤800 正常返(800→801根),1000-2000 退化到 ~641,
# ≥3000 直接返空(0根)。上層 want 常放大到 3000(為長曆史/回測),若原樣透傳騰訊會返 0
# → 每個標的都白白落到東財補全 → 東財一掛就沒資料。故把請求 count 截到 800(取回最多)。
_TENCENT_MAX_COUNT = 800


def fetch_tencent_kline_raw(tsym: str, days: int) -> list[Bar]:
    """按**原始騰訊符號**取日K(不經 Symbol 轉換)。

    供指數等顯式符號場景複用(sh000001/hkHSI/usDJI…;指數與個股的符號規則不同,
    必須顯式傳入)。個股路徑請走 TencentKlineVendor。
    """
    days = min(max(int(days or 1), 1), _TENCENT_MAX_COUNT)
    text = market_get(
        _TENCENT_URL, host_key="web.ifzq.gtimg.cn", min_interval_s=0.15,
        params={"param": f"{tsym},day,,,{days},qfq", "_var": "kline_dayqfq"},
        timeout=10, retries=2, parse="text", log_label="騰訊K線", symbol=tsym,
    )
    if not text or "=" not in text:
        return []
    js = text.split("=", 1)[1].strip().rstrip(";")
    try:
        data = json.loads(js)
    except Exception:
        return []
    raw = data.get("data", {}) if isinstance(data, dict) else {}
    day = []
    if isinstance(raw, dict):
        sd = raw.get(tsym, {})
        if isinstance(sd, dict):
            day = sd.get("day") or sd.get("qfqday") or []
    elif isinstance(raw, list):
        day = raw
    out: list[Bar] = []
    for it in day or []:
        if len(it) >= 5:
            try:
                out.append(Bar(date=it[0], open=float(it[1]), close=float(it[2]),
                               high=float(it[3]), low=float(it[4]),
                               volume=float(it[5]) if len(it) > 5 else 0.0))
            except Exception:
                continue
    return out


# 騰訊美股日K必須帶交易所字尾(usTSLA.OQ=納斯達克 / usBABA.N=紐交所);裸 us{CODE}
# 只回"首日+最新"兩根退化資料,錯字尾只回 1 根。字尾無法從程式碼推斷 → 依次試
# .OQ/.N/裸,根數達標即命中並程序內記憶(下次直達,不再多請求)。
_US_SUFFIX_CACHE: dict[str, str] = {}


def _fetch_tencent_us_kline(code: str, days: int) -> list[Bar]:
    want = min(max(int(days or 1), 1), _TENCENT_MAX_COUNT)
    ok_threshold = min(want, 5)  # 正常歷史遠多於 5 根;退化回應只有 1-2 根
    cached = _US_SUFFIX_CACHE.get(code)
    suffixes = ([cached] if cached is not None else []) + [
        s for s in (".OQ", ".N", "") if s != cached
    ]
    best: list[Bar] = []
    for suf in suffixes:
        bars = fetch_tencent_kline_raw(f"us{code}{suf}", want)
        if len(bars) >= ok_threshold:
            _US_SUFFIX_CACHE[code] = suf
            return bars
        if len(bars) > len(best):
            best = bars
    return best


class TencentKlineVendor(KlineVendor):
    name = "tencent"
    supports_markets = {"CN", "HK", "US"}

    _MAX_COUNT = _TENCENT_MAX_COUNT  # 相容舊引用(測試/外部按類屬性取)

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market == Market.US:
            return _fetch_tencent_us_kline(sym.code, _days(config))
        return fetch_tencent_kline_raw(sym.to_tencent(), _days(config))


class StooqKlineVendor(KlineVendor):
    name = "stooq"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0].code.strip().lower()
        if not sym:
            return []
        text = market_get(
            _STOOQ_URL, host_key="stooq.com", params={"s": f"{sym}.us", "i": "d"},
            headers={"User-Agent": "PanWatch/1.0 (+https://github.com/)"},
            timeout=12, retries=2, parse="text", log_label="Stooq K線", symbol=sym,
        )
        if not text:
            return []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) <= 1:
            return []
        out: list[Bar] = []
        for ln in lines[1:]:
            p = ln.split(",")
            if len(p) < 6 or not p[0] or p[0] == "Date":
                continue
            try:
                out.append(Bar(date=p[0], open=float(p[1]), close=float(p[4]),
                               high=float(p[2]), low=float(p[3]),
                               volume=float(p[5]) if p[5] else 0.0))
            except Exception:
                continue
        return out


def _em_secid(sym: Symbol) -> str:
    if sym.market == Market.HK:
        return f"116.{sym.code}"
    if sym.market == Market.US:
        return f"105.{sym.code}"
    from marketdata.symbol import _cn_exchange
    return f"{'1' if _cn_exchange(sym.code) == 'sh' else '0'}.{sym.code}"


def fetch_eastmoney_kline(secid: str, days: int) -> list[Bar]:
    """按顯式 secid 取東財日K,不經個股 secid 推導規則(_em_secid)。

    供指數等顯式符號場景複用(指數與個股 secid 字首規則不同,必須顯式對映)。
    """
    payload = market_get(
        _EASTMONEY_URL, host_key="push2his.eastmoney.com", min_interval_s=0.2,
        params={"secid": secid, "klt": "101", "fqt": "1",
                "lmt": str(min(max(int(days or 1), 1200), 20000)), "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        timeout=12, retries=1, parse="json", log_label="東財K線", symbol=secid,
    )
    raw = (payload or {}).get("data", {}).get("klines", []) if isinstance(payload, dict) else []
    out: list[Bar] = []
    for row in raw or []:
        p = str(row).split(",")
        if len(p) < 6:
            continue
        try:
            out.append(Bar(date=p[0], open=float(p[1]), close=float(p[2]),
                           high=float(p[3]), low=float(p[4]), volume=float(p[5])))
        except Exception:
            continue
    return out


class EastmoneyKlineVendor(KlineVendor):
    name = "eastmoney"
    supports_markets = {"CN", "HK"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market not in (Market.CN, Market.HK):
            return []
        days = _days(config)
        return fetch_eastmoney_kline(_em_secid(sym), days)


def _yahoo_range(days: int) -> str:
    """days → Yahoo chart v8 的 range 列舉(不用 period1/period2,避免依賴當前時間)。"""
    if days <= 5:
        return "5d"
    if days <= 22:
        return "1mo"
    if days <= 66:
        return "3mo"
    if days <= 130:
        return "6mo"
    if days <= 260:
        return "1y"
    if days <= 520:
        return "2y"
    if days <= 1300:
        return "5y"
    return "max"


class YahooKlineVendor(KlineVendor):
    """Yahoo chart v8 日K,零 crumb / 零 cookie(crumb 只有 quoteSummary 基本面才需要)。"""

    name = "yahoo"
    supports_markets = {"US", "HK"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market not in (Market.US, Market.HK):
            return []
        days = _days(config)
        ysym = sym.to_yfinance()
        proxy = config.get("proxy")
        payload = market_get(
            _YAHOO_CHART_URL.format(sym=ysym), host_key="query2.finance.yahoo.com",
            params={"interval": "1d", "range": _yahoo_range(days)},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=10, retries=2, parse="json", proxy=proxy,
            log_label="Yahoo K線", symbol=ysym,
        )
        if not isinstance(payload, dict):
            return []
        try:
            result = ((payload.get("chart") or {}).get("result")) or []
            if not result:
                return []
            r0 = result[0] or {}
            timestamps = r0.get("timestamp") or []
            indicators = r0.get("indicators") or {}
            quote = (indicators.get("quote") or [{}])[0] or {}
            adjcloses = (indicators.get("adjclose") or [{}])[0].get("adjclose") if indicators.get("adjclose") else None
        except Exception:
            return []
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        out: list[Bar] = []
        for i, ts in enumerate(timestamps or []):
            try:
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                low = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                if o is None or h is None or low is None or c is None:
                    continue
                if adjcloses is not None and i < len(adjcloses) and adjcloses[i] is not None:
                    c = adjcloses[i]
                v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
                date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
                out.append(Bar(date=date, open=float(o), close=float(c),
                               high=float(h), low=float(low), volume=float(v)))
            except Exception:
                continue
        if days > 0 and len(out) > days:
            out = out[-days:]
        return out
