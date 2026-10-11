"""FinMind 台股 Vendor 套件 (日K、基本面、三大法人、融資融券、除權息、新聞、日曆與匯率)。

免費版方案支援單股 (需帶 data_id)，每小時 600 次請求。
官方文件參考: https://finmindtrade.com / https://finmind.github.io/
"""

from __future__ import annotations

import logging
import math
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone

from marketdata.http import market_get, record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import (
    Bar,
    CapitalFlow,
    DividendItem,
    Fundamentals,
    MarginItem,
    NewsArticle,
)
from marketdata.vendors.base import (
    CapitalFlowVendor,
    DividendVendor,
    FundamentalsVendor,
    KlineVendor,
    MarginVendor,
    NewsVendor,
)

logger = logging.getLogger(__name__)

_FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"


def _get_token(config: dict) -> str | None:
    return config.get("token") or os.environ.get("FINMIND_API_TOKEN") or None


def _finmind_get(dataset: str, data_id: str | None = None, start_date: str | None = None,
                 end_date: str | None = None, config: dict | None = None,
                 timeout: float = 10.0, log_label: str = "FinMind") -> list[dict]:
    """呼叫 FinMind /api/v4/data。"""
    config = config or {}
    token = _get_token(config)
    proxy = config.get("proxy")

    params: dict[str, str] = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if token:
        params["token"] = token

    payload = market_get(
        _FINMIND_API_URL,
        host_key="api.finmindtrade.com",
        params=params,
        timeout=timeout,
        retries=2,
        parse="json",
        proxy=proxy,
        log_label=log_label,
        symbol=data_id or "",
    )

    if not isinstance(payload, dict):
        return []
    if payload.get("status") != 200:
        err = payload.get("msg") or "FinMind API 回應狀態非 200"
        record_error(f"FinMind {dataset} {data_id or ''}: {err}")
        return []
    data = payload.get("data")
    return data if isinstance(data, list) else []


class FinMindKlineVendor(KlineVendor):
    """FinMind 台股還原日K Vendor。"""

    name = "finmind"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market != Market.TW:
            return []

        days = int(config.get("days") or 120)
        # 預留日曆日與非交易日緩衝
        lookback_days = max(days * 2, 60)
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        data = _finmind_get(
            dataset="TaiwanStockPriceAdj",
            data_id=sym.code,
            start_date=start_date,
            config=config,
            log_label="FinMind日K",
        )
        if not data:
            # 嘗試未還原日K兜底
            data = _finmind_get(
                dataset="TaiwanStockPrice",
                data_id=sym.code,
                start_date=start_date,
                config=config,
                log_label="FinMind日K(未還原)",
            )

        bars: list[Bar] = []
        for row in data:
            try:
                bars.append(
                    Bar(
                        date=str(row.get("date", "")),
                        open=float(row.get("open") or 0.0),
                        close=float(row.get("close") or 0.0),
                        high=float(row.get("max") or row.get("high") or 0.0),
                        low=float(row.get("min") or row.get("low") or 0.0),
                        volume=float(row.get("Trading_Volume") or row.get("volume") or 0.0),
                    )
                )
            except Exception:
                continue

        # 按日期正序截取最後 days 根
        bars.sort(key=lambda b: b.date)
        return bars[-days:] if len(bars) > days else bars


