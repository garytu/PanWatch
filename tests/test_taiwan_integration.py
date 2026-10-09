from datetime import datetime, date, timezone, timedelta
from types import SimpleNamespace
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketdata import Quote
from marketdata.symbol import Symbol
from src.platform.marketdata import marketdata_client as mc
from src.platform.marketdata.models import MarketCode
from src.platform.scheduling import trading_calendar as tc
from src.modules.market.api import quotes, klines
from src.platform.marketdata.twmd_control import TwmdControlClient, TwmdControlError
from src.modules.market.price_alert_engine import PriceAlertEngine
from src.modules.paper_trading import paper_trading_engine as pt
from src.modules.strategy.backtest.cost_model import cost_model_for_market, trading_lot


def live_quote(**extra):
    return {"symbol": "TWSE:2330", "market": "TW", "current_price": 100,
            "timestamp": datetime.now(timezone.utc).isoformat(), "price_kind": "live", "availability": "available",
            "usable_for_trading": True, "freshness": {"status": "fresh", "max_price_age_seconds": 30}, **extra}


def test_default_taiwan_routes_all_price_types_to_twmd(monkeypatch):
    monkeypatch.delenv("TW_DATA_PROVIDER", raising=False)
    monkeypatch.setenv("TWMD_BASE_URL", "http://twmd:8000")
    monkeypatch.setenv("TWMD_TIMEOUT_SEC", "12.5")
    cp = mc.DbConfigProvider()
    for kind in ("quote", "kline", "intraday_kline"):
        source = cp.sources_for(kind, "TW")[0]
        assert source.vendor == "twmd" and source.config["base_url"] == "http://twmd:8000"
        assert source.config["timeout_sec"] == 12.5
        assert source.config["profile_timeout_sec"] == 20
    assert cp.sources_for("fundamentals", "TW")[0].vendor == "twmd"
    assert cp.sources_for("capital_flow", "TW")[0].vendor == "twmd"


def test_taiwan_research_provider_selection_is_explicit_without_fallback(monkeypatch):
    monkeypatch.setenv("TW_FUNDAMENTALS_PROVIDER", "finmind")
    monkeypatch.setenv("TW_CAPITAL_FLOW_PROVIDER", "twmd")
    monkeypatch.setenv("TW_MARGIN_PROVIDER", "finmind")
    monkeypatch.setenv("FINMIND_API_TOKEN", "test-finmind-token")
    cp = mc.DbConfigProvider()

    fundamentals = cp.sources_for("fundamentals", "TW")
    flows = cp.sources_for("capital_flow", "TW")
    margin = cp.sources_for("margin", "TW")

    assert [(source.vendor, source.config.get("token")) for source in fundamentals] == [
        ("finmind", "test-finmind-token")
    ]
    assert [(source.vendor, source.config.get("base_url")) for source in flows] == [
        ("twmd", "http://127.0.0.1:8000")
    ]
    assert [(source.vendor, source.config.get("token")) for source in margin] == [
        ("finmind", "test-finmind-token")
    ]


def test_taiwan_margin_provider_defaults_to_twmd_without_env_file(monkeypatch):
    from src.platform.runtime.config import Settings

    monkeypatch.delenv("TW_MARGIN_PROVIDER", raising=False)
    assert Settings(_env_file=None).tw_margin_provider == "twmd"


def test_profile_and_revenue_reads_have_dedicated_twmd_configuration(monkeypatch):
    monkeypatch.setenv("TWMD_TIMEOUT_SEC", "6")
    monkeypatch.setenv("TWMD_PROFILE_TIMEOUT_SEC", "23.5")
    cp = mc.DbConfigProvider()

    profile = cp.sources_for("company_profile", "TW")
    revenue = cp.sources_for("monthly_revenue", "TW")

    assert len(profile) == len(revenue) == 1
    assert profile[0].vendor == revenue[0].vendor == "twmd"
    assert profile[0].config["timeout_sec"] == revenue[0].config["timeout_sec"] == 6
    assert profile[0].config["profile_timeout_sec"] == revenue[0].config["profile_timeout_sec"] == 23.5


