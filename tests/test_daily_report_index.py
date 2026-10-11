"""daily_report 大盤指數取數測試。

覆蓋:
- CN 市場走 marketdata 新包 index_quotes,產出正確的 IndexData 列表。
- 非 CN 市場返回空 list（與舊 _get_cn_index 口徑一致），且不呼叫 md。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.modules.automation import daily_report
from src.platform.marketdata.models import IndexData, MarketCode
from src.platform.scheduling import trading_calendar as tc


def _fake_index_items() -> list[dict]:
    return [
        {
            "symbol": "000001",
            "name": "上證指數",
            "current_price": 3123.45,
            "change_pct": 1.23,
            "change_amount": 12.3,
            "prev_close": 3111.15,
            "volume": 100000.0,
            "turnover": 999999999.0,
        },
        {
            "symbol": "399001",
            "name": "深證成指",
            "current_price": 10234.5,
            "change_pct": -0.5,
            "change_amount": -51.2,
            "prev_close": 10285.7,
            "volume": 200000.0,
            "turnover": 888888888.0,
        },
    ]


class _FakeMarketData:
    def __init__(self, items: list[dict]):
        self.items = items
        self.calls: list[list[str]] = []

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        self.calls.append(list(tencent_symbols))
        return self.items


def test_uses_marketdata_index_quotes(monkeypatch):
    """CN 指數走 md.index_quotes,產出正確的 IndexData 列表。"""
    fake_md = _FakeMarketData(_fake_index_items())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.CN))

    assert fake_md.calls == [["sh000001", "sz399001", "sz399006"]]
    assert len(indices) == 2
    assert all(isinstance(i, IndexData) for i in indices)
    assert indices[0].symbol == "000001"
    assert indices[0].name == "上證指數"
    assert indices[0].market == MarketCode.CN
    assert indices[0].current_price == 3123.45
    assert indices[0].change_pct == 1.23
    assert indices[0].change_amount == 12.3
    assert indices[0].volume == 100000.0
    assert indices[0].turnover == 999999999.0


def test_non_cn_market_returns_empty(monkeypatch):
    """非 CN 市場應返回空 list（與舊 _get_cn_index 口徑一致），且不呼叫 md。"""
    fake_md = _FakeMarketData(_fake_index_items())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices_hk = asyncio.run(agent._fetch_index_for_market(MarketCode.HK))
    indices_us = asyncio.run(agent._fetch_index_for_market(MarketCode.US))

    assert indices_hk == []
    assert indices_us == []
    assert fake_md.calls == []


def _prompt_context_for_quotes(quotes):
    watchlist = [
        SimpleNamespace(symbol=quote.symbol, market=MarketCode.TW, name=quote.name)
        for quote in quotes
    ]
    packs = {
        quote.symbol: SimpleNamespace(
            quote=quote, technical={"error": "unavailable"}, capital_flow={},
            news=None, events=None, position=None,
        )
        for quote in quotes
    }
    portfolio = SimpleNamespace(
        accounts=[], total_available_funds=0, total_cost=0,
        get_aggregated_position=lambda _symbol: None,
    )
    context = SimpleNamespace(watchlist=watchlist, portfolio=portfolio)
    data = {
        "timestamp": "2026-10-09T18:00:00+08:00",
        "indices": [], "signal_packs": packs, "symbol_contexts": {}, "quality_overview": {},
    }
    return daily_report.DailyReportAgent().build_prompt(data, context)[1]


def test_prompt_distinguishes_report_date_market_calendar_and_each_quote_date(monkeypatch):
    monkeypatch.setattr(tc, "_TW_TRADING_DATES", frozenset({date(2026, 10, 8), date(2026, 10, 12)}))
    monkeypatch.setattr(tc, "_TW_RANGE", (date(2026, 10, 1), date(2026, 10, 31)))
    stale_live = SimpleNamespace(
        symbol="TWSE:2330", name="台積電", market=MarketCode.TW, current_price=100.0,
        change_pct=1.0, high_price=101.0, low_price=99.0, reference_price=99.0,
        prev_close=99.0, turnover=1000.0, trade_date="2026-10-08", price_kind="live", change_basis=None,
        freshness={"status": "stale"}, availability="available", usable_for_trading=False,
        timestamp=datetime.fromisoformat("2026-10-08T13:30:00+08:00"),
    )
    eod_without_observation_time = SimpleNamespace(
        symbol="TPEX:5347", name="世界", market=MarketCode.TW, current_price=50.0,
        change_pct=0.5, high_price=51.0, low_price=49.0, reference_price=49.0,
        prev_close=49.0, turnover=500.0, trade_date="2026-10-07", price_kind="eod", change_basis=None,
        freshness={"status": "closed"}, availability="available", usable_for_trading=False,
        timestamp=None,
    )

    prompt = _prompt_context_for_quotes([stale_live, eod_without_observation_time])

    assert "報告產製日：2026-10-09（Asia/Taipei）" in prompt
    assert "TW：2026-10-09 休市；時區 Asia/Taipei；下一交易日 2026-10-12" in prompt
    assert "行情日期 2026-10-08；價格種類 live（過期盤中報價）" in prompt
    assert "不可交易 false" in prompt
    assert "來源觀察時間 2026-10-08T13:30:00+08:00" in prompt
    assert "行情日期 2026-10-07；價格種類 eod（收盤行情）" in prompt
    assert "來源觀察時間未知" in prompt
    assert "來源觀察時間 2026-10-09" not in prompt


def test_prompt_marks_calendar_unknown_and_does_not_guess_next_open(monkeypatch):
    monkeypatch.setattr(tc, "_TW_TRADING_DATES", None)
    monkeypatch.setattr(tc, "_TW_RANGE", None)
    quote = SimpleNamespace(
        symbol="TWSE:2330", name="台積電", market=MarketCode.TW, current_price=None,
        change_pct=None, high_price=None, low_price=None, reference_price=None,
        prev_close=None, turnover=None, trade_date=None, price_kind=None, change_basis=None,
        freshness={}, availability=None, usable_for_trading=None, timestamp=None,
    )
    prompt = _prompt_context_for_quotes([quote])

    assert "TW：2026-10-09 日曆未知；時區 Asia/Taipei；下次開盤待確認" in prompt


def test_legacy_watch_action_label_is_normalized_to_next_open_wording():
    watchlist = [SimpleNamespace(symbol="TWSE:2330", market=MarketCode.TW)]
    parsed = daily_report.DailyReportAgent()._parse_suggestions_json({
        "suggestions": [{
            "symbol": "TWSE:2330", "action": "watch", "action_label": "明日關注",
        }],
    }, watchlist)

    assert parsed["TWSE:2330"]["action"] == "watch"
    assert parsed["TWSE:2330"]["action_label"] == "下次開盤關注"


def test_report_prompt_and_saved_context_share_taipei_date_across_utc_midnight(monkeypatch):
    saved = []
    monkeypatch.setattr(daily_report, "save_agent_context_run", lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr(daily_report, "save_analysis", lambda **kwargs: None)
    portfolio = SimpleNamespace(accounts=[], total_available_funds=0, total_cost=0)
    context = SimpleNamespace(
        watchlist=[], portfolio=portfolio, model_label=None,
        ai_client=SimpleNamespace(chat=AsyncMock(return_value="研究報告")),
    )
    data = {"timestamp": "2026-10-09T17:00:00Z", "indices": [], "signal_packs": {}}

    asyncio.run(daily_report.DailyReportAgent().analyze(context, data))

    assert "報告產製日：2026-10-10" in context.ai_client.chat.call_args.args[1]
    assert saved[0]["analysis_date"] == "2026-10-10"