class FinMindFundamentalsVendor(FundamentalsVendor):
    """FinMind 台股基本面/估值 Vendor (PER, PBR, 股息率, 營收, 財報)。"""

    name = "finmind"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Fundamentals]:
        out: list[Fundamentals] = []
        for sym in symbols:
            if sym.market != Market.TW:
                continue

            # 1. 抓取近期 PER / PBR / 殖利率
            lookback = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            per_data = _finmind_get(
                dataset="TaiwanStockPER",
                data_id=sym.code,
                start_date=lookback,
                config=config,
                log_label="FinMind估值",
            )
            latest_per = per_data[-1] if per_data else {}

            pe_ttm = float(latest_per["PER"]) if latest_per.get("PER") is not None else None
            pb = float(latest_per["PBR"]) if latest_per.get("PBR") is not None else None
            div_yield = float(latest_per["dividend_yield"]) if latest_per.get("dividend_yield") is not None else None

            # 2. 抓取最新損益表 (EPS, 營收, 淨利)
            fin_lookback = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
            stmt_data = _finmind_get(
                dataset="TaiwanStockFinancialStatements",
                data_id=sym.code,
                start_date=fin_lookback,
                config=config,
                log_label="FinMind財報",
            )
            eps: float | None = None
            revenue: float | None = None
            net_profit: float | None = None
            report_date = ""

            for row in reversed(stmt_data):
                t = str(row.get("type", ""))
                val = row.get("value")
                if val is None:
                    continue
                if "EPS" in t and eps is None:
                    eps = float(val)
                    report_date = str(row.get("date", ""))
                elif ("Revenue" in t or "營業收入" in str(row.get("origin_name", ""))) and revenue is None:
                    revenue = float(val)
                elif ("NetIncome" in t or "淨利" in str(row.get("origin_name", ""))) and net_profit is None:
                    net_profit = float(val)

            out.append(
                Fundamentals(
                    symbol=sym.code,
                    market=sym.market.value,
                    pe_ttm=pe_ttm,
                    pb=pb,
                    dividend_yield=div_yield,
                    eps=eps,
                    revenue=revenue,
                    net_profit=net_profit,
                    report_date=report_date,
                    timestamp=datetime.now(),
                )
            )
        return out


class FinMindCapitalFlowVendor(CapitalFlowVendor):
    """FinMind 三大法人買賣超 (對齊 PanWatch CapitalFlow)。"""

    name = "finmind"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[CapitalFlow]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market != Market.TW:
            return []

        # 抓取最近 10 天三大法人數據
        lookback = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        start_date = str(config.get("start_date") or lookback)
        end_date = str(config.get("end_date") or "") or None
        data = _finmind_get(
            dataset="TaiwanStockInstitutionalInvestorsBuySell",
            data_id=sym.code,
            start_date=start_date,
            end_date=end_date,
            config=config,
            log_label="FinMind三大法人",
        )
        if not data:
            return []

        # Keep missing categories null. The endpoint has no date-complete
        # coverage contract, so it cannot prove an exact five-session sum.
        by_date: dict[str, dict[str, int | None]] = {}
        for row in data:
            dt = str(row.get("date", ""))
            if not dt:
                continue
            buy = _share_integer(row.get("buy"))
            sell = _share_integer(row.get("sell"))
            net = buy - sell if buy is not None and sell is not None else None
            name = str(row.get("name", ""))
            if name:
                by_date.setdefault(dt, {})[name] = net

        sorted_dates = sorted(by_date.keys())
        if not sorted_dates:
            return []

        latest_date = sorted_dates[-1]
        day_flows = by_date[latest_date]

        foreign_net = day_flows.get("Foreign_Investor")
        trust_net = day_flows.get("Investment_Trust")
        dealer_self = day_flows.get("Dealer_self")
        dealer_hedging = day_flows.get("Dealer_Hedging")
        dealer_net = (
            dealer_self + dealer_hedging
            if dealer_self is not None and dealer_hedging is not None else None
        )
        total_main_net = (
            foreign_net + trust_net + dealer_net
            if foreign_net is not None and trust_net is not None and dealer_net is not None
            else None
        )
        native_components = {
            "foreign_net_shares": foreign_net,
            "investment_trust_net_shares": trust_net,
            "dealer_proprietary_net_shares": dealer_self,
            "dealer_hedging_net_shares": dealer_hedging,
        }

        return [
            CapitalFlow(
                symbol=sym.code,
                name="",
                flow_kind="institutional_shares", unit="shares", trade_date=latest_date,
                foreign_net_shares=foreign_net, trust_net_shares=trust_net,
                dealer_net_shares=dealer_net, institutional_net_shares=total_main_net,
                institutional_net_5d_shares=None,
                native_components=native_components,
                evidence={
                    "provider": "finmind",
                    "source": "finmind",
                    "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                    "selectors": {"data_id": sym.code, "start_date": start_date,
                                  "end_date": end_date},
                    "status": "available",
                    "reason": "selected_record_present",
                    "category_presence": {key: value is not None for key, value in native_components.items()},
                    "rows": data,
                    "five_day_total": "not_computed_without_complete_session_coverage",
                },
            )
        ]


