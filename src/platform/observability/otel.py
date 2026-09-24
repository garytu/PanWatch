"""OpenTelemetry 匯出層(可選,預設關閉)。

在**不改動** PanWatch 自建可觀測體系(``log_context`` / ``agent_runs`` /
``tradingagents.observability``)的前提下,額外掛一層標準 OTel 匯出,讓"自建 + 標準棧"
都能拿到實證。三類橋接:

- Agent 一次執行        -> root span(複用 ``agent_runs`` 的 ``trace_id`` 作關聯)
- 單次 LLM 呼叫         -> gen_ai 子 span(複用 ``ai_client`` 已有的 token 用量)
- TradingAgents 節點    -> 子 span(複用 ``observability.py`` 的節點/LLM 事件)

設計原則(生產專案,增量可回退):

1. **預設零副作用**:未配置 ``OTEL_EXPORTER_OTLP_ENDPOINT``,或未安裝 opentelemetry
   SDK 時,``init_otel()`` 直接返回 False,後續所有 span 介面降級為 no-op —— 不拋錯、
   不引入執行時依賴、不改變任何既有行為。
2. **薄橋接**:只在既有埋點處包一層 context manager;埋點本身不感知 OTel 細節。
3. **懶載入**:本模組頂層**不** import opentelemetry,只有 ``init_otel()`` 被呼叫且
   endpoint 已配置時才嘗試匯入,因此 ``import src.platform.observability.otel`` 永遠安全、零成本。

GenAI 語義約定(OpenTelemetry Semantic Conventions for Generative AI)讓 span 能被
Jaeger / Tempo / Langfuse(OTLP)等標準 APM 直接識別為"一次模型呼叫"。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ---- GenAI 語義約定屬性名 -------------------------------------------------
# 參考: OpenTelemetry Semantic Conventions for Generative AI
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# PanWatch 自定義屬性(橋接自建 trace 模型,便於在 APM 裡與 agent_runs 對齊)
ATTR_AGENT_NAME = "panwatch.agent.name"
ATTR_TRACE_ID = "panwatch.trace_id"
ATTR_TRIGGER_SOURCE = "panwatch.trigger_source"
ATTR_TA_STAGE = "panwatch.tradingagents.stage"

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "panwatch")
_INSTRUMENTATION_SCOPE = "panwatch.otel"

# 模組級狀態(單程式內單例)
_enabled: bool = False
_initialized: bool = False
_provider: Any = None
_tracer: Any = None


def is_enabled() -> bool:
    """OTel 匯出當前是否已啟用(endpoint 已配置且 SDK 可用且初始化成功)。"""
    return _enabled


def _import_sdk():
    """嘗試匯入 OTel SDK。未安裝則返回 None(優雅降級)。"""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        return trace, Resource, TracerProvider, BatchSpanProcessor
    except Exception:  # pragma: no cover - 僅在未裝 SDK 時命中
        return None


def _build_otlp_exporter():
    """構造 OTLP span exporter。

    優先 HTTP(``proto/http``,埠約定 4318),回退 gRPC(``proto/grpc``,4317)。
    兩者都會自動讀取 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 等標準環境變數,因此這裡不顯式
    傳 endpoint,交給 SDK 按標準約定解析(最少驚訝原則)。
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter()
    except Exception:
        pass
    try:  # pragma: no cover - 環境相關
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcOTLPSpanExporter,
        )

        return GrpcOTLPSpanExporter()
    except Exception:
        return None


