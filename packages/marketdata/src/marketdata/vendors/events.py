"""事件 vendor:東財公告(單源)。移植自 PanWatch EastMoneyEventsCollector.fetch_events 抓取核
(src/collectors/events_collector.py:fetch_events/_parse_item/_guess_event_type/_guess_importance)。

原實現是 async(httpx.AsyncClient);此處改為同步 market_get,URL/params/headers/
A股過濾/解析/型別與重要度啟發式/排序/去重全部照搬。無 vendor 內快取(由 Engine 統一管)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import EventItem
from marketdata.vendors.base import EventsVendor as _EventsVendorBase

_ANN_API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_ANN_HOST = "np-anotice-stock.eastmoney.com"
_SOURCE = "eastmoney"


def _guess_event_type(title: str, column_names: list[str]) -> str:
    t = title
    if any(
        k in t
        for k in [
            "業績預告",
            "業績快報",
            "年報",
            "半年報",
            "季報",
            "三季報",
            "一季報",
        ]
    ):
        return "earnings"
    if any(k in t for k in ["分紅", "派息", "除權", "除息", "送轉", "股權登記"]):
        return "dividend"
    if any(k in t for k in ["停牌", "復牌"]):
        return "suspension"
    if any(k in t for k in ["回購", "股份回購"]):
        return "repurchase"
    if any(k in t for k in ["增發", "配股", "定向增發", "發行"]):
        return "financing"
    if any(k in t for k in ["減持", "增持", "股東", "董監高", "持股變動"]):
        return "insider"
    if any(k in t for k in ["訴訟", "仲裁", "立案", "處罰", "監管", "問詢函"]):
        return "regulatory"
    if any(k in t for k in ["重組", "併購", "收購", "出售資產", "重大資產"]):
        return "restructuring"
    if any(k in column_names for k in ["臨時公告", "重大事項"]):
        return "major"
    return "notice"


def _guess_importance(title: str, column_names: list[str]) -> int:
    t = title
    if any(
        k in t
        for k in [
            "重大",
            "業績預告",
            "業績快報",
            "年報",
            "半年報",
            "重組",
            "停牌",
            "復牌",
        ]
    ):
        return 3
    if any(
        k in t for k in ["季報", "分紅", "回購", "增持", "減持", "問詢函", "處罰"]
    ):
        return 2
    if any("臨時" in k for k in column_names):
        return 1
    return 0


def _parse_item(item: dict, stock_codes: list[str]) -> EventItem | None:
    external_id = str(item.get("art_code", ""))
    title = (item.get("title") or "").strip()
    if not external_id or not title:
        return None

    notice_date = item.get("notice_date", "")
    publish_time = datetime.now()
    try:
        publish_time = datetime.strptime(notice_date, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        try:
            publish_time = datetime.strptime(str(notice_date)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            publish_time = datetime.now()

    columns = item.get("columns", []) or []
    column_names = [str(c.get("column_name") or "") for c in columns]

    event_type = _guess_event_type(title, column_names)
    importance = _guess_importance(title, column_names)

    symbol_for_url = stock_codes[0] if stock_codes else ""
    url = (
        f"https://data.eastmoney.com/notices/detail/{symbol_for_url}/{external_id}.html"
        if symbol_for_url
        else ""
    )

    return EventItem(
        source=_SOURCE,
        external_id=external_id,
        event_type=event_type,
        title=title,
        publish_time=publish_time,
        symbols=stock_codes,
        importance=importance,
        url=url,
    )


class EventsVendor(_EventsVendorBase):
    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[EventItem]:
        a_share_symbols = [s.code for s in symbols if len(s.code) == 6 and s.code.isdigit()]
        if not a_share_symbols:
            return []

        since_days = int((config or {}).get("since_days") or 7)
        since = datetime.now() - timedelta(days=max(since_days, 1))
        page_size = int((config or {}).get("page_size") or 50)

        params = {
            "sr": -1,
            "page_size": page_size,
            "page_index": 1,
            "ann_type": "A",
            "stock_list": ",".join(sorted(set(a_share_symbols))),
            "f_node": 0,
            "s_node": 0,
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        data = market_get(
            _ANN_API_URL,
            host_key=_ANN_HOST,
            params=params,
            headers=headers,
            min_interval_s=0.2,
            timeout=10,
            retries=1,
            parse="json",
            verify=False,  # 對齊原 EastMoneyEventsCollector 的 verify_ssl=False(東財 ann 端點 SSL 關閉)
            log_label="事件",
        )
        if not data or not data.get("success"):
            return []

        items = data.get("data", {}).get("list", []) or []
        result: list[EventItem] = []

        for item in items:
            try:
                codes = item.get("codes", []) or []
                stock_codes = [
                    c.get("stock_code", "") for c in codes if c.get("stock_code")
                ]
                if not stock_codes:
                    stock_codes = a_share_symbols[:1]

                ev = _parse_item(item, stock_codes)
                if not ev:
                    continue
                if ev.publish_time < since:
                    continue
                result.append(ev)
            except Exception:
                continue

        result.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)

        seen: set[tuple[str, str]] = set()
        uniq: list[EventItem] = []
        for ev in result:
            key = (ev.source, ev.external_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(ev)

        return uniq
