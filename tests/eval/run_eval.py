#!/usr/bin/env python3
"""Agent 過程評測入口（make eval）。

跑兩組用例：
1. structured_output 解析（純規則，無需模型，永遠執行）；
2. chat 工具迴圈（需要真實模型）——配置**只從環境變數讀取**：
   EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL
   （不讀使用者資料庫裡的 AI 服務配置；未配置則跳過並提示）。

可選 --judge：對 chat 用例的答案追加 LLM-as-judge 語義評分
（需 EVAL_JUDGE_BASE_URL / EVAL_JUDGE_API_KEY / EVAL_JUDGE_MODEL）。

門停用法：prompts/*.txt 或工具 schema 變更時跑本指令碼；
透過率低於閾值（EVAL_PASS_THRESHOLD，預設 0.9）時退出碼非 0，阻斷提交。

示例：
    make eval                                   # 只跑規則用例（未配模型時）
    EVAL_AI_BASE_URL=... EVAL_AI_API_KEY=... EVAL_AI_MODEL=... make eval
    ... make eval EVAL_ARGS="--judge --only quote-1"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 支援 `python tests/eval/run_eval.py` 直跑
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.cases.chat_cases import CHAT_CASES  # noqa: E402
from tests.eval.cases.structured_cases import (  # noqa: E402
    STRUCTURED_CASES,
    check_structured_case,
)
from tests.eval.framework import ChatEvalRunner, evaluate_case  # noqa: E402
from tests.eval.judge import JudgeConfig, LLMJudge  # noqa: E402


_LOCAL_EVAL_ENV_KEYS = {
    "EVAL_AI_BASE_URL",
    "EVAL_AI_API_KEY",
    "EVAL_AI_MODEL",
    "EVAL_JUDGE_BASE_URL",
    "EVAL_JUDGE_API_KEY",
    "EVAL_JUDGE_MODEL",
}


def load_local_eval_env() -> None:
    """載入本地 .env.eval；終端/CI 已顯式設定的值優先。"""
    env_file = REPO_ROOT / ".env.eval"
    if not env_file.is_file():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _LOCAL_EVAL_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _eval_ai_config() -> tuple[str, str, str] | None:
    """chat 用例的被測模型配置（僅環境變數，缺任一即跳過）。"""
    base_url = os.environ.get("EVAL_AI_BASE_URL", "").strip()
    api_key = os.environ.get("EVAL_AI_API_KEY", "").strip()
    model = os.environ.get("EVAL_AI_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    return base_url, api_key, model


def run_structured(only: str | None) -> tuple[int, int]:
    """跑結構化解析用例，返回 (透過數, 總數)。"""
    passed = 0
    cases = [c for c in STRUCTURED_CASES if not only or c.id == only]
    print(f"\n=== structured_output 解析用例（{len(cases)} 條，純規則）===")
    for case in cases:
        failures = check_structured_case(case)
        if failures:
            print(f"  [FAIL] {case.id}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"  [PASS] {case.id}")
    return passed, len(cases)


async def run_chat(only: str | None, use_judge: bool) -> tuple[int, int]:
    """跑 chat 工具迴圈用例，返回 (透過數, 總數)。未配模型時返回 (0, 0)。"""
    cases = [c for c in CHAT_CASES if not only or c.id == only]
    config = _eval_ai_config()
    if config is None:
        print(
            f"\n=== chat 工具迴圈用例（{len(cases)} 條）：跳過 ===\n"
            "  需要環境變數 EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL\n"
            "  （只從環境變數讀取，不使用資料庫裡的 AI 服務配置）"
        )
        return 0, 0

    base_url, api_key, model = config
    from src.platform.ai.ai_client import AIClient

    runner = ChatEvalRunner(AIClient(base_url=base_url, api_key=api_key, model=model))

    judge: LLMJudge | None = None
    if use_judge:
        judge_config = JudgeConfig.from_env()
        if judge_config is None:
            print("  [WARN] --judge 需要 EVAL_JUDGE_* 環境變數，本次跳過 judge 評分")
        else:
            judge = LLMJudge(judge_config)

    passed = 0
    print(f"\n=== chat 工具迴圈用例（{len(cases)} 條，模型: {model}）===")
    for case in cases:
        result = await runner.run_case(case)
        failures = evaluate_case(case, result)
        if failures:
            print(f"  [FAIL] {case.id}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"  [PASS] {case.id}")
        if judge is not None:
            try:
                score = await judge.judge(
                    case.question, list(case.tool_data.values()), result.answer
                )
                print(
                    f"         judge: 相關性{score.relevance} 有據性{score.groundedness} "
                    f"清晰度{score.clarity} 均分{score.mean:.1f} — {score.comment}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"         judge 評分失敗: {e}")
    return passed, len(cases)


def main() -> int:
    load_local_eval_env()
    parser = argparse.ArgumentParser(description="Agent 過程評測")
    parser.add_argument("--judge", action="store_true", help="對 chat 用例追加 LLM-as-judge 評分")
    parser.add_argument("--only", default="", help="只跑指定 id 的用例")
    args = parser.parse_args()
    only = args.only or None

    s_passed, s_total = run_structured(only)
    c_passed, c_total = asyncio.run(run_chat(only, args.judge))

    total = s_total + c_total
    passed = s_passed + c_passed
    if total == 0:
        print("\n沒有匹配的用例")
        return 1

    rate = passed / total
    threshold = float(os.environ.get("EVAL_PASS_THRESHOLD", "0.9"))
    print(f"\n=== 彙總 ===\n  透過 {passed}/{total}（{rate:.0%}），閾值 {threshold:.0%}")
    if rate < threshold:
        print("  ✗ 低於閾值，評測不透過")
        return 1
    print("  ✓ 評測透過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