def test_default_research_window_uses_one_frozen_taipei_date(monkeypatch):
    from datetime import datetime as real_datetime
    from types import SimpleNamespace

    import marketdata.client as marketdata_client
    from marketdata import MarketData
    from marketdata.defaults import StaticConfigProvider
    from marketdata.engine import Engine

    calls = []

    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            return real_datetime(2026, 10, 7, 0, 30, tzinfo=tz)

    requests = []

    def fake_fetch(self, request, **kwargs):
        requests.append(request)
        return SimpleNamespace(ok=True, data=[])

    monkeypatch.setattr(marketdata_client, "datetime", FixedDateTime)
    monkeypatch.setattr(Engine, "fetch", fake_fetch)
    result = MarketData(StaticConfigProvider({})).fundamentals(
        ["TWSE:2330", "TPEX:5347"], market="TW"
    )

    assert result == []
    assert len(calls) == 1 and calls[0].key == "Asia/Taipei"
    assert len(requests) == 1
    assert requests[0].symbols == ("TWSE:2330", "TPEX:5347")
    assert dict(requests[0].extra) == {
        "start_date": "2026-09-07", "end_date": "2026-10-06",
        "today_taipei": "2026-10-07",
    }


def test_taiwan_settings_use_env_file_and_explicit_environment_override(monkeypatch, tmp_path):
    from src.platform.runtime.config import Settings
    monkeypatch.chdir(tmp_path)
    for name in ("TWMD_BASE_URL", "TWMD_TIMEOUT_SEC", "TWMD_PROFILE_TIMEOUT_SEC", "TWMD_API_TOKEN", "TW_PAPER_LOT_SIZE",
                 "TW_COMMISSION_RATE", "PANWATCH_PORT"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("TWMD_BASE_URL=http://twmd:8000\nTWMD_API_TOKEN=test-token\n"
                                   "TWMD_TIMEOUT_SEC=9\nTWMD_PROFILE_TIMEOUT_SEC=27\nTW_PAPER_LOT_SIZE=1\n"
                                   "TW_COMMISSION_RATE=0.001\nPANWATCH_PORT=8001\n")
    assert mc.twmd_config() == {"base_url": "http://twmd:8000", "token": "test-token",
                                "timeout_sec": 9, "profile_timeout_sec": 27}
    assert trading_lot("TW") == 1 and cost_model_for_market("TW").cfg.commission_rate == .001
    assert Settings().panwatch_port == 8001
    monkeypatch.setenv("TWMD_BASE_URL", "http://override:8000")
    assert mc.twmd_config()["base_url"] == "http://override:8000"


def test_twmd_control_uses_separate_authenticated_endpoint(monkeypatch):
    import httpx
    from src.platform.runtime.config import Settings
    requests = []

    def respond(request):
        requests.append((request.method, str(request.url), request.headers.get("Authorization")))
        return httpx.Response(200, json={"version": "v1", "data": {
            "instrument_ids": ["TWSE:2330"], "count": 1, "limit": 200}})

    transport = httpx.MockTransport(respond)
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    settings = Settings(twmd_control_base_url="http://twmd-control:9200",
                        twmd_control_agent_token="private-test-token")
    client = TwmdControlClient(settings)
    for method in ("GET", "PUT", "DELETE"):
        assert client.subscriptions(method, "TWSE:2330" if method != "GET" else None)["count"] == 1
    assert [row[0] for row in requests] == ["GET", "PUT", "DELETE"]
    assert all(row[1].startswith("http://twmd-control:9200/api/v1/control/quote-subscriptions")
               and row[2] == "Bearer private-test-token" for row in requests)


def test_twmd_control_errors_do_not_expose_token(monkeypatch):
    import httpx
    from src.platform.runtime.config import Settings
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, json={
            "error": {"message": "private-test-token"}})), **kwargs))
    with pytest.raises(TwmdControlError, match="拒絕憑證") as error:
        TwmdControlClient(Settings(twmd_control_agent_token="private-test-token")).subscriptions()
    assert "private-test-token" not in str(error.value)


