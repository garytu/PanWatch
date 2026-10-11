import asyncio

import pytest

from pan_agent import (
    ContextBudget,
    ContextCompressionMode,
    ContextEngine,
    ContextSummary,
    ModelMessage,
    estimate_tokens,
)


def test_estimate_tokens_is_explicitly_approximate_and_nonzero():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_context_budget_rejects_inverted_thresholds():
    with pytest.raises(ValueError, match="soft_limit"):
        ContextBudget(soft_limit_tokens=9000, hard_limit_tokens=8000)


def test_context_engine_compacts_older_messages_and_preserves_recent_window():
    messages = [
        ModelMessage(role="system", content="trusted instructions"),
        *[
                ModelMessage(role="user", content=f"歷史問題 {index} " + "x" * 900)
            for index in range(5)
        ],
        ModelMessage(role="user", content="當前問題"),
    ]
    result = asyncio.run(
        ContextEngine().prepare(
            messages,
            budget=ContextBudget(
                max_tokens=400,
                soft_limit_tokens=128,
                hard_limit_tokens=256,
                keep_recent_messages=2,
            ),
        )
    )

    assert result.compressed is True
    assert result.compressed_message_count == 4
    assert result.messages[0].content == "trusted instructions"
    assert result.messages[-1].content == "當前問題"
    assert result.summary is not None


def test_context_engine_uses_existing_summary_without_compressing_small_history():
    summary = ContextSummary(goal=["保留目標"])
    result = asyncio.run(
        ContextEngine().prepare(
            [ModelMessage(role="user", content="短問題")],
            existing_summary=summary,
            page_context="來自詳細資訊頁",
            budget=ContextBudget(max_tokens=400, soft_limit_tokens=200, hard_limit_tokens=300),
        )
    )

    assert result.compressed is False
    assert any("保留目標" in message.content for message in result.messages)
    assert any("來自詳細資訊頁" in message.content for message in result.messages)
    assert result.usage_after.total_tokens > result.usage_before.total_tokens


def test_context_engine_falls_back_when_model_summarizer_fails():
    class BrokenSummarizer:
        async def summarize(self, messages, *, mode):
            raise RuntimeError("provider unavailable")

    result = asyncio.run(
        ContextEngine(BrokenSummarizer()).prepare(
            [
                *[
                    ModelMessage(role="user", content=f"目標是保留當前篩選條件 {index} " + "x" * 500)
                    for index in range(4)
                ],
                ModelMessage(role="user", content="現在繼續"),
            ],
            budget=ContextBudget(max_tokens=400, soft_limit_tokens=128, hard_limit_tokens=256, keep_recent_messages=1),
            mode=ContextCompressionMode.PRESERVE_DETAILS,
        )
    )

    assert result.compressed is True
    assert result.summary is not None
    assert result.summary.goal[0].startswith("目標是保留當前篩選條件")


def test_context_engine_keeps_original_history_when_summary_has_no_gain():
    messages = [
        ModelMessage(role="user", content="很短的歷史"),
        ModelMessage(role="user", content="當前問題"),
    ]
    result = asyncio.run(
        ContextEngine().prepare(
            messages,
            force_compress=True,
            budget=ContextBudget(
                max_tokens=400,
                soft_limit_tokens=128,
                hard_limit_tokens=256,
                keep_recent_messages=1,
            ),
        )
    )

    assert result.compressed is False
    assert result.compression_status == "no_gain"
    assert result.messages == messages
    assert result.usage_after.total_tokens == result.usage_before.total_tokens


def test_context_usage_includes_tool_definition_estimate():
    usage = ContextEngine().measure(
        [ModelMessage(role="user", content="查詢")],
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "get_quote",
                    "description": "查詢行情並返回最新價格",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    tool_section = next(section for section in usage.sections if section.name == "tool_definitions")
    assert tool_section.tokens > 0
