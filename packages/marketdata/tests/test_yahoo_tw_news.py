"""Yahoo 台股 RSS 契約：保留原文、正確標記台股、遇到錯誤安全降級。"""

from datetime import datetime, timezone

from marketdata.symbol import Symbol
from marketdata.vendors import yahoo_tw_news


RSS_2330 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>台積電營收創高（中央社）</title>
    <description>台積電公布最新營收。</description>
    <link>https://tw.stock.yahoo.com/news/tsmc.html</link>
    <guid>yahoo-tsmc-1</guid>
    <pubDate>Fri, 02 Oct 2026 09:30:00 +0800</pubDate>
  </item>
  <item>
    <title>半導體產業新聞</title>
    <description>供應鏈近況。</description>
    <link>https://tw.stock.yahoo.com/news/semiconductor.html</link>
    <pubDate>Fri, 02 Oct 2026 11:30:00 +0800</pubDate>
  </item>
</channel></rss>"""

RSS_2303 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>2303.TW 聯電法說（Yahoo 股市）</title>
    <description>聯電公布展望。</description>
    <link>https://tw.stock.yahoo.com/news/umc.html</link>
    <pubDate>Fri, 02 Oct 2026 10:30:00 +0800</pubDate>
  </item>
  <item>
    <title>半導體產業新聞</title>
    <description>供應鏈近況。</description>
    <link>https://tw.stock.yahoo.com/news/semiconductor.html</link>
    <pubDate>Fri, 02 Oct 2026 11:30:00 +0800</pubDate>
  </item>
</channel></rss>"""


def test_yahoo_rss_maps_per_stock_articles_and_deduplicates(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return RSS_2330 if url.endswith("/2330") else RSS_2303

    monkeypatch.setattr(yahoo_tw_news, "market_get", fake_get)
    symbols = [Symbol.parse("TWSE:2330"), Symbol.parse("TWSE:2303")]
    vendor = yahoo_tw_news.YahooTwNewsVendor()
    rows = vendor.fetch(symbols, {
        "symbol_names": {"TWSE:2330": "台積電", "TWSE:2303": "聯電"},
    })
    assert vendor.fetch(symbols, {}) == rows

    assert [url for url, _ in calls] == [
        "https://tw.stock.yahoo.com/rss/s/2330",
        "https://tw.stock.yahoo.com/rss/s/2303",
    ]
    assert all("User-Agent" in kwargs["headers"] for _, kwargs in calls)
    assert [row.title for row in rows] == [
        "台積電營收創高（中央社）", "半導體產業新聞", "2303.TW 聯電法說（Yahoo 股市）",
    ]
    assert [row.symbols for row in rows] == [
        ["TWSE:2330"], ["TWSE:2330", "TWSE:2303"], ["TWSE:2303"],
    ]
    assert rows[0].content == "台積電公布最新營收。"
    assert rows[0].source == "yahoo_tw"
    assert rows[0].external_id == "yahoo-tsmc-1"
    assert rows[0].url == "https://tw.stock.yahoo.com/news/tsmc.html"
    assert rows[0].publish_time == datetime(2026, 10, 2, 1, 30, tzinfo=timezone.utc)


def test_yahoo_rss_rejects_malformed_xml_and_non_taiwan_symbols(monkeypatch):
    monkeypatch.setattr(yahoo_tw_news, "market_get", lambda *args, **kwargs: "<html>")
    vendor = yahoo_tw_news.YahooTwNewsVendor()
    assert vendor.fetch([Symbol.parse("TWSE:2330")], {}) == []
    assert vendor.fetch([Symbol.parse("AAPL")], {}) == []