def _share_integer(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("FinMind share value is not numeric") from exc
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise ValueError("FinMind share value must be an integer")
    return int(numeric)


class FinMindMarginVendor(MarginVendor):
    """FinMind 個股融資融券 Vendor。"""

    name = "finmind"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[MarginItem]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market != Market.TW:
            return []

        lookback = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        data = _finmind_get(
            dataset="TaiwanStockMarginPurchaseShortSale",
            data_id=sym.code,
            start_date=lookback,
            config=config,
            log_label="FinMind融資融券",
        )
        if not data:
            return []

        latest = data[-1]
        def quantity(key: str) -> float | None:
            try:
                value = float(latest[key])
                return value if math.isfinite(value) else None
            except (KeyError, TypeError, ValueError):
                return None

        return [
            MarginItem(
                date=str(latest.get("date", "")),
                symbol=sym.code,
                # FinMind reports quantities in lots, rather than money or shares.
                quantity_unit="lots",
                margin_balance_lots=quantity("MarginPurchaseTodayBalance"),
                margin_buy_lots=quantity("MarginPurchaseBuy"),
                margin_cash_repayment_lots=quantity("MarginPurchaseCashRepayment"),
                short_balance_lots=quantity("ShortSaleTodayBalance"),
                short_sell_lots=quantity("ShortSaleSell"),
                short_repayment_lots=quantity("ShortSaleCashRepayment"),
            )
        ]


class FinMindDividendVendor(DividendVendor):
    """FinMind 股利政策/除權息 Vendor。"""

    name = "finmind"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[DividendItem]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market != Market.TW:
            return []

        lookback = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
        data = _finmind_get(
            dataset="TaiwanStockDividend",
            data_id=sym.code,
            start_date=lookback,
            config=config,
            log_label="FinMind股利",
        )
        out: list[DividendItem] = []
        for row in data:
            ex_date = str(row.get("CashExDividendTradingDate") or row.get("date") or "")
            cash_div = float(row.get("CashEarningsDistribution") or 0.0)
            stock_div = float(row.get("StockEarningsDistribution") or 0.0)
            if not ex_date:
                continue
            out.append(
                DividendItem(
                    ex_date=ex_date,
                    symbol=sym.code,
                    dividend_per_share=cash_div,
                    bonus_ratio=stock_div,
                    progress="已決議",
                )
            )
        return out


class FinMindNewsVendor(NewsVendor):
    """FinMind 台股新聞 Vendor。"""

    name = "finmind"
    supports_markets = {"TW"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        out: list[NewsArticle] = []
        lookback = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        for sym in symbols:
            if sym.market != Market.TW:
                continue
            data = _finmind_get(
                dataset="TaiwanStockNews",
                data_id=sym.code,
                start_date=lookback,
                config=config,
                log_label="FinMind新聞",
            )
            for row in data:
                title = str(row.get("title") or "")
                link = str(row.get("link") or "")
                source = str(row.get("source") or "FinMind")
                content = str(row.get("description") or "")
                date_str = str(row.get("date") or "")
                try:
                    dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)

                out.append(
                    NewsArticle(
                        source=source,
                        external_id=link or f"{sym.code}_{date_str}",
                        title=title,
                        content=content,
                        publish_time=dt,
                        symbols=[sym.identity],
                        url=link,
                    )
                )
        return out


# ── 工具函式: 交易日曆與匯率 ────────────────────────────────────────────────

def fetch_finmind_trading_dates(start_date: str = "2026-01-01", config: dict | None = None) -> set[str]:
    """獲取台股法定交易日集合 (YYYY-MM-DD)。"""
    data = _finmind_get(
        dataset="TaiwanStockTradingDate",
        start_date=start_date,
        config=config,
        log_label="FinMind交易日曆",
    )
    return {str(row["date"]) for row in data if "date" in row}


def fetch_finmind_exchange_rate(currency: str = "CNY", config: dict | None = None) -> float | None:
    """獲取台幣對外幣的匯率 (1 外幣 = ? 台幣，如 1 CNY = ~4.5 TWD)。"""
    lookback = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    data = _finmind_get(
        dataset="TaiwanExchangeRate",
        data_id=currency,
        start_date=lookback,
        config=config,
        log_label="FinMind匯率",
    )
    rates = [row for row in data if row.get("currency") == currency]
    if not rates:
        return None
    latest = rates[-1]
    # 優先現鈔買入/賣出均值，若無則即期
    buy = float(latest.get("spot_buy") or latest.get("cash_buy") or 0.0)
    sell = float(latest.get("spot_sell") or latest.get("cash_sell") or 0.0)
    if buy > 0 and sell > 0:
        return (buy + sell) / 2.0
    return buy or sell or None