def init_otel(*, force: bool = False) -> bool:
    """從環境變數初始化 OTel 匯出。冪等;返回是否成功啟用。

    僅當 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 非空**且** opentelemetry SDK/exporter 均可
    匯入時才真正啟用;任一缺失都靜默降級為 no-op(不影響現有部署)。
    """
    global _enabled, _initialized, _provider, _tracer

    if _initialized and not force:
        return _enabled

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        # 未配置 endpoint —— 預設關閉,零副作用。
        _initialized = True
        _enabled = False
        return False

    mods = _import_sdk()
    if mods is None:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT 已配置(%s),但未安裝 opentelemetry SDK,"
            "OTel 匯出跳過。安裝: pip install -r requirements-otel.txt",
            endpoint,
        )
        _initialized = True
        _enabled = False
        return False

    trace, Resource, TracerProvider, BatchSpanProcessor = mods
    exporter = _build_otlp_exporter()
    if exporter is None:
        logger.warning(
            "opentelemetry SDK 已裝但缺少 OTLP exporter,OTel 匯出跳過。"
            "安裝: pip install -r requirements-otel.txt"
        )
        _initialized = True
        _enabled = False
        return False

    try:
        resource = Resource.create({"service.name": _SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        # 設為全域性 provider(供上下文傳播);span 建立仍走本模組持有的 tracer。
        trace.set_tracer_provider(provider)
        _provider = provider
        _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
        _enabled = True
        _initialized = True
        logger.info("OTel 匯出已啟用,endpoint=%s service=%s", endpoint, _SERVICE_NAME)
        return True
    except Exception as e:  # pragma: no cover - 初始化異常兜底
        logger.warning("OTel 初始化失敗,降級為 no-op: %s", e)
        _enabled = False
        _initialized = True
        return False


# ---- 供測試:用 InMemorySpanExporter 同步匯出 -----------------------------

def install_test_exporter():
    """測試專用:重置並安裝 InMemorySpanExporter(SimpleSpanProcessor 同步匯出)。

    返回 exporter 例項,可直接 ``get_finished_spans()`` 斷言。生產程式碼不應呼叫。
    """
    global _enabled, _initialized, _provider, _tracer

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # 測試內可能重複安裝:直接覆蓋本模組持有的 provider/tracer;
    # 全域性 provider 只在首次設定(OTel 不允許覆蓋,重複設定會告警),故不強設全域性。
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _provider = provider
    _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
    _enabled = True
    _initialized = True
    return exporter


def reset() -> None:
    """重置模組狀態(測試 teardown 用)。"""
    global _enabled, _initialized, _provider, _tracer
    _enabled = False
    _initialized = False
    _provider = None
    _tracer = None


# ---- span 介面(全部在關閉時 no-op) --------------------------------------

@contextmanager
def agent_run_span(
    agent_name: str,
    trace_id: str = "",
    trigger_source: str = "",
) -> Iterator[Any]:
    """一次 Agent 執行的 root span。關閉時 no-op(yield None)。

    複用 ``agent_runs`` 的 ``trace_id`` 作為 span 屬性,方便在 APM 裡與 run 表對齊。
    """
    if not _enabled or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(f"agent.run {agent_name}") as span:
        try:
            span.set_attribute(ATTR_AGENT_NAME, agent_name)
            if trace_id:
                span.set_attribute(ATTR_TRACE_ID, trace_id)
            if trigger_source:
                span.set_attribute(ATTR_TRIGGER_SOURCE, trigger_source)
        except Exception:
            pass
        yield span


class _LLMSpan:
    """gen_ai span 的薄控制程式碼:呼叫返回後回填 token 用量/回應模型。"""

    __slots__ = ("_span",)

    def __init__(self, span: Any):
        self._span = span

    def set_response(
        self,
        *,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        if self._span is None:
            return
        try:
            if model:
                self._span.set_attribute(GEN_AI_RESPONSE_MODEL, model)
            if input_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
            if output_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
        except Exception:
            pass


@contextmanager
def llm_span(
    model: str,
    *,
    system: str = "openai",
    operation: str = "chat",
) -> Iterator[_LLMSpan]:
    """單次 LLM 呼叫的 gen_ai 子 span。關閉時 yield 一個 no-op 控制程式碼。

    span 名遵循 GenAI 約定 ``{operation} {model}``;請求側屬性在進入時寫入,回應側
    (token/回應模型)由呼叫方拿到 usage 後透過返回控制程式碼回填。
    """
    if not _enabled or _tracer is None:
        yield _LLMSpan(None)
        return
    span_name = f"{operation} {model}".strip() if model else operation
    with _tracer.start_as_current_span(span_name) as span:
        try:
            span.set_attribute(GEN_AI_SYSTEM, system)
            span.set_attribute(GEN_AI_OPERATION_NAME, operation)
            if model:
                span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        except Exception:
            pass
        yield _LLMSpan(span)


def capture_context() -> Any:
    """捕獲當前 OTel 上下文(供跨執行緒傳播 root span 關係)。關閉時返回 None。

    TradingAgents 在 ``asyncio.to_thread`` 裡同步執行,OTel 上下文不會自動跨執行緒,
    需在非同步側捕獲、在工作執行緒側顯式作為 parent 傳入。
    """
    if not _enabled:
        return None
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:
        return None


def start_detached_span(
    name: str,
    *,
    parent_context: Any = None,
    attributes: Optional[dict] = None,
) -> Any:
    """啟動一個"遊離" span(不設為 current,需手動 ``end``)。關閉時返回 None。

    用於 callback 式埋點(如 TradingAgents 節點)——start/end 分處兩次回撥、且可能
    執行在工作執行緒,無法用 with 語法。傳入 ``capture_context()`` 的結果作為 parent
    以掛到 root span 下。
    """
    if not _enabled or _tracer is None:
        return None
    try:
        span = _tracer.start_span(name, context=parent_context)
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass
        return span
    except Exception:
        return None


def set_span_attributes(span: Any, attributes: dict) -> None:
    """給遊離 span 補屬性。span 為 None 時 no-op。"""
    if span is None:
        return
    for k, v in attributes.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            pass


def end_span(span: Any) -> None:
    """結束一個遊離 span。span 為 None 時 no-op。"""
    if span is None:
        return
    try:
        span.end()
    except Exception:
        pass