def test_taiwan_subscription_proxy_validates_cash_instrument(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr("marketdata.vendors.twmd.TwmdClient.instruments", lambda self: [
        {"instrument_id": "TWSE:2330", "is_active": True, "security_type": "EQUITY"},
        {"instrument_id": "TPEX:6488", "is_active": False, "security_type": "OTHER"},
    ])
    calls = []
    monkeypatch.setattr(TwmdControlClient, "subscriptions", lambda self, method, instrument_id:
                        calls.append((method, instrument_id)) or {"instrument_ids": [instrument_id]})
    assert quotes.subscribe_taiwan_quote("TWSE:2330")["instrument_ids"] == ["TWSE:2330"]
    with pytest.raises(HTTPException):
        quotes.subscribe_taiwan_quote("TPEX:6488")
    with pytest.raises(HTTPException):
        quotes.subscribe_taiwan_quote("2330")
    assert quotes.unsubscribe_taiwan_quote("TPEX:6488")["instrument_ids"] == ["TPEX:6488"]
    assert calls == [("PUT", "TWSE:2330"), ("DELETE", "TPEX:6488")]


@pytest.mark.parametrize("raw, canonical", [("6488.TWO", "TPEX:6488"), ("00878.TW", "TWSE:00878"),
                                            ("TPEX:00679B", "TPEX:00679B")])
def test_venue_and_etf_identity_survive_roundtrip(raw, canonical):
    symbol = Symbol.parse(raw)
    assert symbol.market.value == "TW" and symbol.identity == canonical
    assert Symbol.parse(symbol.to_yfinance()).identity == canonical


def test_quote_aliases_and_null_metadata_reach_batch_api(monkeypatch):
    class MD:
        def quotes(self, *args, **kw):
            return [Quote(symbol="TWSE:2330", market="TW", current_price=None, timestamp=None,
                          instrument_id="TWSE:2330", freshness={"status": "closed"}, usable_for_trading=False)]
    monkeypatch.setattr(mc, "get_market_data", MD)
    rows = mc.md_quote_rows(["2330.TW", "TWSE:2330"], "TW")
    assert [row["symbol"] for row in rows] == ["2330.TW", "TWSE:2330"]
    assert [row.symbol for row in mc.md_stock_data(["2330.TW", "TWSE:2330"], "TW")] == ["2330.TW", "TWSE:2330"]
    response = quotes._quote_to_response("2330.TW", MarketCode.TW, rows[0])
    assert response["timestamp"] is None and response["current_price"] is None
    assert response["freshness"]["status"] == "closed" and response["usable_for_trading"] is False


@pytest.mark.parametrize("override", [
    {"price_kind": "eod"}, {"usable_for_trading": False}, {"availability": "unavailable"},
    {"current_price": None}, {"current_price": float("nan")}, {"timestamp": None},
    {"timestamp": datetime.now().isoformat()},
    {"timestamp": (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()},
    {"freshness": {"status": "closed"}},
])
def test_taiwan_trading_requires_unexpired_live_evidence(override):
    assert mc.quote_usable_for_trading(live_quote(), "TW")
    assert not mc.quote_usable_for_trading(live_quote(**override), "TW")


def test_fresh_price_expires_in_consumer_cache():
    now = datetime.now(timezone.utc)
    row = live_quote(timestamp=now.isoformat())
    assert mc.quote_usable_for_trading(row, "TW", now=now)
    assert not mc.quote_usable_for_trading(row, "TW", now=now + timedelta(seconds=31))


def test_alert_cannot_fire_on_historical_quote():
    rule = SimpleNamespace(stock=SimpleNamespace(market="TW", symbol="TWSE:2330"),
                           condition_group={"op": "and", "items": [{"type": "price", "op": ">", "value": 90}]})
    engine = PriceAlertEngine()
    result = asyncio.run(engine.eval_rule(rule, live_quote(price_kind="eod", usable_for_trading=False)))
    assert not result.matched and result.snapshot["error"] == "unusable_quote"
    assert asyncio.run(engine.eval_rule(rule, live_quote())).matched


def test_taiwan_costs_and_lot_quantity(monkeypatch):
    monkeypatch.delenv("TW_PAPER_LOT_SIZE", raising=False)
    monkeypatch.setenv("TW_COMMISSION_RATE", "0.001425")
    monkeypatch.setenv("TW_MIN_COMMISSION", "20")
    cost = cost_model_for_market("TW", "TWSE:2330")
    assert cost.cfg.transfer_fee_rate == 0 and cost.cfg.stamp_duty_rate == .003
    assert cost_model_for_market("TW", "TWSE:00878", security_type="ETF").cfg.stamp_duty_rate == .001
    assert trading_lot("TW") == 1000
    assert cost.fill("buy", 100, 1000).stamp_duty == 0
    quantity = pt._compute_quantity(rank_score=80, market_budget=1_000_000, price=100,
                                   available_cash=200_000, cost_model=cost, lot=trading_lot("TW"))
    assert quantity == 1000
    monkeypatch.setenv("TW_PAPER_LOT_SIZE", "1")
    assert trading_lot("TW") == 1


def test_taiwan_allocation_is_opt_in():
    assert "TW" in pt.ALL_MARKETS
    assert pt.DEFAULT_ALLOCATIONS["TW"] == 0
    assert pt.normalize_allocations({"TW": 1})["TW"] == 1


def test_intraday_api_retains_missing_slots_and_disables_signal(monkeypatch):
    rows = [{"timestamp": "2026-09-29T09:00:00+08:00", "status": "missing", "finalized": False,
             "open": None, "close": None, "high": None, "low": None, "volume": None, "turnover": None}]
    monkeypatch.setattr("marketdata.vendors.twmd.TwmdClient.bars", lambda *args, **kw:
                        {"bars": rows, "coverage_complete": False, "adjustment_mode": "provider_reported"})
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get("/klines/TWSE:2330/intraday?timeframe=5m")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["klines"][0]["close"] is None and payload["klines"][0]["status"] == "missing"
    assert payload["summary"] is None and payload["live_collection"] is False
    assert payload["usable_for_trading"] is False


@pytest.mark.parametrize("query", ["market=CN", "timeframe=1d", "limit=1001", "start_date=2026-09-01",
                                    "start_date=2026-01-01&end_date=2026-09-29"])
def test_intraday_api_rejects_unsupported_or_unbounded_requests(query):
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    assert TestClient(app).get(f"/klines/TWSE:2330/intraday?{query}").status_code in {400, 422}


def _intraday_coverage_read(rows):
    from datetime import date as date_type

    return SimpleNamespace(
        instrument_id="TWSE:2330", session="regular", start_date=date_type(2026, 9, 11),
        end_date=date_type(2026, 10, 10), schema_ready=True, coverage_complete=False,
        coverage=[SimpleNamespace(**row) for row in rows],
        raw={"instrument_id": "TWSE:2330", "session": "regular", "schema_ready": True,
              "coverage_complete": False, "coverage": rows, "served_at": "2026-10-10T14:00:00+08:00"},
    )


def _intraday_coverage_row(day, *, status="available", calendar="observed_open", observed=270,
                          missing=0, pending=0):
    return {"trade_date": date.fromisoformat(day), "status": status,
            "calendar_status": calendar, "expected_minutes": 270,
            "observed_minutes": observed, "missing_minutes": missing, "pending_minutes": pending}


def _intraday_bars_payload(day, timeframe="5m", *, complete=True, priced=True, row_count=None):
    limit = 270 if timeframe == "1m" else 54
    count = limit if row_count is None else row_count
    bars = []
    for index in range(count):
        step = 1 if timeframe == "1m" else 5
        minute = 9 * 60 + index * step
        hh, mm = divmod(minute, 60)
        end_hh, end_mm = divmod(minute + step, 60)
        bars.append({"instrument_id": "TWSE:2330", "trade_date": day, "timeframe": timeframe,
                     "timestamp": f"{day}T{hh:02d}:{mm:02d}:00+08:00",
                     "interval_start": f"{day}T{hh:02d}:{mm:02d}:00+08:00",
                     "interval_end": f"{day}T{end_hh:02d}:{end_mm:02d}:00+08:00",
                     "provider_timestamp": None, "open": "100", "high": "101", "low": "99",
                     "close": "100" if priced else None, "volume": 100 if priced else None,
                     "status": "observed" if priced else "no_trade", "finalized": True})
    return {"instrument_id": "TWSE:2330", "timeframe": timeframe, "price_kind": "intraday",
            "adjustment_mode": "provider_reported", "limit": limit, "returned_count": count,
            "total_count": limit, "truncated": False, "partial": not complete,
            "coverage_complete": complete, "schema_ready": True, "start_date": day, "end_date": day,
            "availability": "available" if complete else "incomplete", "units": {"price": "TWD"},
            "coverage": [{"trade_date": day, "status": "available" if complete else "incomplete",
                          "calendar_status": "observed_open"}], "bars": bars}


@pytest.mark.parametrize(("timeframe", "limit"), [("1m", 270), ("5m", 54)])
def test_intraday_auto_uses_covered_completed_date_and_exact_timeframe_limit(monkeypatch, timeframe, limit):
    from zoneinfo import ZoneInfo
    from marketdata.vendors.twmd import TwmdClient

    tc._TW_TRADING_DATES = frozenset({date(2026, 10, 8)})
    tc._TW_RANGE = (date(2026, 1, 1), date(2026, 12, 31))
    monkeypatch.setattr(klines, "_taipei_now", lambda: datetime(2026, 10, 10, 14, 0, tzinfo=ZoneInfo("Asia/Taipei")))
    read = _intraday_coverage_read([
        _intraday_coverage_row("2026-10-08"),
        _intraday_coverage_row("2026-10-09", status="incomplete", calendar="unknown", observed=269, missing=1),
    ])
    coverage_calls, bar_calls = [], []

    def coverage(self, symbol, start, end, *, timeout_sec=None):
        coverage_calls.append((symbol.identity, start, end, timeout_sec))
        return read

    def get_response(self, path, **kwargs):
        bar_calls.append((path, kwargs))
        return _intraday_bars_payload("2026-10-08", timeframe), {}

    monkeypatch.setattr(TwmdClient, "bars_coverage", coverage)
    monkeypatch.setattr(TwmdClient, "get_response", get_response)
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get(
        f"/klines/TWSE:2330/intraday?date_mode=auto&timeframe={timeframe}&limit={limit}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["date_selection"]["selected_date"] == "2026-10-08"
    assert payload["date_selection"]["coverage_status"] == "available"
    assert payload["date_selection"]["display_coverage_complete"] is True
    assert payload["selection_coverage"]["coverage"][1]["calendar_status"] == "unknown"
    assert len(coverage_calls) == 1
    assert coverage_calls[0][1:3] == (date(2026, 9, 11), date(2026, 10, 10))
    assert 0 < coverage_calls[0][3] <= 3.0
    assert len(bar_calls) == 1
    assert bar_calls[0][1]["retries"] == 0
    assert bar_calls[0][1]["limit"] == limit
    assert bar_calls[0][1]["start_date"] == bar_calls[0][1]["end_date"] == "2026-10-08"


def test_intraday_auto_can_select_incomplete_but_drawable_completed_session(monkeypatch):
    from zoneinfo import ZoneInfo
    from marketdata.vendors.twmd import TwmdClient

    tc._TW_TRADING_DATES = frozenset({date(2026, 10, 8), date(2026, 10, 9)})
    tc._TW_RANGE = (date(2026, 1, 1), date(2026, 12, 31))
    monkeypatch.setattr(klines, "_taipei_now", lambda: datetime(2026, 10, 10, 14, 0, tzinfo=ZoneInfo("Asia/Taipei")))
    read = _intraday_coverage_read([
        _intraday_coverage_row("2026-10-08"),
        _intraday_coverage_row("2026-10-09", status="incomplete", observed=269, missing=1),
    ])
    calls = []
    monkeypatch.setattr(TwmdClient, "bars_coverage", lambda *_args, **_kwargs: read)
    monkeypatch.setattr(TwmdClient, "get_response", lambda self, path, **kwargs: (
        calls.append(kwargs) or (_intraday_bars_payload("2026-10-09", complete=False, row_count=53), {})
    ))
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get("/klines/TWSE:2330/intraday?date_mode=auto&timeframe=5m&limit=54")

    assert response.status_code == 200
    payload = response.json()
    assert payload["date_selection"]["selected_date"] == "2026-10-09"
    assert payload["date_selection"]["coverage_status"] == "incomplete"
    assert payload["display_coverage_complete"] is False
    assert len(payload["klines"]) == 53 and payload["coverage_complete"] is False
    assert len(calls) == 1 and calls[0]["start_date"] == "2026-10-09"


def test_intraday_auto_stops_after_three_unpriced_bar_requests(monkeypatch):
    from zoneinfo import ZoneInfo
    from marketdata.vendors.twmd import TwmdClient

    days = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    tc._TW_TRADING_DATES = frozenset(date.fromisoformat(day) for day in days)
    tc._TW_RANGE = (date(2026, 1, 1), date(2026, 12, 31))
    monkeypatch.setattr(klines, "_taipei_now", lambda: datetime(2026, 10, 10, 14, 0, tzinfo=ZoneInfo("Asia/Taipei")))
    read = _intraday_coverage_read([_intraday_coverage_row(day, status="incomplete", observed=269, missing=1)
                                    for day in days])
    bar_dates = []
    monkeypatch.setattr(TwmdClient, "bars_coverage", lambda *_args, **_kwargs: read)

    def get_response(self, path, **kwargs):
        day = kwargs["start_date"]
        bar_dates.append(day)
        return _intraday_bars_payload(day, priced=False, complete=False, row_count=1), {}

    monkeypatch.setattr(TwmdClient, "get_response", get_response)
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get("/klines/TWSE:2330/intraday?date_mode=auto&timeframe=5m&limit=54")

    assert response.status_code == 200
    payload = response.json()
    assert payload["date_selection"]["selected_date"] is None
    assert payload["date_selection"]["reason"] == "request_budget_exhausted"
    assert payload["date_selection"]["bar_request_count"] == 3
    assert bar_dates == ["2026-10-08", "2026-10-07", "2026-10-06"]
    assert payload["coverage_complete"] is False


def test_intraday_manual_holiday_date_is_never_replaced_by_previous_data(monkeypatch):
    from marketdata.vendors.twmd import TwmdClient

    tc._TW_TRADING_DATES = frozenset({date(2026, 10, 8), date(2026, 10, 12)})
    tc._TW_RANGE = (date(2026, 1, 1), date(2026, 12, 31))
    calls = []
    payload = _intraday_bars_payload("2026-10-09", complete=False, priced=False, row_count=1)
    monkeypatch.setattr(TwmdClient, "bars", lambda self, symbol, **kwargs: (calls.append(kwargs) or payload))
    monkeypatch.setattr(TwmdClient, "bars_coverage", lambda *_args, **_kwargs: pytest.fail("manual date queried auto coverage"))
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get(
        "/klines/TWSE:2330/intraday?date_mode=selected&timeframe=5m&start_date=2026-10-09&end_date=2026-10-09&limit=54"
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["date_selection"]["selected_date"] == "2026-10-09"
    assert result["date_selection"]["calendar"]["is_trading_day"] is False
    assert calls == [{"timeframe": "5m", "limit": 54, "start_date": "2026-10-09", "end_date": "2026-10-09"}]
    assert result["klines"][0]["close"] is None


def test_intraday_previous_trading_day_with_missing_bars_keeps_resolved_date(monkeypatch):
    from marketdata.vendors.twmd import TwmdClient

    tc._TW_TRADING_DATES = frozenset({date(2026, 10, 8), date(2026, 10, 12)})
    tc._TW_RANGE = (date(2026, 1, 1), date(2026, 12, 31))
    calls = []
    payload = {"instrument_id": "TWSE:2330", "timeframe": "5m", "start_date": "2026-10-08",
               "end_date": "2026-10-08", "coverage_complete": False, "availability": "unavailable",
               "bars": [], "coverage": [{"trade_date": "2026-10-08", "status": "missing",
                                            "calendar_status": "observed_open"}]}
    monkeypatch.setattr(TwmdClient, "bars", lambda self, symbol, **kwargs: (calls.append(kwargs) or payload))
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get(
        "/klines/TWSE:2330/intraday?date_mode=previous&trade_date=2026-10-12&timeframe=5m"
    )

    assert response.status_code == 200
    result = response.json()
    assert result["date_selection"]["requested_date"] == "2026-10-12"
    assert result["date_selection"]["selected_date"] == "2026-10-08"
    assert result["date_selection"]["coverage_status"] == "missing"
    assert calls[0]["start_date"] == calls[0]["end_date"] == "2026-10-08"
    assert result["klines"] == []


@pytest.mark.parametrize("mutation", [
    lambda payload: payload.update(start_date="not-a-date"),
    lambda payload: payload["bars"][0].update(instrument_id="TPEX:2330"),
    lambda payload: payload["bars"][0].pop("timestamp"),
])
def test_intraday_rejects_malformed_provider_identity_date_or_timestamp(monkeypatch, mutation):
    from marketdata.vendors.twmd import TwmdClient

    payload = _intraday_bars_payload("2026-10-08", timeframe="5m")
    mutation(payload)
    monkeypatch.setattr(TwmdClient, "bars", lambda *_args, **_kwargs: payload)
    app = FastAPI(); app.include_router(klines.router, prefix="/klines")
    response = TestClient(app).get(
        "/klines/TWSE:2330/intraday?date_mode=selected&timeframe=5m&start_date=2026-10-08&end_date=2026-10-08&limit=54"
    )

    assert response.status_code == 502


def test_intraday_auto_uses_taipei_cutoff_for_utc_crossing_dates():
    from zoneinfo import ZoneInfo

    before_cutoff_utc = datetime(2026, 10, 8, 0, 30, tzinfo=ZoneInfo("UTC"))
    after_cutoff_utc = datetime(2026, 10, 8, 5, 31, tzinfo=ZoneInfo("UTC"))
    assert klines._automatic_window(before_cutoff_utc) == (date(2026, 9, 8), date(2026, 10, 7))
    assert klines._automatic_window(after_cutoff_utc) == (date(2026, 9, 9), date(2026, 10, 8))


def test_unknown_taiwan_calendar_does_not_infer_a_weekday_session(monkeypatch):
    tc.reset_cache()
    assert not tc.is_trading_day("TW", date(2026, 9, 29))
    assert tc.calendar_status("TW", date(2026, 9, 29))["status"] == "unknown"
    monkeypatch.setattr(tc, "_TW_TRADING_DATES", frozenset({date(2026, 9, 29)}))
    monkeypatch.setattr(tc, "_TW_RANGE", (date(2026, 1, 1), date(2026, 12, 31)))
    assert tc.is_trading_day("TW", date(2026, 9, 29))
    assert not tc.is_trading_day("TW", date(2026, 9, 28))
    assert not tc.is_trading_day("TW", date(2027, 1, 4))
    monkeypatch.setenv("TW_EXTRA_CLOSED_DATES", "2026-09-29")
    assert not tc.is_trading_day("TW", date(2026, 9, 29))


def test_official_calendar_distinguishes_open_annotations_and_settlement_only(monkeypatch, tmp_path):
    year = datetime.now().year
    payload = {"stat": "ok", "queryYear": year, "data": [
        [f"{year}-01-01", "中華民國開國紀念日", "放假"],
        [f"{year}-01-02", "國曆新年開始交易日", "開始交易"],
        [f"{year}-02-11", "農曆春節前最後交易日", "最後交易"],
        [f"{year}-02-12", "市場無交易，僅辦理結算交割作業", ""],
    ]}
    monkeypatch.setattr(tc, "_TW_CALENDAR_CACHE", tmp_path / "calendar.json")
    monkeypatch.setattr("marketdata.http.market_get", lambda *args, **kw: payload)
    dates = tc._fetch_tw_trading_dates()
    assert date(year, 1, 1) not in dates and date(year, 2, 12) not in dates
    if date(year, 1, 2).weekday() < 5:
        assert date(year, 1, 2) in dates
    monkeypatch.setattr("marketdata.http.market_get", lambda *args, **kw: None)
    assert tc._fetch_tw_trading_dates() == dates
    monkeypatch.setattr("marketdata.http.market_get", lambda *args, **kw:
                        {**payload, "data": [["invalid-date", "休市"]]})
    assert tc._fetch_tw_trading_dates() == dates
    import json
    assert json.loads(tc._TW_CALENDAR_CACHE.read_text())["data"] == payload["data"]


def test_taiwan_paper_entry_and_exit_require_live_prices(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition, StrategySignalRun
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    account = PaperTradingAccount(initial_capital=1_000_000, current_capital=1_000_000,
                                  peak_capital=1_000_000, market_allocations={"TW": 1})
    signal = StrategySignalRun(snapshot_date="2026-09-29", stock_symbol="TWSE:2330", stock_market="TW",
                               strategy_code="trend", action="buy", rank_score=80, entry_low=95, entry_high=105,
                               stop_loss=90, target_price=115, status="active")
    db.add_all([account, signal]); db.commit()
    simulator = pt.PaperTradingEngine()
    quote = live_quote(price_kind="eod", usable_for_trading=False)
    monkeypatch.setattr(simulator, "_fetch_quotes_map", lambda *args: {("TW", "TWSE:2330"): quote})
    monkeypatch.setattr(pt, "_cost_model", lambda *args: cost_model_for_market("TW"))
    try:
        assert simulator._check_entries(db, account)[0] == 0
        quote = live_quote()
        assert simulator._check_entries(db, account)[0] == 1
        position = db.query(PaperTradingPosition).one()
        assert position.quantity == 1000
        quote = live_quote(current_price=80, price_kind="eod", usable_for_trading=False)
        assert simulator._check_exits(db, account)[0] == 0
        assert position.status == "open"
        quote = live_quote(current_price=80)
        assert simulator._check_exits(db, account)[0] == 1
        assert position.status == "closed"
    finally:
        db.close(); db_engine.dispose()


def test_taiwan_quote_routing_stops_with_unknown_calendar(monkeypatch):
    monkeypatch.setattr(pt, "_is_trading_time", lambda *args: False)
    calls = []
    monkeypatch.setattr(pt, "md_quote_rows", lambda *args: calls.append(args))
    assert pt.PaperTradingEngine()._fetch_quotes_map([("TWSE:2330", "TW")]) == {}
    assert calls == []


def test_taiwan_analysis_uses_twmd_despite_cn_source_policy(monkeypatch):
    from src.modules.research.signals import signal_pack as sp
    from src.platform.marketdata.models import StockData
    stock = StockData(symbol="TPEX:6488", name="環球晶", market=MarketCode.TW, current_price=100,
                      change_pct=None, change_amount=None, volume=None, turnover=None, open_price=None,
                      high_price=None, low_price=None, prev_close=None, timestamp=None, price_kind="eod",
                      usable_for_trading=False, provider="TPEX")
    monkeypatch.setattr(sp.SignalPackBuilder, "_source_policy", staticmethod(lambda *a, **k: ([], True)))
    monkeypatch.setattr(sp, "md_stock_data", lambda symbols, market: [stock])
    monkeypatch.setattr(sp.KlineCollector, "get_kline_summary", lambda *a, **k: {"asof": "2026-09-29", "adjustment_mode": "raw"})
    portfolio = SimpleNamespace(get_positions_for_stock=lambda *a: [], get_aggregated_position=lambda *a: None)
    packs = asyncio.run(sp.SignalPackBuilder().build_for_symbols(symbols=[("TPEX:6488", MarketCode.TW, "環球晶")],
                                                                include_news=False, news_hours=2, portfolio=portfolio))
    assert packs["TPEX:6488"].quote is stock
    assert packs["TPEX:6488"].sources["quote"] == "TPEX"
    assert packs["TPEX:6488"].technical["adjustment_mode"] == "raw"


def test_tw_backtest_uses_taiwan_fees_and_lot():
    from src.modules.strategy.backtest.engine import Backtester, Signal
    from src.modules.strategy.backtest.data_adapter import PriceBar
    bars = [PriceBar(date=f"2026-09-{day}", open=100, close=100, high=101, low=99, volume=1000)
            for day in (22, 23, 24)]
    trade = Backtester(cash_per_trade=150000).run_single(Signal("TWSE:2330", "TW", "2026-09-21"), bars)
    assert trade.quantity == 1000
    expected = cost_model_for_market("TW").round_trip_pnl(100, 100, 1000)
    assert trade.pnl == expected["pnl"]


@pytest.mark.parametrize('timeframe,limit', [('1m', 270), ('5m', 54)])
def test_intraday_truncated_payload_never_reports_a_complete_display(monkeypatch, timeframe, limit):
    from marketdata.vendors.twmd import TwmdClient

    payload = _intraday_bars_payload('2026-10-08', timeframe, row_count=1)
    payload.update(truncated=True, returned_count=1)
    monkeypatch.setattr(TwmdClient, 'bars', lambda *_args, **_kwargs: payload)
    app = FastAPI(); app.include_router(klines.router, prefix='/klines')
    response = TestClient(app).get(
        f'/klines/TWSE:2330/intraday?timeframe={timeframe}&limit={limit}'
        '&start_date=2026-10-08&end_date=2026-10-08'
    )
    assert response.status_code == 200
    assert response.json()['coverage_complete'] is True
    assert response.json()['display_coverage_complete'] is False
    assert response.json()['summary'] is None


def test_intraday_unknown_calendar_previous_date_does_not_query_upstream(monkeypatch):
    from marketdata.vendors.twmd import TwmdClient

    tc.reset_cache()
    monkeypatch.setattr(TwmdClient, 'bars', lambda *_args, **_kwargs: pytest.fail('unknown previous date queried bars'))
    app = FastAPI(); app.include_router(klines.router, prefix='/klines')
    response = TestClient(app).get('/klines/TWSE:2330/intraday?date_mode=previous&trade_date=2027-01-04')
    assert response.status_code == 200
    assert response.json()['date_selection']['reason'] == 'calendar_unknown'
    assert response.json()['klines'] == []
