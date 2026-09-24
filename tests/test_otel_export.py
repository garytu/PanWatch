"""OTel 匯出層測試。

覆蓋三條主線:
1. 未配置 endpoint / 未裝 SDK 時,otel.py 全程 no-op —— 不拋錯、不產 span;
2. Agent 執行對映為 root span,LLM 呼叫對映為帶 GenAI 語義約定屬性的子 span,且子 span
   正確掛在 root span 之下(trace 關聯);
3. gen_ai span 回填 token 用量(複用 ai_client 已有的 usage 資料)。

用 InMemorySpanExporter 斷言,不接觸真實 endpoint。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.platform.observability import otel

# 未裝 opentelemetry SDK 時,涉及 exporter 的用例整體跳過(no-op 用例不需要 SDK)。
_otel_sdk = pytest.importorskip("opentelemetry.sdk")


@pytest.fixture
def in_memory_exporter():
    """安裝 InMemorySpanExporter(同步匯出),用例結束後重置 otel 狀態。"""
    exporter = otel.install_test_exporter()
    try:
        yield exporter
    finally:
        exporter.clear()
        otel.reset()


def _fake_openai_response(content: str, prompt_tokens: int, completion_tokens: int):
    """構造一個最小的 OpenAI ChatCompletion 回應樁。"""
    return SimpleNamespace(
        model="test-model",
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


# ---- no-op 降級 -----------------------------------------------------------

def test_未配置endpoint時init返回False且不啟用():
    """未配置 OTEL_EXPORTER_OTLP_ENDPOINT 時 init_otel 返回 False 且保持關閉。"""
    otel.reset()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        assert otel.init_otel() is False
    assert otel.is_enabled() is False
    otel.reset()


def test_關閉時span介面全部no_op不報錯():
    """OTel 關閉時,所有 span 介面均為 no-op,既不拋錯也不產 span。"""
    otel.reset()
    assert otel.is_enabled() is False
    # 上下文管理器返回 None / no-op 控制程式碼,均可安全使用
    with otel.agent_run_span("daily_report", trace_id="t-1") as span:
        assert span is None
    with otel.llm_span("gpt-x", operation="chat") as handle:
        handle.set_response(model="gpt-x", input_tokens=1, output_tokens=2)  # 不報錯
    # 遊離 span 介面
    assert otel.capture_context() is None
    s = otel.start_detached_span("x", attributes={"a": 1})
    assert s is None
    otel.set_span_attributes(s, {"b": 2})  # None 安全
    otel.end_span(s)  # None 安全


def test_關閉時ai_client正常工作無span():
    """OTel 關閉時,ai_client.chat 正常返回且不產生任何 span。"""
    otel.reset()
    from src.platform.ai.ai_client import AIClient

    client = AIClient(base_url="http://x", api_key="k", model="test-model")
    fake = _fake_openai_response("你好", 10, 5)
    with patch.object(
        client.client.chat.completions, "create", AsyncMock(return_value=fake)
    ):
        out = asyncio.run(client.chat("sys", "user"))
    assert out == "你好"
    assert client.total_tokens_used == 15


# ---- 啟用後的 span 斷言 ---------------------------------------------------

def test_agent執行對映為root_span(in_memory_exporter):
    """Agent 一次執行對映為 root span,帶 agent 名與 trace_id 屬性。"""
    with otel.agent_run_span("daily_report", trace_id="trace-123", trigger_source="schedule"):
        pass
    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    root = spans[0]
    assert root.name == "agent.run daily_report"
    assert root.parent is None  # 是 root
    assert root.attributes[otel.ATTR_AGENT_NAME] == "daily_report"
    assert root.attributes[otel.ATTR_TRACE_ID] == "trace-123"
    assert root.attributes[otel.ATTR_TRIGGER_SOURCE] == "schedule"


def test_llm呼叫產生帶genai屬性的子span(in_memory_exporter):
    """LLM 呼叫對映為 gen_ai 子 span,帶 GenAI 語義約定屬性,且掛在 root span 之下。"""
    from src.platform.ai.ai_client import AIClient

    client = AIClient(base_url="http://x", api_key="k", model="test-model")
    fake = _fake_openai_response("分析結果", 100, 40)

    async def _call():
        with otel.agent_run_span("daily_report", trace_id="trace-abc"):
            with patch.object(
                client.client.chat.completions, "create", AsyncMock(return_value=fake)
            ):
                return await client.chat("sys", "user")

    out = asyncio.run(_call())

    assert out == "分析結果"
    spans = in_memory_exporter.get_finished_spans()
    # 子 span 先結束、root 後結束
    assert len(spans) == 2
    llm = next(s for s in spans if s.name.startswith("chat"))
    root = next(s for s in spans if s.name.startswith("agent.run"))

    # GenAI 語義約定屬性
    assert llm.attributes[otel.GEN_AI_SYSTEM] == "openai"
    assert llm.attributes[otel.GEN_AI_OPERATION_NAME] == "chat"
    assert llm.attributes[otel.GEN_AI_REQUEST_MODEL] == "test-model"
    assert llm.attributes[otel.GEN_AI_USAGE_INPUT_TOKENS] == 100
    assert llm.attributes[otel.GEN_AI_USAGE_OUTPUT_TOKENS] == 40

    # 子 span 掛在 root span 之下(同一 trace)
    assert llm.parent is not None
    assert llm.parent.span_id == root.context.span_id
    assert llm.context.trace_id == root.context.trace_id


def test_遊離span可掛到捕獲的父上下文(in_memory_exporter):
    """start_detached_span 用捕獲的父上下文,可把節點 span 掛到 root span 下(模擬跨執行緒)。"""
    with otel.agent_run_span("tradingagents", trace_id="ta-1"):
        parent_ctx = otel.capture_context()
    # 在 root span 結束後,用捕獲的上下文仍能建立父子關係(模擬 to_thread 場景)
    span = otel.start_detached_span(
        "tradingagents.stage market_analyst",
        parent_context=parent_ctx,
        attributes={otel.ATTR_TA_STAGE: "market_analyst"},
    )
    otel.end_span(span)

    spans = in_memory_exporter.get_finished_spans()
    root = next(s for s in spans if s.name.startswith("agent.run"))
    stage = next(s for s in spans if s.name.startswith("tradingagents.stage"))
    assert stage.attributes[otel.ATTR_TA_STAGE] == "market_analyst"
    assert stage.parent is not None
    assert stage.parent.span_id == root.context.span_id
