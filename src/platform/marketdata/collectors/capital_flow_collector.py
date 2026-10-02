"""資金流向採集器 - 經 marketdata 包統一接入"""
from dataclasses import dataclass, asdict

from src.platform.marketdata.collectors.market_http import TTLCache
from src.platform.marketdata.models import MarketCode

# 資金流為日級資料、變動慢:中等 TTL 快取,避免每輪重複拉。
_FLOW_CACHE = TTLCache(default_ttl_sec=600.0)


@dataclass
class CapitalFlow:
    """資金流向資料"""
    symbol: str
    name: str

    # 今日資金流（單位：元）
    main_net_inflow: float | None
    main_net_inflow_pct: float | None
    super_net_inflow: float | None
    big_net_inflow: float | None
    mid_net_inflow: float | None
    small_net_inflow: float | None

    # 5日資金流
    main_net_5d: float | None = None  # 5日主力淨流入
    flow_kind: str = "large_order_cash"
    unit: str = "currency"
    trade_date: str | None = None
    foreign_net_shares: float | None = None
    trust_net_shares: float | None = None
    dealer_net_shares: float | None = None
    institutional_net_shares: float | None = None
    institutional_net_5d_shares: float | None = None


def get_market_data():
    """惰性匯入,避免模組載入時的迴圈依賴(便於測試 monkeypatch)。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g
    return _g()


class CapitalFlowCollector:
    """資金流向採集器"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_capital_flow(self, symbol: str) -> CapitalFlow | None:
        """獲取單隻股票的資金流向(經 marketdata 包統一接入 + TTL快取)。"""
        cache_key = f"{self.market.value}:{symbol}"
        cached = _FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        md_cf = get_market_data().capital_flow(symbol, market=self.market.value)
        if md_cf is None:
            return None
        capital_flow = CapitalFlow(
            symbol=md_cf.symbol,
            name=md_cf.name,
            main_net_inflow=md_cf.main_net_inflow,
            main_net_inflow_pct=md_cf.main_net_inflow_pct,
            super_net_inflow=md_cf.super_net_inflow,
            big_net_inflow=md_cf.big_net_inflow,
            mid_net_inflow=md_cf.mid_net_inflow,
            small_net_inflow=md_cf.small_net_inflow,
            main_net_5d=md_cf.main_net_5d,
            **{key: getattr(md_cf, key, None) for key in (
                "flow_kind", "unit", "trade_date", "foreign_net_shares", "trust_net_shares",
                "dealer_net_shares", "institutional_net_shares", "institutional_net_5d_shares")},
        )
        _FLOW_CACHE.set(cache_key, capital_flow)
        return capital_flow

    def get_capital_flow_summary(self, symbol: str) -> dict:
        """獲取資金流向摘要（用於 prompt）"""
        flow = self.get_capital_flow(symbol)

        if not flow:
            return {"error": "無資金流向資料"}

        if flow.flow_kind == "institutional_shares":
            net = flow.institutional_net_shares
            net5 = flow.institutional_net_5d_shares
            return {**asdict(flow), "status": f"三大法人淨買賣超 {net:+,.0f} 股" if net is not None else "法人資料缺失",
                    "trend_5d": f"5日法人淨買賣超 {net5:+,.0f} 股" if net5 is not None else "無資料"}
        if flow.main_net_inflow is None:
            return {"error": "無資金流向資料"}

        # 判斷資金狀態
        if flow.main_net_inflow > 0:
            if (flow.main_net_inflow_pct or 0) > 10:
                status = "主力大幅流入"
            elif (flow.main_net_inflow_pct or 0) > 5:
                status = "主力明顯流入"
            else:
                status = "主力小幅流入"
        elif flow.main_net_inflow < 0:
            if (flow.main_net_inflow_pct or 0) < -10:
                status = "主力大幅流出"
            elif (flow.main_net_inflow_pct or 0) < -5:
                status = "主力明顯流出"
            else:
                status = "主力小幅流出"
        else:
            status = "主力資金平衡"

        # 5日趨勢
        trend_5d = "無資料"
        if flow.main_net_5d is not None:
            if flow.main_net_5d > 0:
                trend_5d = f"5日淨流入{flow.main_net_5d/1e8:.2f}億"
            else:
                trend_5d = f"5日淨流出{abs(flow.main_net_5d)/1e8:.2f}億"

        return {
            "status": status,
            "main_net_inflow": flow.main_net_inflow,
            "main_net_inflow_pct": flow.main_net_inflow_pct,
            "super_net_inflow": flow.super_net_inflow,
            "big_net_inflow": flow.big_net_inflow,
            "mid_net_inflow": flow.mid_net_inflow,
            "small_net_inflow": flow.small_net_inflow,
            "trend_5d": trend_5d,
        }
