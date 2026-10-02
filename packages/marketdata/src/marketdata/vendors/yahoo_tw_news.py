"""Yahoo 股市官方個股 RSS，供個人、非商業用途。

保留 RSS 原始標題、摘要及 Yahoo 原文連結；使用時應遵守
https://tw.stock.yahoo.com/rss-help 的來源標示要求。
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from marketdata.cache import TTLCache
from marketdata.http import market_get, record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import NewsArticle
from marketdata.vendors.base import NewsVendor


_RSS_BASE_URL = "https://tw.stock.yahoo.com/rss/s"
_TAIPEI = ZoneInfo("Asia/Taipei")
_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PanWatchRSSReader/1.0)",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}


def _published_at(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TAIPEI)
    return parsed.astimezone(timezone.utc)


class YahooTwNewsVendor(NewsVendor):
    name = "yahoo_tw"
    supports_markets = {"TW"}

    def __init__(self) -> None:
        self._rss_cache = TTLCache(default_ttl_sec=300)

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        taiwan = [symbol for symbol in symbols if symbol.market == Market.TW]
        if not taiwan:
            return []

        by_code: dict[str, list[str]] = {}
        for symbol in taiwan:
            identities = by_code.setdefault(symbol.code, [])
            if symbol.identity not in identities:
                identities.append(symbol.identity)

        articles: dict[str, NewsArticle] = {}
        for code, identities in by_code.items():
            xml = self._rss_cache.get(code)
            if xml is None:
                xml = market_get(
                    f"{_RSS_BASE_URL}/{code}",
                    host_key="tw.stock.yahoo.com",
                    headers=_RSS_HEADERS,
                    timeout=float(config.get("timeout_sec", 8)),
                    min_interval_s=1.0,
                    retries=1,
                    log_label="Yahoo 股市個股 RSS",
                )
                if xml:
                    self._rss_cache.set(code, xml)
            if not xml:
                continue
            try:
                root = ElementTree.fromstring(xml)
            except ElementTree.ParseError as exc:
                record_error(f"Yahoo 股市 RSS {code} XML 解析失敗: {exc}")
                continue

            for item in root.findall("./channel/item"):
                title = (item.findtext("title") or "").strip()
                description = (item.findtext("description") or "").strip()
                link = (item.findtext("link") or "").strip()
                published = _published_at(item.findtext("pubDate") or "")
                if not title or not link or not published:
                    continue
                external_id = (item.findtext("guid") or link).strip()
                existing = articles.get(external_id)
                if existing:
                    existing.symbols.extend(identity for identity in identities
                                            if identity not in existing.symbols)
                    continue
                articles[external_id] = NewsArticle(
                    source=self.name,
                    external_id=external_id,
                    title=title,
                    content=description,
                    publish_time=published,
                    symbols=identities.copy(),
                    url=link,
                )
        return list(articles.values())
