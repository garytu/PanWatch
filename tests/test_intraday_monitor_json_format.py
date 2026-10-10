import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.modules.automation.base import PortfolioInfo
from src.modules.automation.intraday_monitor import IntradayMonitorAgent, format_change_pct
from src.platform.marketdata.models import MarketCode, StockData


def test_intraday_monitor_loose_json_parse_with_json_prefix() -> None:
    """盤中監控 — json 字首寬鬆解析"""
    agent = IntradayMonitorAgent()
    text = '\njson\n{"action":"add","action_label":"建倉","signal":"放量突破","reason":"測試"}\n'
    obj = agent._try_parse_loose_json(text)  # noqa: SLF001 - internal helper regression
    assert obj is not None
    assert obj.get("action_label") == "建倉"


def test_intraday_monitor_parse_suggestion_accepts_non_standard_action() -> None:
    """盤中監控 — 非標準 action 別名對映"""
    agent = IntradayMonitorAgent()
    text = '\njson\n{"action":"build","action_label":"建倉","signal":"KDJ金叉","reason":"測試"}\n'
    result = agent._parse_suggestion(text)  # noqa: SLF001 - regression
    assert result["action_label"] == "建倉"
    assert result["signal"] == "KDJ金叉"


def test_intraday_monitor_missing_change_does_not_fail_after_analysis(monkeypatch) -> None:
    stock = StockData(
        symbol="TWSE:2303", name="聯電", market=MarketCode.TW,
        current_price=159.5, change_pct=None, change_amount=None,
        volume=None, turnover=None, open_price=None, high_price=None,
        low_price=None, prev_close=None,
    )
    agent = IntradayMonitorAgent()
    context = SimpleNamespace(
        portfolio=PortfolioInfo(),
        ai_client=SimpleNamespace(chat=AsyncMock(return_value='{"action":"watch","reason":"測試"}')),
        model_label="",
    )
    monkeypatch.setattr("src.modules.automation.intraday_monitor.save_suggestion", lambda **_: None)
    monkeypatch.setattr("src.modules.automation.intraday_monitor.save_agent_prediction_outcome", lambda **_: None)
    monkeypatch.setattr("src.modules.automation.intraday_monitor.save_agent_context_run", lambda **_: None)
    monkeypatch.setattr(
        "src.modules.automation.tradingagents.operations.try_auto_trigger", lambda *_args, **_kwargs: None
    )

    data = {"stock_data": stock, "timestamp": "2026-10-02T09:05:10+08:00"}
    result = asyncio.run(agent.analyze(context, data))

    assert result.title == "【盤中監測】聯電 N/A"
    assert "漲跌：N/A" in result.content
    assert "當前漲跌幅：N/A（資料不足）" in context.ai_client.chat.call_args.args[1]
    assert format_change_pct(0) == "+0.00%"
