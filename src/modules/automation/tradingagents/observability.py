"""TradingAgents 進度回撥。

走一個統一回呼鏈:
1. LangChain `BaseCallbackHandler`:捕獲 LangGraph 節點、LLM 和工具的真實生命週期
2. `agent.py` 將同一個 handler 注入 `Propagator.get_graph_args(callbacks=...)`，不依賴 debug 文本解析

進度寫入 PanWatch 的 `log_context`,前端輪詢 `/api/agents/runs/{trace_id}/progress`
聚合返回階段；同一檔案下半部提供成本提取、預算檢查和估算入口。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

from src.platform.observability import otel
from src.platform.observability.log_context import log_context
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AnalysisHistory

logger = logging.getLogger(__name__)

# 進度和預算共用同一套 TradingAgents 執行觀測入口；資料庫生命週期仍由 agent_runs 負責。
__all__ = [
    "STAGES_ORDER",
    "PanWatchProgressHandler",
    "aggregate_progress",
    "check_budget",
    "estimate_cost",
    "get_today_cache_key",
]


# 預設階段對映:TradingAgents 4 個 analyst + 辯論 + 風控 + PM
STAGES_ORDER = [
    "data_collection",
    "market_analyst",
    "social_analyst",
    "news_analyst",
    "fundamentals_analyst",
    "bull_bear_debate",
    "research_manager",
    "trader",
    "risk_judge",
    "final_decision",
]

# TradingAgents 0.5.0 的 LangGraph 節點名不是介面階段名的一一對映。
# 這裡集中維護別名，而不是在每個 callback 分支裡散落字串判斷；上游節點改名時只需改這一張表。
NODE_STAGE_ALIASES = {
    "market_analyst": "market_analyst",
    "sentiment_analyst": "social_analyst",
    "social_analyst": "social_analyst",
    "news_analyst": "news_analyst",
    "fundamentals_analyst": "fundamentals_analyst",
    "bull_researcher": "bull_bear_debate",
    "bear_researcher": "bull_bear_debate",
    "research_manager": "research_manager",
    "trader": "trader",
    "aggressive_analyst": "risk_judge",
    "conservative_analyst": "risk_judge",
    "neutral_analyst": "risk_judge",
    "risk_judge": "risk_judge",
    "portfolio_manager": "final_decision",
    "final_decision": "final_decision",
}


try:
    from langchain_core.callbacks import BaseCallbackHandler as _LCBaseCallbackHandler
    _LANGCHAIN_AVAILABLE = True
except ImportError:  # tradingagents 未裝時仍允許 import 本模組,測試不依賴
    _LANGCHAIN_AVAILABLE = False

    class _LCBaseCallbackHandler:  # type: ignore[no-redef]
        """Fallback stub when langchain_core 未安裝。"""
        pass


class PanWatchProgressHandler(_LCBaseCallbackHandler):
    """LangChain BaseCallbackHandler 相容的進度處理器。

    新版 langchain (1.x) 把 callbacks 欄位用 pydantic 校驗為 BaseCallbackHandler 例項,
    所以必須繼承上游基類才能被接受。

    覆蓋核心 hook:
    - on_llm_start: 某個 LLM 呼叫開始(可推斷當前在哪個 analyst)
    - on_llm_end: LLM 呼叫結束,帶成本
    - on_chain_start/end: LangGraph 節點切換

    P0 簡單實現:把所有事件都 logger.info 出來,帶 trace_id 標籤。
    前端透過過濾 log_entries 表的 trace_id + event=ta_progress 拿到時間線。
    """

    def __init__(
        self,
        trace_id: str,
        agent_name: str = "tradingagents",
        cancel_event: threading.Event | None = None,
    ):
        # langchain_core BaseCallbackHandler 沒有 __init__ 引數,直接 super 安全
        try:
            super().__init__()
        except TypeError:
            # 某些版本要求無參,某些要求帶參,兜底
            pass
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.cancel_event = cancel_event
        self._started_at = time.monotonic()
        self._total_cost = 0.0
        self._completed_stages: set[str] = set()
        # LangChain 1.x 的 on_chain_end 不保證攜帶 name/metadata，因此必須儲存
        # start 時的 run_id -> 節點資訊，才能把結束事件關回正確階段。
        self._chain_runs: dict[str, dict[str, str]] = {}
        self._llm_runs: dict[str, dict[str, str]] = {}
        self._tool_runs: dict[str, dict[str, str]] = {}
        # OTel 橋接:handler 在非同步側構造(to_thread 之前),此處捕獲當前上下文,
        # 供工作執行緒裡的 callback 把節點/LLM 子 span 掛到 root span 下(關閉時為 None)。
        self._otel_parent = otel.capture_context()
        self._otel_stage_spans: dict[str, Any] = {}
        self._otel_llm_span: Any = None

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self._started_at

    def _emit(self, stage: str, action: str, **extra):
        """寫一條進度日誌。前端按 trace_id + event=ta_progress 拉。"""
        if self.cancel_event is not None and self.cancel_event.is_set():
            return
        with log_context(
            trace_id=self.trace_id,
            agent_name=self.agent_name,
            event="ta_progress",
            tags={
                "stage": stage,
                "action": action,
                "elapsed_sec": round(self.elapsed_sec, 2),
                "total_cost_usd": round(self._total_cost, 6),
                **extra,
            },
        ):
            agent = extra.get("agent") or extra.get("langgraph_node") or ""
            detail = f" agent={agent}" if agent else ""
            logger.info(f"[TA進度] stage={stage} action={action}{detail} {extra}")

    def emit(self, stage: str, action: str, **extra) -> None:
        """向採集等非 LangChain 階段發出同一格式的進度事件。"""
        self._emit(stage, action, **extra)

    # ---- LangChain callbacks 介面 ----

    # 關鍵:LLM 預設按 token 估算成本(deepseek-chat 單價),後續可由呼叫方注入更精確單價
    _PRICE_PER_M_PROMPT = 0.14
    _PRICE_PER_M_COMPLETION = 0.28

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_call_count = getattr(self, "_llm_call_count", 0) + 1
        model = ""
        try:
            model = (
                (kwargs.get("invocation_params") or {}).get("model")
                or (serialized or {}).get("name")
                or ""
            )
        except Exception:
            model = ""
        agent = _callback_agent(kwargs, self._chain_runs)
        operation_id = str(kwargs.get("run_id") or f"llm:{self._llm_call_count}")
        self._llm_runs[operation_id] = {"agent": agent, "model": model}
        self._emit(
            "llm_call",
            "llm_start",
            call_n=self._llm_call_count,
            model=model,
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel:TA 的一次 LLM 呼叫 -> gen_ai 子 span(遵循 GenAI 語義約定)。
        self._otel_llm_span = otel.start_detached_span(
            f"chat {model}".strip() if model else "chat",
            parent_context=self._otel_parent,
            attributes={
                otel.GEN_AI_SYSTEM: "tradingagents",
                otel.GEN_AI_OPERATION_NAME: "chat",
                **({otel.GEN_AI_REQUEST_MODEL: model} if model else {}),
            },
        )

    def on_llm_end(self, response, **kwargs):
        # langchain LLMResult.llm_output 含 token_usage
        usage = {}
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
        except Exception:
            pass
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        # 累加成本估算
        cost = (
            prompt_tokens / 1_000_000 * self._PRICE_PER_M_PROMPT
            + completion_tokens / 1_000_000 * self._PRICE_PER_M_COMPLETION
        )
        self.record_cost(cost)
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._llm_runs.pop(operation_id, {})
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "llm_end",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            call_cost=round(cost, 6),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel:回填 token 用量並結束 gen_ai span。
        if self._otel_llm_span is not None:
            otel.set_span_attributes(
                self._otel_llm_span,
                {
                    otel.GEN_AI_USAGE_INPUT_TOKENS: int(prompt_tokens),
                    otel.GEN_AI_USAGE_OUTPUT_TOKENS: int(completion_tokens),
                },
            )
            otel.end_span(self._otel_llm_span)
            self._otel_llm_span = None

    def on_chain_start(self, serialized, inputs, **kwargs):
        # LangGraph 節點切換。節點名優先取 kwargs.name/metadata.langgraph_node，
        # 因為 serialized 在不同 LangChain 版本里可能只有 runnable 型別名稱。
        name = _callback_name(serialized, kwargs)
        stage = _normalize_stage(name)
        if not stage:
            return
        run_id = _run_id(kwargs)
        if run_id:
            self._chain_runs[run_id] = {
                "name": name,
                "stage": stage,
                "parent_run_id": _parent_run_id(kwargs),
            }
        self._emit(
            stage,
            "stage_start",
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=_parent_run_id(kwargs),
        )
        # OTel 節點 span 只保留一個當前階段，重複的並行/重試節點仍會產生進度事件，
        # 但不會因為重複 span 讓追蹤樹無限膨脹。
        if stage not in self._otel_stage_spans:
            span = otel.start_detached_span(
                f"tradingagents.stage {stage}",
                parent_context=self._otel_parent,
                attributes={
                    otel.ATTR_TA_STAGE: stage,
                    otel.ATTR_AGENT_NAME: self.agent_name,
                },
            )
            if span is not None:
                self._otel_stage_spans[stage] = span

    def on_chain_end(self, outputs, **kwargs):
        self._finish_chain("stage_end", kwargs)

    def on_llm_error(self, error, **kwargs):
        self._emit(
            "llm_call",
            "llm_error",
            error=str(error)[:200],
            operation_id=str(kwargs.get("run_id") or ""),
        )
        self._emit("error", "llm_error", error=str(error)[:200])

    def on_chain_error(self, error, **kwargs):
        self._finish_chain("stage_error", kwargs, error=str(error)[:200])
        self._emit("error", "chain_error", error=str(error)[:200], run_id=_run_id(kwargs))

    def on_tool_start(self, serialized, input_str, **kwargs):
        """記錄 LangGraph ToolNode 當前正在執行的工具。"""
        name = ""
        try:
            name = kwargs.get("name") or (serialized or {}).get("name") or "unknown"
        except Exception:
            name = kwargs.get("name") or "unknown"
        operation_id = str(kwargs.get("run_id") or f"tool:{name}")
        agent = _callback_agent(kwargs, self._chain_runs)
        self._tool_runs[operation_id] = {"agent": agent, "tool": str(name)}
        self._emit(
            "llm_call",
            "tool_start",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_end(self, output, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_end",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_error(self, error, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_error",
            tool=str(name),
            error=str(error)[:200],
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    # ---- 公共方法 ----

    def record_cost(self, usd: float) -> None:
        self._total_cost += usd

    def _guess_stage(self, serialized: dict, kwargs: dict) -> str:
        name = _callback_name(serialized, kwargs) or "unknown"
        return _normalize_stage(name) or "unknown"

    def _finish_chain(self, action: str, kwargs: dict, **extra: Any) -> None:
        """按 run_id 找回節點併發出結束事件；上游未攜帶節點名時也能正確閉環。"""
        run_id = _run_id(kwargs)
        record = self._chain_runs.pop(run_id, None) if run_id else None
        name = (record or {}).get("name") or _callback_name(None, kwargs)
        stage = (record or {}).get("stage") or _normalize_stage(name)
        if not stage:
            return
        self._completed_stages.add(stage)
        self._emit(
            stage,
            action,
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=(record or {}).get("parent_run_id") or _parent_run_id(kwargs),
            **extra,
        )
        if action in {"stage_end", "stage_error"}:
            span = self._otel_stage_spans.pop(stage, None)
            if span is not None:
                otel.end_span(span)


def _normalize_stage(name: str) -> str:
    """把 LangGraph 節點名標準化到 STAGES_ORDER 裡的一個值。"""
    n = "_".join(str(name or "").strip().lower().replace("-", " ").split())
    if not n:
        return ""
    if n in NODE_STAGE_ALIASES:
        return NODE_STAGE_ALIASES[n]
    for stage in STAGES_ORDER:
        if stage in n:
            return stage
    return ""


def _callback_name(serialized: Any, kwargs: dict[str, Any]) -> str:
    """相容 LangChain callback 的 name/metadata/serialized 三種節點來源。"""
    metadata = kwargs.get("metadata") or {}
    return str(
        kwargs.get("name")
        or metadata.get("langgraph_node")
        or (serialized or {}).get("name", "")
        or ""
    ).strip()


def _run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("run_id") or "")


def _parent_run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("parent_run_id") or "")


def _callback_agent(kwargs: dict[str, Any], chain_runs: dict[str, dict[str, str]]) -> str:
    metadata = kwargs.get("metadata") or {}
    agent = str(metadata.get("langgraph_node") or kwargs.get("name") or "").strip()
    if agent:
        return agent
    parent = chain_runs.get(_parent_run_id(kwargs))
    return str((parent or {}).get("name") or "")


def aggregate_progress(log_entries: list[dict]) -> dict:
    """讀 log_entries 表裡 event=ta_progress 的記錄,聚合成階段進度。

    log_entries 行結構(參考 src/web/log_handler.py):
    {timestamp, level, logger_name, message, trace_id, agent_name, event, tags, ...}
    tags 是 dict,含 stage / action / elapsed_sec / total_cost_usd 等。

    返回結構(給前端):
    {
        "current_stage": "bull_bear_debate",
        "completed_stages": [...],
        "started_at": ...,
        "elapsed_sec": 123.4,
        "total_cost_usd": 0.018,
        "stages": [
            {"name": "market_analyst", "status": "done", "duration_sec": 12.3, "cost_usd": 0.004},
            ...
        ]
    }
    """
    stage_state: dict[str, dict] = {s: {"name": s, "status": "pending"} for s in STAGES_ORDER}
    total_cost = 0.0
    current_stage = None
    active_operations: dict[str, dict] = {}
    started_at = None
    collection_sources: dict[str, dict] = {}

    for entry in log_entries:
        tags = entry.get("tags") or {}
        stage = tags.get("stage")
        action = tags.get("action") or ""
        source = tags.get("source")
        ts = entry.get("timestamp")
        if started_at is None and ts:
            started_at = ts

        if not stage or stage not in stage_state:
            # LLM/工具事件不屬於獨立階段，但需要保留當前活動操作，
            # 這樣外部資料請求卡住時 UI 能顯示具體工具名。
            if stage == "llm_call":
                kind = "tool" if action.startswith("tool_") else "llm"
                name = tags.get("tool") if kind == "tool" else tags.get("model")
                operation_id = str(tags.get("operation_id") or f"{kind}:{name or action}")
                agent = str(tags.get("agent") or tags.get("langgraph_node") or "")
                if action == "llm_start":
                    operation = {"kind": "llm", "name": tags.get("model") or "LLM 呼叫"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action == "tool_start":
                    operation = {"kind": "tool", "name": tags.get("tool") or "工具呼叫"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action in {"llm_end", "tool_end", "llm_error", "tool_error"}:
                    if tags.get("operation_id"):
                        active_operations.pop(operation_id, None)
                    else:
                        # 相容舊日誌/上游未傳 run_id 的回撥：只移除同型別同名稱
                        # 的一個操作，不影響並行執行的其它工具。
                        expected_name = name or ("工具呼叫" if kind == "tool" else "LLM 呼叫")
                        for key, operation in list(active_operations.items()):
                            if operation["kind"] == kind and operation["name"] == expected_name:
                                active_operations.pop(key, None)
                                break
            continue

        if stage == "data_collection" and source:
            source_state = collection_sources.setdefault(
                source,
                {"name": source, "status": "pending"},
            )
            if action == "source_start":
                source_state["status"] = "running"
            elif action == "source_end":
                source_state["status"] = "done"
            elif action == "source_error":
                source_state["status"] = "error"
                if tags.get("error"):
                    source_state["error"] = str(tags["error"])[:200]

        # cost 累積取最後一條的 total_cost_usd
        cost = tags.get("total_cost_usd")
        if cost is not None:
            total_cost = max(total_cost, float(cost))

        if action == "stage_start":
            stage_state[stage]["status"] = "running"
            stage_state[stage]["started_at"] = ts
            current_stage = stage
        elif action == "stage_end":
            stage_state[stage]["status"] = "done"
            if "started_at" in stage_state[stage] and ts:
                # 簡略時長(實際 ts 是 datetime,這裡依賴呼叫方轉換)
                pass

    return {
        "current_stage": current_stage,
        "completed_stages": [s for s, v in stage_state.items() if v["status"] == "done"],
        "started_at": started_at,
        "elapsed_sec": float(log_entries[-1].get("tags", {}).get("elapsed_sec", 0))
        if log_entries
        else 0,
        "total_cost_usd": round(total_cost, 6),
        "active_operation": next(reversed(active_operations.values()), None)
        if active_operations
        else None,
        "stages": [stage_state[s] for s in STAGES_ORDER],
        "data_sources": list(collection_sources.values()),
    }


# ============================================================================
# Cost and budget tracking
# ============================================================================


def check_budget(monthly_budget_usd: float, agent_name: str = "tradingagents") -> dict:
    """統計本月已用美元 + 剩餘,供觸發前校驗。

    Returns:
        {
            "used": float,           # 本月已用(美元)
            "remaining": float,      # 剩餘(美元)
            "limit": float,          # 配置上限
            "exceeded": bool,        # 是否超限
            "runs_this_month": int,  # 本月執行次數
        }
    """
    now = datetime.now(timezone.utc)
    # AnalysisHistory.analysis_date 是 "YYYY-MM-DD" 字串
    month_prefix = now.strftime("%Y-%m")

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == agent_name,
                AnalysisHistory.analysis_date.like(f"{month_prefix}-%"),
            )
            .all()
        )

        total = 0.0
        for r in records:
            cost = _extract_cost(r.raw_data)
            if cost:
                total += cost

        used = round(total, 4)
        remaining = max(0.0, float(monthly_budget_usd) - used)
        return {
            "used": used,
            "remaining": round(remaining, 4),
            "limit": float(monthly_budget_usd),
            "exceeded": used >= float(monthly_budget_usd),
            "runs_this_month": len(records),
        }
    except Exception as e:
        logger.warning(f"[TA成本] 預算查詢失敗,預設放行: {e}")
        return {
            "used": 0.0,
            "remaining": float(monthly_budget_usd),
            "limit": float(monthly_budget_usd),
            "exceeded": False,
            "runs_this_month": 0,
        }
    finally:
        db.close()


def _extract_cost(raw_data) -> float:
    """從 AnalysisHistory.raw_data 提取 cost_usd。"""
    if not isinstance(raw_data, dict):
        return 0.0
    cost = raw_data.get("cost_usd")
    if cost is None:
        return 0.0
    try:
        return float(cost)
    except (TypeError, ValueError):
        return 0.0


def estimate_cost(
    *,
    debate_rounds: int,
    selected_analysts: list[str],
    model: str = "deepseek-chat",
) -> dict:
    """單次分析的成本估算(粗略,實際可能 ±50%)。

    用於觸發前給使用者預估。公式假設:
    - 每分析師 ~5k input + 2k output token
    - 辯論每輪 ~12k input + 4k output token
    - 風控 + PM ~15k input + 3k output token
    - LangGraph 累積上下文實際比理論高 2-5 倍
    """
    n_analysts = len(selected_analysts or [])
    prompt_tokens = n_analysts * 5000 + max(1, debate_rounds) * 12000 + 15000
    completion_tokens = n_analysts * 2000 + max(1, debate_rounds) * 4000 + 3000

    # 單價表(美元/百萬 token)
    PRICING = {
        "deepseek-chat": (0.14, 0.28),
        "deepseek-reasoner": (0.55, 2.19),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "claude-sonnet-4": (3.00, 15.00),
        "glm-4-flash": (0.05, 0.20),
    }
    input_rate, output_rate = PRICING.get(model.lower(), PRICING["deepseek-chat"])
    cost = (prompt_tokens / 1_000_000 * input_rate) + (
        completion_tokens / 1_000_000 * output_rate
    )

    return {
        "model": model,
        "prompt_tokens_est": prompt_tokens,
        "completion_tokens_est": completion_tokens,
        "cost_low_usd": round(cost * 2, 4),
        "cost_high_usd": round(cost * 5, 4),
    }


def get_today_cache_key(symbol: str, market: str, debate_rounds: int, model: str) -> str:
    """生成同標的同日的快取鍵,用於跳過重複 LLM 呼叫。"""
    today = date.today().isoformat()
    return f"{market}:{symbol}:{today}:r{debate_rounds}:{model}"
