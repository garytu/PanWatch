"""Taiwan venue identities must survive TradingAgents' path-safe ticker boundary."""

from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.modules.automation.tradingagents import agent as agent_module
from src.modules.automation.tradingagents import toolkit_adapter as ta


def test_three_statement_renderers_use_the_authentic_typed_block_and_keep_report_discovery_separate():
    from marketdata.financial_statements import decode_financial_statement_response
    from src.modules.research.twmd_financial_statements import financial_statement_block
    from dataclasses import asdict
    path = Path(__file__).parents[1] / "packages/marketdata/tests/fixtures/twmd/captured/financial-statements-twse-2330-2024q4.json"
    payload = json.loads(path.read_text())
    payload["coverage"].update(latest_discovery_presence="not_advertised", capture_id="later-discovery",
                               original_received_at_utc="2026-10-06T00:00:00Z")
    read = decode_financial_statement_response(payload, instrument_id="TWSE:2330", fiscal_year=2024,
        fiscal_quarter=4, report_scope="consolidated", statement=None, limit=1000)
    research = {"blocks": {"financial_statements": asdict(financial_statement_block(read))}}
    for statement, concept, value in (
        ("balance_sheet", "Assets", "6691938000000"),
        ("comprehensive_income", "Revenue", "2894307699000"),
        ("cash_flows", "CashFlowsFromUsedInOperatingActivities", "1826177068000"),
    ):
        rendered = ta._render_taiwan_financial_statement(research, statement)
        assert concept in rendered and value in rendered
        assert "比較期" in rendered and "not_advertised" in rendered
        assert read.report.original_received_at_utc in rendered
        assert "年初至今值不是單季值" in rendered
    assert "45.25 TWD/shares" in ta._render_taiwan_financial_statement(research, "comprehensive_income")


def test_taiwan_missing_financial_block_never_uses_cn_abstract_or_quote(monkeypatch):
    stock = SimpleNamespace(symbol="TWSE:2330", market=SimpleNamespace(value="TW"))
    abstract = {"periods": ["20251231"], "indicators": {"營業總收入": {"20251231": 123456789}}}
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **kw: None)
    with ta.panwatch_data_context({"stock": stock, "quote": {"current_price": 69.1}, "financial": abstract}):
        for method in ("get_income_statement", "get_balance_sheet", "get_cashflow"):
            output = ta._serve_from_panwatch(method, "2330.TW", {}, ()).split("\n\n", 1)[-1]
            assert "unavailable" in output and "123456789" not in output and "69.1" not in output
    with ta.panwatch_data_context({"stock": SimpleNamespace(symbol="600519", market=SimpleNamespace(value="CN")), "financial": abstract}):
        assert "akshare" in ta._serve_from_panwatch("get_income_statement", "600519", {}, ())


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


def test_taiwan_research_tools_share_evidence_and_render_three_official_statements(monkeypatch):
    stock = SimpleNamespace(symbol="TWSE:2330", name="台積電", market=SimpleNamespace(value="TW"))
    def fact(statement, concept, value, lexical, scale, period):
        return {
            "statement": statement, "occurrence_ordinal": 1,
            "concept_qname": f"{{urn:ifrs}}{concept}",
            "context": {"period": period, "entity_identifier": "2330", "entity_scheme": "http://www.twse.com.tw"},
            "unit": {"numerator": ["{http://www.xbrl.org/2003/iso4217}TWD"], "denominator": []},
            "value": value, "lexical_value": lexical, "scale": scale, "sign": None,
            "decimals": "-3", "precision": None, "is_nil": False,
        }

    research = {
        "instrument_id": "TWSE:2330",
        "blocks": {
            "institutional_flows": {"status": "partial", "evidence": {"receipt": "source-receipt"}, "data": {"shares": 123}},
            "financial_statements": {
                "status": "available", "reason": "report_retained",
                "data": {
                    "instrument_id": "TWSE:2330", "fiscal_year": 2024, "fiscal_quarter": 3,
                    "report_scope": "consolidated", "truncated": False,
                    "returned_fact_count": 3, "total_fact_count": 3,
                    "qualification": {"status": "qualified", "reason": "twse_equity_industry_24"},
                    "coverage": {"latest_discovery_presence": "not_advertised", "original_received_at_utc": "2026-10-06T06:00:00Z"},
                    "report": {"member_filename": "mops-report.html", "semantic_revision_id": "revision-1", "raw_sha256": "a" * 64, "original_received_at_utc": "2026-10-04T13:00:00Z"},
                    "facts": [
                        fact("comprehensive_income", "Revenue", "1000000", "1,000", 3, {"kind": "duration", "start_date": "2024-01-01", "end_date": "2024-09-30"}),
                        fact("balance_sheet", "Assets", "2000000", "2,000", 3, {"kind": "instant", "instant": "2024-09-30"}),
                        fact("cash_flows", "CashFlowsFromUsedInOperatingActivities", "3000000", "3,000", 3, {"kind": "duration", "start_date": "2024-01-01", "end_date": "2024-09-30"}),
                    ],
                },
            },
        },
        "limitations": {"financial_statements": {"status": "limited_scope"}},
    }
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **kw: None)
    monkeypatch.setattr(ta, "_real_route_to_vendor", lambda *a, **kw: pytest.fail("TW financial tools must not reach upstream finance"))
    with ta.panwatch_data_context({"stock": stock, "quote": {"instrument_id": "TPEX:2330"}, "taiwan_research": research}):
        fundamentals = ta._patched_route_to_vendor("get_fundamentals", "2330.TW")
        flows = ta._patched_route_to_vendor("get_capital_flow", "2330.TW")
        assert "source-receipt" in fundamentals and "source-receipt" in flows
        assert '"financial_statements"' in fundamentals and "not_integrated" not in fundamentals
        income = ta._patched_route_to_vendor("get_income_statement", "2330.TW")
        balance = ta._patched_route_to_vendor("get_balance_sheet", "2330.TW")
        cashflow = ta._patched_route_to_vendor("get_cashflow", "2330.TW")
        assert "Revenue" in income and "1,000" in income and "年初至今值不是單季值" in income
        assert "Assets" in balance and "2,000" in balance
        assert "CashFlowsFromUsedInOperatingActivities" in cashflow and "3,000" in cashflow
        assert "DATA_UNAVAILABLE" in ta._patched_route_to_vendor("get_fundamentals", "2330.TWO")
    research["instrument_id"] = "TPEX:2330"
    with ta.panwatch_data_context({"stock": stock, "taiwan_research": research}):
        assert "source-receipt" not in ta._patched_route_to_vendor("get_fundamentals", "2330.TW")
