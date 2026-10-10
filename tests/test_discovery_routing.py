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
                    price_kind="eod",
                    trade_date="2026-10-08",
                    freshness={"status": "current"},
                    provider="TWSE",
                    adjustment_mode="raw",
                    change_basis="raw_close",
                    units={"currency": "TWD", "volume": "shares"},
                    availability={
                        "status": "available",
                        "observation_coverage": {"acquired_at": "2026-10-08T06:30:01Z"},
                    },
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
    assert item.price_kind == "eod"
    assert item.trade_date == "2026-10-08"
    assert item.freshness == {"status": "current"}
    assert item.provider == "TWSE"
    assert item.adjustment_mode == "raw"
    assert item.change_basis == "raw_close"
    assert item.units == {"currency": "TWD", "volume": "shares"}
    assert item.availability["observation_coverage"]["acquired_at"] == "2026-10-08T06:30:01Z"


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
                    price_kind="eod",
                    trade_date="2026-10-08",
                    freshness={"status": "current"},
                    provider="TWSE",
                    adjustment_mode="raw",
                    change_basis="raw_close",
                    units={"currency": "CNY", "volume": "shares"},
                    availability={"status": "available"},
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
    assert item.price_kind == "eod"
    assert item.trade_date == "2026-10-08"
    assert item.freshness == {"status": "current"}
    assert item.provider == "TWSE"
    assert item.adjustment_mode == "raw"
    assert item.change_basis == "raw_close"
    assert item.units == {"currency": "CNY", "volume": "shares"}
    assert item.availability == {"status": "available"}


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


def test_hot_stocks_api_preserves_provenance_and_leaves_legacy_cache_unknown(monkeypatch):
    from src.modules.market.api import discovery

    availability = {
        "status": "available",
        "latest_observation_coverage": {
            "dataset": "twse_daily_price",
            "partition_key": "2026-10-08",
            "status": "AVAILABLE",
            "acquired_at": "2026-10-08T06:30:01Z",
        },
        "freshness": {"status": "current", "known_sessions_behind": 0},
    }
    item = dc.HotStock(
        symbol="TWSE:2330", market="TW", name="台積電", price=100,
        change_pct=1.5, turnover=1000, volume=10, price_kind="eod",
        trade_date="2026-10-08", freshness=availability["freshness"],
        provider="TWSE", adjustment_mode="raw", change_basis="raw_close",
        units={"currency": "TWD", "volume": "shares"}, availability=availability,
    )

    class _Collector:
        calls = 0

        async def fetch_hot_stocks(self, **_kwargs):
            self.calls += 1
            return [item]

    collector = _Collector()
    monkeypatch.setattr(discovery, "EastMoneyDiscoveryCollector", lambda **_kwargs: collector)
    monkeypatch.setattr(discovery, "_resolve_proxy", lambda: "")
    discovery._cache.clear()

    expected = {
        "symbol": "TWSE:2330", "market": "TW", "name": "台積電", "price": 100,
        "change_pct": 1.5, "turnover": 1000, "volume": 10, "price_kind": "eod",
        "trade_date": "2026-10-08", "freshness": availability["freshness"],
        "provider": "TWSE", "adjustment_mode": "raw", "change_basis": "raw_close",
        "units": {"currency": "TWD", "volume": "shares"}, "availability": availability,
    }
    result = asyncio.run(discovery.get_hot_stocks(market="TW", db=None))
    assert result == [expected]
    assert asyncio.run(discovery.get_hot_stocks(market="TW", db=None)) == [expected]
    assert collector.calls == 1

    legacy = [{
        "symbol": "TWSE:2330", "market": "TW", "name": "舊快取",
        "price": 100, "change_pct": 1.5, "turnover": 1000, "volume": 10,
    }]
    discovery._cache_set("stocks:TW:gainers:20", legacy)
    assert asyncio.run(discovery.get_hot_stocks(market="TW", mode="gainers", db=None)) == legacy
    assert collector.calls == 1
    discovery._cache.clear()

    class _LegacyRow:
        symbol = "TPEX:006201"
        market = "TW"
        name = "舊供應商"
        price = 20
        change_pct = 0
        turnover = 100
        volume = 10

    class _LegacyCollector:
        async def fetch_hot_stocks(self, **_kwargs):
            return [_LegacyRow()]

    monkeypatch.setattr(discovery, "EastMoneyDiscoveryCollector", lambda **_kwargs: _LegacyCollector())
    result = asyncio.run(discovery.get_hot_stocks(market="TW", mode="gainers", db=None))
    assert result[0]["symbol"] == "TPEX:006201"
    assert result[0]["price_kind"] is None
    assert result[0]["trade_date"] is None
    assert result[0]["freshness"] is None
    assert result[0]["provider"] is None
    discovery._cache.clear()


