from src.modules.automation.intraday_monitor import IntradayMonitorAgent


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
