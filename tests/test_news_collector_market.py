"""個股新聞依市場分流，台股代碼不可誤送 A 股源。"""

import asyncio
from datetime import datetime, timezone

from src.platform.marketdata.collectors import news_collector
from src.platform.marketdata import models


def test_news_collector_routes_taiwan_symbols(monkeypatch):
    monkeypatch.setattr(models, "ENABLED_MARKETS", (models.MarketCode.TW,))
    calls = []

    def fake_news(symbols, since_hours, names, *, market):
        calls.append((symbols, market, names))
        return [news_collector.NewsItem(
            source="yahoo_tw", external_id="1", title="台積電消息", content="",
            publish_time=datetime(2026, 10, 2, tzinfo=timezone.utc),
            symbols=symbols,
        )]

    monkeypatch.setattr(
        "src.platform.marketdata.marketdata_client.md_news", fake_news,
    )
    result = asyncio.run(news_collector.NewsCollector().fetch_all(
        symbols=["600519", "TWSE:2330"], since_hours=24,
        symbol_names={"TWSE:2330": "台積電"},
    ))

    assert calls == [(["TWSE:2330"], "TW", {"TWSE:2330": "台積電"})]
    assert result[0].symbols == ["TWSE:2330"]
