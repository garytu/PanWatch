"""發現(東財熱門榜)取數路由測試:統一走 marketdata 包"""
import asyncio

import src.platform.marketdata.collectors.discovery_collector as dc


def test_fetch_hot_stocks_uses_marketdata(monkeypatch):
    """fetch_hot_stocks 走 marketdata 包的 hot_stocks,轉換為本模組 HotStock,且透傳 proxy。"""
    from marketdata.types import HotStock as MdHotStock

    captured: dict = {}

    class _MD:
        def hot_stocks(self, *, market="CN", mode="turnover", limit=20, proxy=None):
            captured["market"] = market
            captured["mode"] = mode
            captured["limit"] = limit
            captured["proxy"] = proxy
            return [
                MdHotStock(
                    symbol="600519",
                    market="CN",
                    name="貴州茅臺",
                    price=1700.0,
                    change_pct=1.23,
                    turnover=999999.0,
                    volume=1000.0,
                )
            ]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.EastMoneyDiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_hot_stocks(market="CN", mode="turnover", limit=20))

    assert captured["proxy"] == "http://market-scan-proxy:1080"
    assert captured["market"] == "CN"
    assert captured["mode"] == "turnover"
    assert captured["limit"] == 20

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotStock)
    assert item.symbol == "600519"
    assert item.market == "CN"
    assert item.name == "貴州茅臺"
    assert item.price == 1700.0
    assert item.change_pct == 1.23
    assert item.turnover == 999999.0
    assert item.volume == 1000.0


def test_fetch_hot_boards_uses_marketdata(monkeypatch):
    """fetch_hot_boards 走 marketdata 包的 hot_boards,轉換為本模組 HotBoard,且透傳 proxy。"""
    from marketdata.types import HotBoard as MdHotBoard

    captured: dict = {}

    class _MD:
        def hot_boards(self, *, market="CN", mode="gainers", limit=12, proxy=None):
            captured["market"] = market
            captured["mode"] = mode
            captured["limit"] = limit
            captured["proxy"] = proxy
            return [
                MdHotBoard(
                    code="BK0500",
                    name="白酒",
                    change_pct=2.5,
                    change_amount=1.1,
                    turnover=88888.0,
                )
            ]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.EastMoneyDiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_hot_boards(market="CN", mode="gainers", limit=12))

    assert captured["proxy"] == "http://market-scan-proxy:1080"
    assert captured["market"] == "CN"
    assert captured["mode"] == "gainers"
    assert captured["limit"] == 12

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotBoard)
    assert item.code == "BK0500"
    assert item.name == "白酒"
    assert item.change_pct == 2.5
    assert item.change_amount == 1.1
    assert item.turnover == 88888.0


def test_fetch_board_stocks_uses_marketdata(monkeypatch):
    """fetch_board_stocks 走 marketdata 包的 board_stocks,轉換為本模組 HotStock,且透傳 proxy。"""
    from marketdata.types import HotStock as MdHotStock

    captured: dict = {}

    class _MD:
        def board_stocks(self, *, board_code, mode="gainers", limit=20, proxy=None):
            captured["board_code"] = board_code
            captured["mode"] = mode
            captured["limit"] = limit
            captured["proxy"] = proxy
            return [
                MdHotStock(
                    symbol="000858",
                    market="CN",
                    name="五糧液",
                    price=150.0,
                    change_pct=3.3,
                    turnover=55555.0,
                    volume=200.0,
                )
            ]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.EastMoneyDiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_board_stocks(board_code="BK0500", mode="gainers", limit=20))

    assert captured["proxy"] == "http://market-scan-proxy:1080"
    assert captured["board_code"] == "BK0500"
    assert captured["mode"] == "gainers"
    assert captured["limit"] == 20

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotStock)
    assert item.symbol == "000858"
    assert item.market == "CN"
    assert item.name == "五糧液"
    assert item.price == 150.0
    assert item.change_pct == 3.3
    assert item.turnover == 55555.0
    assert item.volume == 200.0


def test_taiwan_empty_live_discovery_does_not_fall_back_to_local_snapshot(monkeypatch):
    from src.modules.market.api import discovery

    class _Collector:
        async def fetch_hot_stocks(self, **_kwargs):
            return []

    monkeypatch.setattr(
        discovery,
        "_latest_snapshot_stocks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("TW snapshot fallback used")),
    )
    result = asyncio.run(discovery._hot_stocks_live_or_snapshot(
        collector=_Collector(), db=None, market="TW", mode="turnover", limit=20,
    ))
    assert result == []


def test_taiwan_official_screen_route_is_opt_in_and_returns_result_envelope(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.modules.market.api import discovery
    from src.modules.research.taiwan_discovery import TaiwanDiscoveryService

    calls = []
    payload = {
        "market": "TW",
        "provider": "twmd_official",
        "selectors": {"daily_start_date": "2026-09-07", "daily_end_date": "2026-10-06"},
        "conditions": {"pe_max": {"label": "本益比上限", "threshold": "20"}},
        "scope": {"partial_scan": False, "request_counts": {"total": 2}},
        "matches": [],
        "excluded": [],
    }
    monkeypatch.setattr(
        TaiwanDiscoveryService,
        "collect",
        lambda self, conditions, *, limit: (calls.append((conditions, limit)) or payload),
    )
    app = FastAPI()
    app.include_router(discovery.router, prefix="/api/discovery")
    with TestClient(app) as client:
        empty = client.post("/api/discovery/stocks/screen", json={})
        response = client.post("/api/discovery/stocks/screen", json={"pe_max": 20})
        extra = client.post("/api/discovery/stocks/screen", json={"pe_max": 20, "source": "other"})
        boolean = client.post("/api/discovery/stocks/screen", json={"pe_max": True})
        fractional = client.post("/api/discovery/stocks/screen", json={"institutional_net_min_shares": 1.5})

    assert empty.status_code == 400
    assert response.status_code == 200
    assert response.json() == payload
    assert extra.status_code == 422
    assert boolean.status_code == 422
    assert fractional.status_code == 422
    assert calls == [({"pe_max": 20.0}, 20)]
