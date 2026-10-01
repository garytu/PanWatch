"""Taiwan venue identities must survive TradingAgents' path-safe ticker boundary."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from src.modules.automation.tradingagents import agent as agent_module
from src.modules.automation.tradingagents import toolkit_adapter as ta


@pytest.mark.parametrize("canonical, alias", [
    ("TWSE:4164", "4164.TW"), ("TPEX:6488", "6488.TWO"),
])
def test_taiwan_alias_is_path_safe_and_retains_venue(canonical, alias):
    from tradingagents.dataflows.utils import safe_ticker_component

    assert safe_ticker_component(agent_module._tradingagents_ticker(canonical, "TW")) == alias
    assert ta._taiwan_identity(alias) == canonical
    assert ta._same_snapshot_symbol(alias, canonical)


def test_taiwan_route_uses_canonical_snapshot_and_rejects_other_instrument(monkeypatch):
    stock = SimpleNamespace(symbol="TWSE:4164", name="智擎", market=SimpleNamespace(value="TW"))
    snapshot = {"stock": stock, "quote": {"instrument_id": "TWSE:4164", "current_price": 42},
                "klines": [SimpleNamespace(date="2026-10-01", open=40, high=43, low=39, close=42, volume=1000)]}
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(ta, "_real_route_to_vendor", lambda *args, **kwargs:
                        pytest.fail("Taiwan quote must not fall through to upstream vendor"))
    with ta.panwatch_data_context(snapshot):
        result = ta._patched_route_to_vendor("get_stock_data", "4164.TW", "2026-10-01")
        other = ta._patched_route_to_vendor("get_stock_data", "2330.TW", "2026-10-01")
        df = ta._build_panwatch_ohlcv_df("4164.TW", "2026-10-01")
    assert "智擎" in result and "2026-10-01" in result
    assert "DATA_UNAVAILABLE" in other and "TWSE:4164" in other
    assert len(df) == 1 and df.iloc[0]["Close"] == 42


def test_graph_receives_safe_ticker_and_matching_portfolio(monkeypatch):
    from tradingagents.graph import trading_graph

    captured = {}

    class FakeGraph:
        def __init__(self, **kwargs):
            self.propagator = None
            self.total_cost = 0.01

        def propagate(self, company_name, trade_date, asset_type="stock", portfolio=None):
            captured["ticker"] = company_name
            captured["portfolio"] = portfolio
            return {"final_trade_decision": "**Rating**: Hold"}, "HOLD"

    portfolio = SimpleNamespace(accounts=[SimpleNamespace(available_funds=1000, positions=[
        SimpleNamespace(symbol="TWSE:4164", quantity=1000, cost_price=40)])])
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    monkeypatch.setattr(agent_module, "apply_compat_patches", lambda: None)
    monkeypatch.setattr(agent_module, "inject_api_key_env", lambda _: None)
    monkeypatch.setattr(agent_module, "patch_route_to_vendor", lambda: nullcontext())
    monkeypatch.setattr(agent_module, "panwatch_data_context", lambda *args, **kwargs: nullcontext())
    result = agent_module.TradingAgentsAgent()._run_tradingagents_sync(
        ai_client=SimpleNamespace(), symbol="TWSE:4164", market="TW",
        ta_config={"selected_analysts": ["market"]}, progress_handler=None,
        panwatch_data={"quote": {"instrument_id": "TWSE:4164"}}, portfolio=portfolio,
    )
    assert result["decision"] == "HOLD"
    assert captured["ticker"] == "4164.TW"
    assert captured["portfolio"].positions[0].ticker == "4164.TW"
