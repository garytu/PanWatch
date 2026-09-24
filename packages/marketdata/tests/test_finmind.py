"""FinMind Vendor 單測 (全 mock)。"""

from __future__ import annotations

from marketdata.symbol import Market, Symbol
from marketdata.vendors.finmind import (
    FinMindCapitalFlowVendor,
    FinMindDividendVendor,
    FinMindFundamentalsVendor,
    FinMindKlineVendor,
    FinMindMarginVendor,
    FinMindNewsVendor,
    fetch_finmind_exchange_rate,
    fetch_finmind_trading_dates,
)


def test_finmind_kline_vendor(monkeypatch):
    vendor = FinMindKlineVendor()
    fake_data = [
        {"date": "2026-09-22", "open": 970.0, "max": 975.0, "min": 968.0, "close": 972.0, "Trading_Volume": 15000000},
        {"date": "2026-09-23", "open": 973.0, "max": 980.0, "min": 971.0, "close": 978.0, "Trading_Volume": 20000000},
    ]
    monkeypatch.setattr(
        "marketdata.vendors.finmind._finmind_get",
        lambda dataset, **kwargs: fake_data if dataset == "TaiwanStockPriceAdj" else [],
    )

    bars = vendor.fetch([Symbol(Market.TW, "2330")], config={"days": 10})
    assert len(bars) == 2
    assert bars[0].date == "2026-09-22"
    assert bars[0].open == 970.0
    assert bars[1].close == 978.0
    assert bars[1].volume == 20000000.0


def test_finmind_fundamentals_vendor(monkeypatch):
    vendor = FinMindFundamentalsVendor()

    def mock_get(dataset, **kwargs):
        if dataset == "TaiwanStockPER":
            return [{"date": "2026-09-23", "PER": 24.5, "PBR": 5.2, "dividend_yield": 2.1}]
        if dataset == "TaiwanStockFinancialStatements":
            return [
                {"date": "2026-06-30", "type": "EPS", "value": 9.5},
                {"date": "2026-06-30", "type": "TotalRevenue", "value": 673510000000.0},
                {"date": "2026-06-30", "type": "NetIncome", "value": 247850000000.0},
            ]
        return []

    monkeypatch.setattr("marketdata.vendors.finmind._finmind_get", mock_get)

    res = vendor.fetch([Symbol(Market.TW, "2330")], config={})
    assert len(res) == 1
    f = res[0]
    assert f.symbol == "2330"
    assert f.pe_ttm == 24.5
    assert f.pb == 5.2
    assert f.dividend_yield == 2.1
    assert f.eps == 9.5
    assert f.revenue == 673510000000.0
    assert f.net_profit == 247850000000.0


def test_finmind_capital_flow_vendor(monkeypatch):
    vendor = FinMindCapitalFlowVendor()
    fake_flow = [
        {"date": "2026-09-22", "name": "Foreign_Investor", "buy": 1000, "sell": 400},
        {"date": "2026-09-22", "name": "Investment_Trust", "buy": 500, "sell": 100},
        {"date": "2026-09-22", "name": "Dealer_self", "buy": 200, "sell": 100},
        {"date": "2026-09-23", "name": "Foreign_Investor", "buy": 1200, "sell": 200},
        {"date": "2026-09-23", "name": "Investment_Trust", "buy": 300, "sell": 50},
        {"date": "2026-09-23", "name": "Dealer_self", "buy": 100, "sell": 200},
    ]
    monkeypatch.setattr("marketdata.vendors.finmind._finmind_get", lambda dataset, **kwargs: fake_flow)

    res = vendor.fetch([Symbol(Market.TW, "2330")], config={})
    assert len(res) == 1
    cf = res[0]
    assert cf.symbol == "2330"
    # 2026-09-23: Foreign: +1000, Trust: +250, Dealer: -100 => Total main = +1150
    assert cf.main_net_inflow == 1150.0
    assert cf.super_net_inflow == 1000.0
    assert cf.big_net_inflow == 250.0


def test_finmind_margin_vendor(monkeypatch):
    vendor = FinMindMarginVendor()
    fake_margin = [
        {
            "date": "2026-09-23",
            "MarginPurchaseTodayBalance": 12000,
            "MarginPurchaseBuy": 1500,
            "MarginPurchaseCashRepayment": 200,
            "ShortSaleTodayBalance": 450,
            "ShortSaleSell": 80,
            "ShortSaleCashRepayment": 10,
        }
    ]
    monkeypatch.setattr("marketdata.vendors.finmind._finmind_get", lambda dataset, **kwargs: fake_margin)

    res = vendor.fetch([Symbol(Market.TW, "2330")], config={})
    assert len(res) == 1
    m = res[0]
    assert m.symbol == "2330"
    assert m.rz_balance == 12000.0
    assert m.rz_buy == 1500.0
    assert m.rq_balance == 450.0


def test_finmind_dividend_vendor(monkeypatch):
    vendor = FinMindDividendVendor()
    fake_div = [
        {
            "CashExDividendTradingDate": "2026-06-18",
            "CashEarningsDistribution": 4.0,
            "StockEarningsDistribution": 0.0,
        }
    ]
    monkeypatch.setattr("marketdata.vendors.finmind._finmind_get", lambda dataset, **kwargs: fake_div)

    res = vendor.fetch([Symbol(Market.TW, "2330")], config={})
    assert len(res) == 1
    d = res[0]
    assert d.ex_date == "2026-06-18"
    assert d.dividend_per_share == 4.0


def test_finmind_calendar_and_fx(monkeypatch):
    def mock_get(dataset, **kwargs):
        if dataset == "TaiwanStockTradingDate":
            return [{"date": "2026-09-23"}, {"date": "2026-09-24"}]
        if dataset == "TaiwanExchangeRate":
            return [
                {"currency": "CNY", "spot_buy": 4.48, "spot_sell": 4.52}
            ]
        return []

    monkeypatch.setattr("marketdata.vendors.finmind._finmind_get", mock_get)

    dates = fetch_finmind_trading_dates()
    assert "2026-09-23" in dates
    assert "2026-09-24" in dates

    fx = fetch_finmind_exchange_rate("CNY")
    assert fx == 4.50
