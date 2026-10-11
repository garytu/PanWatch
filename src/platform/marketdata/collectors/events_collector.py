import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


@dataclass
class EventItem:
    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


# 東財公告全文(純文本)content API:art_code -> data.notice_content。
# 走系統代理(trust_env=True,env HTTP_PROXY);東財證書鏈偶發問題,verify=False。
ANN_CONTENT_API_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"


def fetch_announcement_fulltext(
    art_code: str,
    *,
    timeout_s: float = 8.0,
    proxy: str | None = None,
) -> str:
    """按 art_code 取東方財富公告全文(純文本)。

    成功返回 ``data.notice_content`` 去空白後的純文本;任何失敗(網路/解析/空)
    返回空串 —— 呼叫方據此 fail-soft 只保留標題。

    Args:
        art_code: 公告唯一編號(EventItem.external_id)
        timeout_s: 請求超時
        proxy: 顯式代理(預設不走 env 代理)
    """
    if not art_code:
        return ""
    params = {
        "art_code": str(art_code),
        "client_source": "web",
        "page_index": 1,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0))
        with httpx.Client(
            timeout=timeout,
            verify=False,
            headers=headers,
            follow_redirects=True,
            trust_env=True,  # 走系統代理(env HTTP_PROXY,由 apply_proxy_env 統一設)
            proxy=proxy,
        ) as client:
            resp = client.get(ANN_CONTENT_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json() or {}
        content = ((data.get("data") or {}).get("notice_content")) or ""
        return str(content).strip()
    except Exception as e:
        logger.debug(f"公告全文獲取失敗 art_code={art_code}: {type(e).__name__}: {e!r}")
        return ""


def get_market_data():
    """惰性匯入,避免模組載入時的迴圈依賴(便於測試 monkeypatch)。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g
    return _g()


class EastMoneyEventsCollector:
    """A-share event collector based on EastMoney notices.

    Uses the same notices endpoint as news collector, but returns structured event types.
    """

    source = "eastmoney"

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        connect_timeout_s: float | None = None,
        verify_ssl: bool = False,
        proxy: str | None = None,
        retries: int = 1,
        backoff_s: float = 0.6,
    ):
        # timeout_s/connect_timeout_s/verify_ssl/proxy/retries/backoff_s 僅為相容舊呼叫方簽名保留
        # (DataSource 配置、EventsCollector.COLLECTOR_MAP、EastmoneyEventsProvider 仍按這些引數構造例項);
        # 取數已改走 marketdata 包,這些引數當前不再被內部邏輯使用。
        self.last_error: str | None = None

    async def fetch_events(
        self,
        symbols: list[str] | None = None,
        *,
        since: datetime | None = None,
        page_size: int = 50,
    ) -> list[EventItem]:
        import asyncio as _asyncio

        symbols_list = list(symbols or [])
        if not symbols_list:
            return []

        # since 語義:md.events 按 since_days 天窗過濾,本方法按 since 精確 datetime 過濾。
        # 用 since 反推一個足夠寬鬆的 since_days,取回資料後再用原 since 精確重過濾。
        if since is not None:
            delta_days = (datetime.now() - since).days
            since_days = max(1, delta_days + 1)
        else:
            # since=None 時不按時間過濾;用足夠大的視窗近似同等效果
            # (實際結果仍受上游 API page_size 條數限制,不會引入額外老舊資料)。
            since_days = 3650

        md_items = await _asyncio.to_thread(
            get_market_data().events,
            symbols_list,
            market="CN",
            since_days=since_days,
        )

        result: list[EventItem] = []
        for it in md_items:
            if since and it.publish_time < since:
                continue
            result.append(
                EventItem(
                    source=it.source,
                    external_id=it.external_id,
                    event_type=it.event_type,
                    title=it.title,
                    publish_time=it.publish_time,
                    symbols=it.symbols,
                    importance=it.importance,
                    url=it.url,
                )
            )

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


class EventsCollector:
    """Aggregate events collectors."""

    COLLECTOR_MAP = {
        "eastmoney": lambda config: EastMoneyEventsCollector(
            timeout_s=(config or {}).get("timeout_s", 10.0),
            connect_timeout_s=(config or {}).get("connect_timeout_s"),
            verify_ssl=(config or {}).get("verify_ssl", False),
            proxy=(config or {}).get("proxy"),
            retries=(config or {}).get("retries", 1),
            backoff_s=(config or {}).get("backoff_s", 0.6),
        ),
    }

    def __init__(self, collectors: list[EastMoneyEventsCollector] | None = None):
        self.collectors = collectors or [EastMoneyEventsCollector()]

    @classmethod
    def from_database(cls) -> "EventsCollector":
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import DataSource

        collectors = []
        db = SessionLocal()
        try:
            data_sources = (
                db.query(DataSource)
                .filter(DataSource.type == "events", DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
            for ds in data_sources:
                factory = cls.COLLECTOR_MAP.get(ds.provider)
                if not factory:
                    continue
                try:
                    collectors.append(factory(ds.config or {}))
                except Exception:
                    pass
        finally:
            db.close()

        if not collectors:
            collectors = [EastMoneyEventsCollector()]
        return cls(collectors=collectors)

    async def fetch_all(
        self,
        *,
        symbols: list[str] | None = None,
        since_days: int = 7,
    ) -> list[EventItem]:
        import asyncio

        since = datetime.now() - timedelta(days=max(int(since_days), 1))

        async def fetch_one(c) -> list[EventItem]:
            try:
                return await c.fetch_events(symbols=symbols, since=since)
            except Exception as e:
                logger.warning(f"Events collector failed: {e}")
                return []

        results = await asyncio.gather(*[fetch_one(c) for c in self.collectors])
        all_items: list[EventItem] = []
        for items in results:
            all_items.extend(items)

        all_items.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)
        seen: set[tuple[str, str]] = set()
        uniq: list[EventItem] = []
        for it in all_items:
            key = (it.source, it.external_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(it)
        return uniq