def test_synthetic_boards_keep_each_constituents_provenance(monkeypatch):
    from src.modules.market.api import discovery

    availability = {"status": "available", "observation_coverage": {"acquired_at": "2026-10-08T06:30:01Z"}}
    items = [
        dc.HotStock(
            symbol="TWSE:2330", market="TW", name="台積電", price=100,
            change_pct=5, turnover=300, volume=30, price_kind="eod", trade_date="2026-10-08",
            freshness={"status": "current"}, provider="TWSE", adjustment_mode="raw",
            change_basis="raw_close", units={"volume": "shares"}, availability=availability,
        ),
        dc.HotStock(
            symbol="TPEX:5347", market="TW", name="世界", price=50,
            change_pct=4, turnover=200, volume=20, price_kind="live", trade_date="2026-10-07",
            freshness={"status": "stale"}, provider="tpex-live", adjustment_mode="raw",
            change_basis="live_reference", units={"volume": "shares"},
            availability={"status": "available", "freshness": {"status": "stale"}},
        ),
        dc.HotStock(
            symbol="TPEX:006201", market="TW", name="ETF", price=20,
            change_pct=3, turnover=100, volume=10,
        ),
    ]

    class _Collector:
        async def fetch_hot_boards(self, **_kwargs):
            return []

        async def fetch_hot_stocks(self, **_kwargs):
            return items

    monkeypatch.setattr(discovery, "EastMoneyDiscoveryCollector", lambda **_kwargs: _Collector())
    monkeypatch.setattr(discovery, "_resolve_proxy", lambda: "")
    monkeypatch.setattr(discovery, "_watchlist_symbols", lambda *_args: set())
    discovery._cache.clear()

    boards = asyncio.run(discovery.get_hot_boards(market="TW", mode="gainers", limit=12, db=None))
    gainers = next(board for board in boards if board["code"] == "TW_GAINERS")
    provenance = gainers["constituent_provenance"]
    assert [row["symbol"] for row in provenance] == ["TWSE:2330", "TPEX:5347", "TPEX:006201"]
    assert provenance[0]["trade_date"] == "2026-10-08"
    assert provenance[0]["availability"] == availability
    assert provenance[1]["trade_date"] == "2026-10-07"
    assert provenance[1]["freshness"] == {"status": "stale"}
    assert provenance[2]["trade_date"] is None
    constituents = discovery._stocks_by_synthetic_board(
        code="TW_GAINERS", market="TW", stocks=[{
            "symbol": item.symbol,
            "market": item.market,
            "name": item.name,
            "change_pct": item.change_pct,
            "turnover": item.turnover,
            "trade_date": item.trade_date,
            "price_kind": item.price_kind,
            "freshness": item.freshness,
            "provider": item.provider,
            "adjustment_mode": item.adjustment_mode,
            "change_basis": item.change_basis,
            "units": item.units,
            "availability": item.availability,
        } for item in items], watchlist=set(), limit=20,
    )
    assert [row["symbol"] for row in constituents] == ["TWSE:2330", "TPEX:5347", "TPEX:006201"]
    assert constituents[1]["trade_date"] == "2026-10-07"
    assert constituents[2]["price_kind"] is None
    discovery._cache.clear()


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
