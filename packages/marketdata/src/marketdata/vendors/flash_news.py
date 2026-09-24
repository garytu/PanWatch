"""快訊(7×24)vendor:cls(財聯社)/ sina(新浪直播)/ eastmoney(東財快訊),均市場級、單源。

三家均為未公開檔案的私有介面,欄位以瀏覽器抓包常見鍵名為準,未檔案化欄位
(cls 的 level/stock_list、sina 的 ext.stocks)一律用防禦性 .get() + 缺失填預設,
避免上游改欄位導致硬失敗。**待實抓校準**(沙箱代理攔截,無法驗證真實回應結構)。

時間統一走 datetime.fromtimestamp(ts, tz=timezone.utc) 或防禦解析字串;
解析失敗一律回退到 EPOCH(1970-01-01 UTC),絕不用無參 datetime.now()/time.time(),
保證離線測試可復現。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import FlashNews
from marketdata.vendors.base import FlashNewsVendor as _FlashNewsVendorBase

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _parse_epoch_seconds(ts) -> datetime:
    """秒級時間戳(int/float/數字字串)→ UTC datetime;解析失敗回退 EPOCH。"""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return _EPOCH


def _parse_datetime_str(s, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime:
    """字串時間(可能是數字戳或格式化串)→ UTC datetime;解析失敗回退 EPOCH。"""
    if s is None:
        return _EPOCH
    text = str(s).strip()
    if not text:
        return _EPOCH
    if text.isdigit():
        return _parse_epoch_seconds(text)
    try:
        return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return _EPOCH


def _extract_symbol_code(entry) -> str:
    """防禦性地從一個"關聯股"元素裡取程式碼:可能是純字串,也可能是各種鍵名的 dict。"""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ("code", "stock_code", "SecurityCode", "secu_code", "symbol", "Code"):
            v = entry.get(key)
            if v:
                return str(v).strip()
    return ""


def _extract_symbols(raw) -> list[str]:
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for entry in raw:
        try:
            code = _extract_symbol_code(entry)
            if code:
                out.append(code)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# cls(財聯社)
# ---------------------------------------------------------------------------

_CLS_URL = "https://www.cls.cn/v1/roll/get_roll_list"
_CLS_HOST = "www.cls.cn"

# cls "level" 欄位的常見取值(A/B/C 或數字)→ importance;未識別一律 0。
_CLS_LEVEL_MAP = {"A": 3, "B": 2, "C": 1}


def _cls_importance(item: dict) -> int:
    level = item.get("level")
    if isinstance(level, bool):
        return 0
    if isinstance(level, (int, float)):
        try:
            return int(level)
        except (TypeError, ValueError):
            return 0
    if isinstance(level, str):
        return _CLS_LEVEL_MAP.get(level.strip().upper(), 0)
    return 0


def _cls_sign(params: dict) -> str:
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


class ClsFlashNewsVendor(_FlashNewsVendorBase):
    name = "cls"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[FlashNews]:
        limit = int((config or {}).get("days") or 50)
        params = {
            "appName": "CailianpressWeb",
            "os": "web",
            "sv": "7.7.5",
            "last_time": "",
            "refresh_type": 1,
            "rn": limit,
        }
        sign = _cls_sign(params)
        headers = {"User-Agent": _UA, "Referer": "https://www.cls.cn/"}

        data = market_get(
            _CLS_URL,
            host_key=_CLS_HOST,
            params={**params, "sign": sign},
            headers=headers,
            parse="json",
            retries=2,
            timeout=8,
            log_label="財聯社快訊",
        )
        if not data:
            return []

        items = ((data or {}).get("data") or {}).get("roll_data") or []
        result: list[FlashNews] = []
        for item in items:
            try:
                brief = item.get("brief") or ""
                title = (item.get("title") or brief or "").strip()
                content = (item.get("content") or brief or "").strip()
                if not title and not content:
                    continue
                publish_time = _parse_epoch_seconds(item.get("ctime"))
                symbols_out = _extract_symbols(item.get("stock_list") or item.get("shares") or [])
                result.append(
                    FlashNews(
                        source="cls",
                        external_id=str(item.get("id", "")),
                        title=title,
                        content=content,
                        publish_time=publish_time,
                        symbols=symbols_out,
                        importance=_cls_importance(item),
                        url=item.get("shareurl", "") or "",
                    )
                )
            except Exception:
                continue
        return result


# ---------------------------------------------------------------------------
# sina(新浪財經直播)
# ---------------------------------------------------------------------------

_SINA_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
_SINA_HOST = "zhibo.sina.com.cn"


def _sina_symbols(item: dict) -> list[str]:
    """sina 的關聯股藏在 ext(JSON 字串)裡的 stocks 欄位,防禦性解析。"""
    ext_raw = item.get("ext")
    if not ext_raw:
        return []
    try:
        ext = json.loads(ext_raw) if isinstance(ext_raw, str) else ext_raw
    except (ValueError, TypeError):
        return []
    if not isinstance(ext, dict):
        return []
    stocks = ext.get("stocks") or []
    out = []
    for s in stocks:
        if isinstance(s, dict):
            code = s.get("symbol") or s.get("code") or ""
            if code:
                out.append(str(code).strip())
        elif isinstance(s, str) and s:
            out.append(s.strip())
    return out


class SinaFlashNewsVendor(_FlashNewsVendorBase):
    name = "sina"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[FlashNews]:
        limit = int((config or {}).get("days") or 50)
        params = {"zhibo_id": 152, "page_size": limit, "dire": "f"}
        headers = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"}

        data = market_get(
            _SINA_URL,
            host_key=_SINA_HOST,
            params=params,
            headers=headers,
            parse="json",
            retries=2,
            timeout=8,
            log_label="新浪快訊",
        )
        if not data:
            return []

        feed_list = (
            ((((data or {}).get("result") or {}).get("data") or {}).get("feed") or {}).get("list") or []
        )
        result: list[FlashNews] = []
        for item in feed_list:
            try:
                content = (item.get("rich_text") or item.get("content") or item.get("text") or "").strip()
                if not content:
                    continue
                publish_time = _parse_datetime_str(item.get("create_time"))
                result.append(
                    FlashNews(
                        source="sina",
                        external_id=str(item.get("id", "")),
                        title=content[:40],
                        content=content,
                        publish_time=publish_time,
                        symbols=_sina_symbols(item),
                        importance=0,
                        url="",
                    )
                )
            except Exception:
                continue
        return result


# ---------------------------------------------------------------------------
# eastmoney(東財快訊)
# ---------------------------------------------------------------------------

_EM_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
_EM_HOST = "np-weblist.eastmoney.com"


class EastmoneyFlashNewsVendor(_FlashNewsVendorBase):
    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[FlashNews]:
        limit = int((config or {}).get("days") or 50)
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": limit,
            # 固定串佔位(非隨機/非時間戳),避免破壞離線測試可復現性。
            "req_trace": "marketdata",
        }
        headers = {"User-Agent": _UA, "Referer": "https://kuaixun.eastmoney.com/"}

        data = market_get(
            _EM_URL,
            host_key=_EM_HOST,
            params=params,
            headers=headers,
            parse="json",
            retries=2,
            timeout=8,
            log_label="東財快訊",
        )
        if not data:
            return []

        items = ((data or {}).get("data") or {}).get("fastNewsList") or []
        result: list[FlashNews] = []
        for item in items:
            try:
                title = (item.get("title") or "").strip()
                content = (item.get("summary") or "").strip()
                if not title and not content:
                    continue
                publish_time = _parse_datetime_str(item.get("showTime"))
                result.append(
                    FlashNews(
                        source="eastmoney",
                        external_id=str(item.get("id", "")),
                        title=title,
                        content=content,
                        publish_time=publish_time,
                        symbols=[],
                        importance=0,
                        url="",
                    )
                )
            except Exception:
                continue
        return result
