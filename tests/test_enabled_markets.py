"""台股部署策略：停用市場不觸發取數、交易或排程，保留其支援定義。"""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.marketdata import models


@pytest.fixture(autouse=True)
def taiwan_only(_supported_markets_for_regression, monkeypatch):
    monkeypatch.setattr(models, 'ENABLED_MARKETS', (models.MarketCode.TW,))


def test_disabled_market_never_opens_even_during_session():
    dt = datetime.fromisoformat('2026-09-29T10:00:00+08:00')
    assert not models.MARKETS[models.MarketCode.CN].is_trading_time(dt)
    assert set(models.MARKETS) == set(models.MarketCode)


def test_quotes_do_not_contact_disabled_providers(monkeypatch):
    from src.platform.marketdata import marketdata_client as client
    provider = Mock()
    monkeypatch.setattr(client, 'get_market_data', provider)
    assert client.md_quote_rows(['AAPL'], 'US') == []
    provider.assert_not_called()


def test_indices_do_not_contact_other_markets(monkeypatch):
    from src.modules.market.api import market
    from src.platform.marketdata import marketdata_client
    provider = Mock()
    market.clear_indices_cache()
    monkeypatch.setattr(market, 'get_market_data', lambda: provider)
    monkeypatch.setattr(marketdata_client, 'twmd_config', lambda: {'base_url': 'http://fixture', 'token': 'test'})
    rows = asyncio.run(market.get_market_indices())
    assert [item['symbol'] for item in rows] == ['TAIEX', 'TPEX']
    provider.index_quotes.assert_not_called()
    assert [call.args[0] for call in provider.benchmark_bars.call_args_list] == ['TAIEX', 'TPEX']


def test_paper_allocation_is_masked_without_moving_or_mutating_money():
    from src.modules.paper_trading.paper_trading_engine import market_allocations_or_default
    raw = {'CN': 0.5, 'HK': 0.2, 'US': 0.1, 'TW': 0.2}
    account = SimpleNamespace(market_allocations=raw)
    assert market_allocations_or_default(account) == {'CN': 0, 'HK': 0, 'US': 0, 'TW': 0.2}
    assert raw == {'CN': 0.5, 'HK': 0.2, 'US': 0.1, 'TW': 0.2}


def test_paper_quotes_only_fetch_taiwan(monkeypatch):
    from src.modules.paper_trading import paper_trading_engine as paper
    quotes = Mock(return_value=[{'symbol': 'TWSE:2330', 'current_price': 2475}])
    monkeypatch.setattr(paper, 'md_quote_rows', quotes)
    monkeypatch.setattr(paper, '_is_trading_time', lambda _: True)
    result = paper.PaperTradingEngine()._fetch_quotes_map([('AAPL', 'US'), ('TWSE:2330', 'TW')])
    quotes.assert_called_once_with(['TWSE:2330'], 'TW')
    assert set(result) == {('TW', 'TWSE:2330')}


def test_scheduler_ignores_us_trading_day(monkeypatch):
    from src.platform.scheduling import trading_calendar as calendar
    check = Mock(side_effect=lambda market, _: market == 'US')
    monkeypatch.setattr(calendar, 'is_trading_day', check)
    assert not calendar.any_market_trading_day(datetime.fromisoformat('2026-09-29'))
    check.assert_called_once_with('TW', datetime.fromisoformat('2026-09-29'))


def test_market_status_only_contains_taiwan():
    from src.modules.market.api.stocks import get_market_status
    assert [item['code'] for item in get_market_status()] == ['TW']


def test_market_scan_never_fetches_disabled_markets(monkeypatch):
    from src.modules.strategy import entry_candidates as candidates
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(candidates, 'EastMoneyDiscoveryCollector', lambda **_: SimpleNamespace(fetch_hot_stocks=fetch))
    for name in ['_load_market_scan_seed_inputs', '_load_market_scan_history_inputs', '_load_market_scan_snapshot_inputs']:
        if hasattr(candidates, name):
            monkeypatch.setattr(candidates, name, lambda **_: {})
    candidates._load_market_scan_inputs(20)
    assert fetch.call_count == 2
    assert all(call.kwargs['market'] == 'TW' for call in fetch.call_args_list)


def test_stock_list_refresh_only_uses_taiwan_source(monkeypatch):
    from src.platform.marketdata import stock_list

    forbidden = Mock(side_effect=AssertionError('disabled source contacted'))
    for name in ('_fetch_from_eastmoney', '_fetch_from_akshare',
                 '_fetch_bj_from_eastmoney', '_fetch_hk_from_eastmoney',
                 '_fetch_us_from_eastmoney'):
        monkeypatch.setattr(stock_list, name, forbidden)
    monkeypatch.setattr(stock_list, '_fetch_tw_from_twmd', lambda: [
        {'symbol': 'TWSE:2330', 'name': '台積電', 'market': 'TW'}
    ])
    save = Mock()
    monkeypatch.setattr(stock_list, '_save_cache', save)

    rows = stock_list.refresh_stock_list()
    assert [row['market'] for row in rows] == ['TW']
    forbidden.assert_not_called()
    save.assert_called_once_with(rows)


def test_stock_list_cache_and_search_hide_disabled_markets(monkeypatch):
    from src.platform.marketdata import stock_list

    monkeypatch.setattr(stock_list, '_load_cache', lambda: [
        {'symbol': '600519', 'name': '貴州茅臺', 'market': 'CN'},
        {'symbol': 'TWSE:2330', 'name': '台積電', 'market': 'TW'},
    ])
    assert [row['market'] for row in stock_list.get_stock_list()] == ['TW']
    monkeypatch.setattr(stock_list, '_fetch_tw_from_twmd', lambda: [
        {'symbol': 'TWSE:2330', 'name': '台積電', 'market': 'TW'}
    ])
    realtime = Mock(side_effect=AssertionError('disabled search contacted'))
    monkeypatch.setattr(stock_list, '_realtime_search', realtime)
    assert stock_list.search_stocks('2330')[0]['symbol'] == 'TWSE:2330'
    assert stock_list.search_stocks('600519', market='CN') == []
    realtime.assert_not_called()


def test_calendar_refresh_skips_a_share_source(monkeypatch):
    from datetime import date
    from src.platform.scheduling import trading_calendar as calendar

    cn_fetch = Mock(side_effect=AssertionError('A-share calendar contacted'))
    monkeypatch.setattr(calendar, '_fetch_cn_trading_dates', cn_fetch)
    monkeypatch.setattr(calendar, '_fetch_tw_trading_dates',
                        lambda: frozenset({date(2026, 10, 2)}))
    assert calendar.refresh_blocking() is True
    cn_fetch.assert_not_called()
