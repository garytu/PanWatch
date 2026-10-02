"""Agent 過程評測框架：用例結構、執行器與規則斷言引擎。

用例 = 固定輸入（問題 + mock 工具資料）→ 規則斷言：
- 工具選擇正確（該調的調了、不該調的沒調、閒聊不調）；
- 工具引數正確；
- 動作在白名單內（只允許 CHAT_TOOLS 註冊的只讀工具）；
- 答案引用了工具結果（有據性：mock 資料裡的關鍵值必須出現在答案中）；
- 工具失敗時優雅降級（不編造無據數值）。

規則斷言優先；語義維度（相關性/清晰度）由 judge.py 的 LLM-as-judge 補充。
每個線上 bad case 修復後應固化為一條新用例（加進 cases/chat_cases.py）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.modules.assistant.chat_api import SYSTEM_PROMPT
from src.modules.assistant.legacy_chat_tools import CHAT_TOOLS

# 動作白名單：chat agent 只允許呼叫這些只讀工具
TOOL_WHITELIST = {t["function"]["name"] for t in CHAT_TOOLS}
MAX_TOOL_ROUNDS = 5

# 用例未提供某工具 mock 資料時的預設返回（模擬工具失敗）
DEFAULT_TOOL_MISSING = "工具執行出錯: eval 用例未提供該工具的 mock 資料"


@dataclass
class ChatEvalCase:
    """一條 chat 工具迴圈評測用例。"""

    id: str
    question: str
    # 工具名 → mock 返回文本（工具失敗場景直接給"工具執行出錯: ..."文案）
    tool_data: dict[str, str] = field(default_factory=dict)
    # 必須呼叫的工具（子集斷言，不要求順序）
    expected_tools: tuple[str, ...] = ()
    # 明確不應呼叫的工具
    forbidden_tools: tuple[str, ...] = ()
    # 閒聊/概念題：完全不應呼叫任何工具
    expect_no_tools: bool = False
    # 工具名 → {引數名: 期望值或校驗函式}；同名多次呼叫時任一命中即透過
    param_checks: dict[str, dict] = field(default_factory=dict)
    # 有據性：答案必須包含的關鍵值（全部命中才透過）
    answer_must_contain: tuple[str, ...] = ()
    # 答案必須包含其中任意一個（如失敗場景的"失敗/無法/未能"類表述）
    answer_must_contain_any: tuple[str, ...] = ()
    # 答案不得包含（如工具失敗時不得出現編造的具體數值）
    answer_must_not_contain: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class ChatEvalResult:
    """一次用例執行的過程記錄。"""

    case_id: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    answer: str = ""
    error: str = ""


class ChatEvalRunner:
    """驅動 chat 工具迴圈跑一條評測用例（工具執行被 mock 資料替代）。

    ai_client 需實現 `chat_with_tools(messages, tools, temperature) -> message`
    （與 src.platform.ai.ai_client.AIClient 一致）：
    - make eval 時注入真實 AIClient（配置從環境變數讀取，見 run_eval.py）；
    - 單測裡注入指令碼化的假使用者端，不發任何真實請求。
    """

    def __init__(self, ai_client, temperature: float = 0.0):
        self.ai_client = ai_client
        # 評測用低溫，儘量減少非確定性
        self.temperature = temperature

    async def run_case(self, case: ChatEvalCase) -> ChatEvalResult:
        result = ChatEvalResult(case_id=case.id)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case.question},
        ]
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                msg = await self.ai_client.chat_with_tools(
                    messages, tools=CHAT_TOOLS, temperature=self.temperature
                )
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    result.answer = getattr(msg, "content", "") or ""
                    break

                messages.append({
                    "role": "assistant",
                    "content": getattr(msg, "content", None),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    result.tool_calls.append((tc.function.name, args))
                    tool_result = case.tool_data.get(tc.function.name, DEFAULT_TOOL_MISSING)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })
            else:
                result.error = "超過最大工具輪次仍未給出回答"
        except Exception as e:  # noqa: BLE001 - 評測記錄任何執行異常
            result.error = f"執行異常: {e}"
        return result


def _param_match(actual, expected) -> bool:
    """引數斷言：expected 可為期望值或校驗函式。"""
    if callable(expected):
        try:
            return bool(expected(actual))
        except Exception:
            return False
    return str(actual or "").strip() == str(expected)


def evaluate_case(case: ChatEvalCase, result: ChatEvalResult) -> list[str]:
    """對一次執行做規則斷言，返回失敗原因列表（空列表即透過）。"""
    failures: list[str] = []
    if result.error:
        failures.append(result.error)

    called = [name for name, _ in result.tool_calls]
    called_set = set(called)

    # 1) 動作白名單：呼叫了未註冊的工具直接失敗
    for name in sorted(called_set - TOOL_WHITELIST):
        failures.append(f"呼叫了白名單外的工具: {name}")

    # 2) 工具選擇
    if case.expect_no_tools and called:
        failures.append(f"不該呼叫工具卻呼叫了: {called}")
    for name in case.expected_tools:
        if name not in called_set:
            failures.append(f"缺少必需的工具呼叫: {name}")
    for name in case.forbidden_tools:
        if name in called_set:
            failures.append(f"呼叫了不該呼叫的工具: {name}")

    # 3) 工具引數
    for tool_name, expects in (case.param_checks or {}).items():
        calls = [args for name, args in result.tool_calls if name == tool_name]
        if not calls:
            continue  # 缺呼叫已在上面報過
        matched = any(
            all(_param_match(args.get(k), v) for k, v in expects.items())
            for args in calls
        )
        if not matched:
            expect_desc = {k: (v if not callable(v) else "<校驗函式>") for k, v in expects.items()}
            failures.append(f"{tool_name} 引數不符合預期 {expect_desc}，實際 {calls}")

    # 4) 有據性 / 內容約束
    answer = result.answer or ""
    for token in case.answer_must_contain:
        if token not in answer:
            failures.append(f"答案缺少工具結果引用: {token!r}")
    if case.answer_must_contain_any and not any(
        token in answer for token in case.answer_must_contain_any
    ):
        failures.append(f"答案未包含任一預期表述: {case.answer_must_contain_any}")
    for token in case.answer_must_not_contain:
        if token in answer:
            failures.append(f"答案包含不應出現的內容: {token!r}")

    return failures
