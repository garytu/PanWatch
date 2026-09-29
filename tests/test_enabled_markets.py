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
    provider = Mock()
    monkeypatch.setattr(market, 'get_market_data', provider)
    assert asyncio.run(market.get_market_indices()) == []
    provider.assert_not_called()


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
