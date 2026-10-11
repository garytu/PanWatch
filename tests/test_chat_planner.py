"""Planning 試點(全面診斷持倉)測試。

全 mock ai_client 與工具執行器,不觸網。覆蓋:意圖識別、計劃解析容錯、
正常逐步推進、步驟失敗重規劃、計劃生成失敗降級預設計劃。
"""

import asyncio

from src.modules.assistant.chat_planner import (
    build_default_plan,
    normalize_steps,
    parse_plan,
    run_portfolio_diagnosis,
    should_use_planning,
)


class FakeStream:
    """記錄 publish 事件的假 SSE 流。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event, data):
        self.events.append((event, data))

    def plan_events(self):
        return [d for e, d in self.events if e == "plan"]

    def tokens(self):
        return "".join(d.get("text", "") for e, d in self.events if e == "token")


class FakeAI:
    """指令碼化假 AI:chat_multi 從佇列彈出(異常則拋),chat_stream 產出固定 token。"""

    def __init__(self, multi_queue, stream_tokens=("診", "斷", "完")):
        self.multi_queue = list(multi_queue)
        self.stream_tokens = list(stream_tokens)
        self.multi_calls = 0

    async def chat_multi(self, messages, temperature=0.4):
        self.multi_calls += 1
        item = self.multi_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        for t in self.stream_tokens:
            yield ("token", t)
        yield ("message", {"content": "".join(self.stream_tokens), "tool_calls": []})


def _exec_ok(db, name, args):
    async def _inner():
        return {
            "get_portfolio": "持倉:貴州茅臺(600519) 100股",
            "get_technical_analysis": "技術面:多頭",
            "get_stock_suggestions": "建議:持有",
        }.get(name, "")

    return _inner()


# ── 意圖識別 ────────────────────────────────────────────────────────────
def test_should_use_planning_hits():
    """命中觸發詞 → 走計劃驅動"""
    assert should_use_planning("幫我全面診斷我的持倉")
    assert should_use_planning("持倉診斷一下")
    assert should_use_planning("給我的組合做個全面體檢")


def test_should_use_planning_miss():
    """普通問題不觸發"""
    assert not should_use_planning("茅臺現在多少錢")
    assert not should_use_planning("")


# ── 計劃解析容錯 ──────────────────────────────────────────────────────────
def test_parse_plan_plain_list():
    """純 JSON 列表"""
    steps = parse_plan('[{"title":"A","action":"portfolio_risk"}]')
    assert steps and steps[0]["title"] == "A"


def test_parse_plan_dict_with_steps():
    """dict 帶 steps 欄位"""
    steps = parse_plan('{"steps":[{"title":"B","action":"analyze_stock"}]}')
    assert steps and steps[0]["action"] == "analyze_stock"


def test_parse_plan_json_fence_with_prose():
    """```json 圍欄 + 前後解釋文字"""
    text = '好的,這是計劃:\n```json\n{"steps":[{"title":"C","action":"portfolio_risk"}]}\n```\n請確認'
    steps = parse_plan(text)
    assert steps and steps[0]["title"] == "C"


def test_parse_plan_malformed_returns_none():
    """完全非 JSON → None"""
    assert parse_plan("抱歉我無法生成計劃") is None
    assert parse_plan("") is None


def test_normalize_steps_filters_summarize_and_assigns_ids():
    """規範化:過濾 summarize,補 id/status"""
    steps = normalize_steps(
        [
            {"title": "X", "action": "analyze_stock"},
            {"title": "彙總", "action": "summarize"},
            {"title": "Y", "action": "portfolio_risk"},
        ]
    )
    assert [s["id"] for s in steps] == [1, 2]
    assert all(s["status"] == "pending" for s in steps)
    assert all(s["action"] != "summarize" for s in steps)


# ── 編排:正常 / 重規劃 / 降級 ────────────────────────────────────────────
def test_run_diagnosis_happy_path():
    """正常:生成計劃 → 逐步執行 → 流式彙總,plan 事件推進到 done"""
    plan = '{"steps":[{"title":"組合整體風險","action":"portfolio_risk"}]}'
    ai = FakeAI(multi_queue=[plan, "風險評估結果"])
    stream = FakeStream()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec_ok))

    assert summary == "診斷完"  # 流式 token 拼接
    plans = stream.plan_events()
    assert plans[0]["status"] == "planning"
    assert plans[-1]["status"] == "done"
    # 最終步驟全部完成
    assert all(s["status"] == "done" for s in plans[-1]["steps"])
    assert stream.tokens() == "診斷完"


def test_run_diagnosis_replan_on_step_failure():
    """步驟失敗 → 重規劃一次 → 用新計劃繼續"""
    # 初始計劃:analyze_stock(會因 get_technical_analysis 拋錯而失敗)
    plan = '{"steps":[{"title":"分析茅臺","action":"analyze_stock","params":{"symbol":"600519"}}]}'
    replan = '{"steps":[{"title":"改為組合風險","action":"portfolio_risk"}]}'
    ai = FakeAI(multi_queue=[plan, replan, "組合風險結果"])
    stream = FakeStream()

    calls = {"tech": 0}

    def _exec(db, name, args):
        async def _inner():
            if name == "get_technical_analysis":
                calls["tech"] += 1
                raise RuntimeError("資料來源超時")
            return {
                "get_portfolio": "持倉:茅臺",
                "get_stock_suggestions": "建議",
            }.get(name, "")

        return _inner()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec))

    assert summary == "診斷完"
    # 觸發過重規劃(plan+replan+step 共 3 次 chat_multi)
    assert ai.multi_calls == 3
    # 最終計劃裡出現重規劃後的步驟且已完成
    final_steps = stream.plan_events()[-1]["steps"]
    assert any("組合風險" in s["title"] and s["status"] == "done" for s in final_steps)


def test_run_diagnosis_degrades_when_plan_generation_fails():
    """計劃生成失敗 → 回退預設計劃,仍產出彙總"""
    ai = FakeAI(multi_queue=[RuntimeError("LLM 掛了"), "預設風險評估"])
    stream = FakeStream()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec_ok))

    assert summary == "診斷完"
    plans = stream.plan_events()
    assert plans[-1]["status"] == "done"
    # 預設計劃只有組合風險一步
    assert len(plans[-1]["steps"]) == 1


def test_build_default_plan_shape():
    """預設計劃為組合風險單步"""
    plan = build_default_plan("持倉文本")
    assert plan[0]["action"] == "portfolio_risk"
