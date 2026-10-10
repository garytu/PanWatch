"""評測框架自測（全 mock，不發真實請求，隨 make test 常跑）。

驗證：執行器能正確驅動工具迴圈並記錄過程；斷言引擎能抓住
工具選錯/白名單外/引數錯/無據回答/閒聊誤調工具等每一類失敗。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.eval.cases.chat_cases import CHAT_CASES
from tests.eval.cases.structured_cases import STRUCTURED_CASES
from tests.eval.framework import (
    ChatEvalCase,
    ChatEvalRunner,
    TOOL_WHITELIST,
    evaluate_case,
)
from tests.eval.judge import JudgeConfig, JudgeScore, LLMJudge


def _msg(content=None, tool_calls=None):
    """構造 chat_with_tools 返回的 message 替身。"""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _tc(id, name, arguments):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=id, function=fn)


class ScriptedAIClient:
    """按指令碼逐輪返回 message 的假模型。"""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.seen_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, temperature=0.0):
        self.seen_messages.append([dict(m) for m in messages])
        assert self._rounds, "指令碼輪次已用盡"
        return self._rounds.pop(0)


def _run(runner, case):
    return asyncio.run(runner.run_case(case))


def test_runner_records_tool_loop():
    """執行器：完整走一輪工具迴圈，記錄呼叫與最終回答，斷言全過"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519", "market": "CN"}')]),
        _msg(content="貴州茅臺現價 1712.5 元，漲 1.35%。"),
    ])
    result = _run(ChatEvalRunner(client), case)

    assert result.tool_calls == [("get_stock_quote", {"symbol": "600519", "market": "CN"})]
    assert "1712.5" in result.answer
    assert evaluate_case(case, result) == []
    # mock 工具資料確實注入了第二輪上下文
    tool_msgs = [m for m in client.seen_messages[-1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "1712.5" in tool_msgs[0]["content"]


def test_assert_catches_wrong_tool():
    """斷言引擎：該查行情卻查了自選股 → 報缺少必需工具"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_watchlist", "{}")]),
        _msg(content="您的自選股如下…"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("缺少必需的工具呼叫: get_stock_quote" in f for f in failures)


def test_assert_catches_whitelist_violation():
    """斷言引擎：呼叫白名單外的工具（如寫操作）→ 直接失敗"""
    case = ChatEvalCase(id="x", question="幫我下單買入")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "place_order", '{"symbol": "600519"}')]),
        _msg(content="已下單"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("白名單外的工具: place_order" in f for f in failures)


def test_assert_catches_wrong_params():
    """斷言引擎：symbol 引數傳錯 → 報引數不符"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "000001"}')]),
        _msg(content="價格 1712.5"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("引數不符合預期" in f for f in failures)


def test_assert_catches_ungrounded_answer():
    """斷言引擎：答案沒有引用工具返回的關鍵值 → 報無據"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519"}')]),
        _msg(content="茅臺是好公司，建議長期持有。"),  # 沒引用價格
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("答案缺少工具結果引用" in f for f in failures)


def test_assert_catches_chitchat_tool_call():
    """斷言引擎：閒聊時誤調工具 → 報不該呼叫"""
    case = next(c for c in CHAT_CASES if c.id == "chitchat-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_portfolio", "{}")]),
        _msg(content="你好！你的持倉是…"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("不該呼叫工具卻呼叫了" in f for f in failures)


def test_assert_tool_failure_case():
    """斷言引擎：工具失敗場景——如實說明透過，編造價格失敗"""
    case = next(c for c in CHAT_CASES if c.id == "fail-1")

    honest = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519"}')]),
        _msg(content="抱歉，行情資料獲取失敗，請稍後再試。"),
    ])
    assert evaluate_case(case, _run(ChatEvalRunner(honest), case)) == []

    fabricating = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519"}')]),
        _msg(content="600519 現價 1712.5 元。"),  # 工具失敗還報價 = 編造
    ])
    failures = evaluate_case(case, _run(ChatEvalRunner(fabricating), case))
    assert any("不應出現的內容" in f for f in failures)


def test_golden_set_size_and_whitelist():
    """golden set：規模 ≥ 30 條，且所有 expected_tools 都在白名單內"""
    assert len(CHAT_CASES) + len(STRUCTURED_CASES) >= 30
    ids = [c.id for c in CHAT_CASES] + [c.id for c in STRUCTURED_CASES]
    assert len(ids) == len(set(ids)), "用例 id 不得重複"
    for case in CHAT_CASES:
        for name in case.expected_tools:
            assert name in TOOL_WHITELIST, f"{case.id} 期望了白名單外的工具 {name}"


def test_judge_parse_and_mock_call():
    """judge：mock 使用者端端到端評分，容忍程式碼圍欄輸出"""

    class FakeJudgeClient:
        async def chat(self, system_prompt, user_content, temperature=0.0):
            assert "評審員" in system_prompt
            assert "600519" in user_content
            return '```json\n{"relevance": 5, "groundedness": 4, "clarity": 5, "comment": "有據且清晰"}\n```'

    config = JudgeConfig(base_url="http://mock", api_key="mock", model="mock-judge")
    judge = LLMJudge(config, client=FakeJudgeClient())
    score = asyncio.run(judge.judge("600519 多少錢", ["即時行情：價格 1712.5"], "現價 1712.5"))
    assert isinstance(score, JudgeScore)
    assert (score.relevance, score.groundedness, score.clarity) == (5, 4, 5)
    assert abs(score.mean - 14 / 3) < 1e-9


def test_judge_parse_rejects_bad_output():
    """judge：非法輸出（非 JSON / 缺維度 / 越界分值）解析行為正確"""
    with pytest.raises(ValueError):
        LLMJudge.parse_score("我覺得挺好的")
    with pytest.raises(ValueError):
        LLMJudge.parse_score('{"relevance": 5}')
    # 越界分值被夾到 1-5
    score = LLMJudge.parse_score(json.dumps({"relevance": 9, "groundedness": 0, "clarity": 3}))
    assert (score.relevance, score.groundedness, score.clarity) == (5, 1, 3)


def test_judge_config_from_env(monkeypatch):
    """judge：配置只從環境變數讀取，缺任一項即返回 None（不讀資料庫）"""
    for key in ("EVAL_JUDGE_BASE_URL", "EVAL_JUDGE_API_KEY", "EVAL_JUDGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert JudgeConfig.from_env() is None

    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://judge")
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "k")
    assert JudgeConfig.from_env() is None  # 還缺 model
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "judge-model")
    config = JudgeConfig.from_env()
    assert config is not None
    assert config.temperature == 0.0
