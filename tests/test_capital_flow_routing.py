"""資金流取數路由測試(經 marketdata 包統一接入)"""
import src.platform.marketdata.collectors.capital_flow_collector as cf
from src.platform.marketdata.models import MarketCode


def test_get_capital_flow_uses_marketdata(monkeypatch):
    """走 marketdata 包的 capital_flow,轉換為 PanWatch CapitalFlow"""
    from marketdata.types import CapitalFlow as MdCF

    class _MD:
        def capital_flow(self, symbol, *, market="CN"):
            return MdCF(symbol=symbol, name="X", main_net_inflow=5000.0, main_net_inflow_pct=3.2,
                        super_net_inflow=1000.0, big_net_inflow=1500.0, mid_net_inflow=2000.0,
                        small_net_inflow=500.0, main_net_5d=None)

    monkeypatch.setattr(cf, "get_market_data", lambda: _MD())
    out = cf.CapitalFlowCollector(MarketCode.CN).get_capital_flow("600519")
    assert out is not None and isinstance(out, cf.CapitalFlow)
    assert out.main_net_inflow == 5000.0 and out.symbol == "600519"


def test_taiwan_flow_summary_keeps_share_evidence_and_skips_old_cache(monkeypatch):
    from marketdata.types import CapitalFlow as MdCF

    calls = []

    class _MD:
        def capital_flow(self, symbol, *, market="CN"):
            calls.append((symbol, market))
            return MdCF(
                symbol="TPEX:5347", name="X", flow_kind="institutional_shares", unit="shares",
                trade_date="2026-10-02", foreign_net_shares=100, trust_net_shares=5,
                dealer_net_shares=20, institutional_net_shares=125,
                institutional_net_5d_shares=None,
                native_components={"foreign_dealer_net_shares": 25},
                evidence={"status": "available", "rows": [{"trade_date": "2026-10-02"}]},
            )

    monkeypatch.setattr(cf, "get_market_data", lambda: _MD())
    collector = cf.CapitalFlowCollector(MarketCode.TW)

    summary = collector.get_capital_flow_summary("TPEX:5347")
    collector.get_capital_flow("TPEX:5347")

    assert len(calls) == 2
    assert summary["status"] == "三大法人淨買賣超 +125 股"
    assert summary["trend_5d"] == "無資料"
    assert summary["institutional_net_5d_shares"] is None
    assert summary["native_components"]["foreign_dealer_net_shares"] == 25
    assert summary["evidence"]["rows"][0]["trade_date"] == "2026-10-02"
