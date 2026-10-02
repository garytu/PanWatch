from src.modules.research.signals.structured_output import try_parse_action_json


def test_try_parse_action_json_plain_json_prefix() -> None:
    """LLM 輸出解析 — json 字首格式"""
    text = '\njson\n{"action":"add","action_label":"建倉","reason":"突破"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
    assert obj.get("action_label") == "建倉"


def test_try_parse_action_json_fenced_json() -> None:
    """LLM 輸出解析 — 程式碼塊格式"""
    text = '```json\n{"action":"reduce","action_label":"減碼"}\n```'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "reduce"


def test_try_parse_action_json_action_alias_build_to_add() -> None:
    """LLM 輸出解析 — build 別名自動對映為 add"""
    text = '\njson\n{"action":"build","action_label":"建倉","reason":"突破"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
