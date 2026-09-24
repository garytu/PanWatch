"""TradingAgents 執行時適配：LLM 配置、金鑰注入和 LangChain 相容補丁。

橋接 PanWatch AIClient 配置 → TradingAgents LLM config。

TradingAgents 透過 langchain-openai / langchain-anthropic 等驅動 LLM,
讀取 config 字典 + 環境變數(`OPENAI_API_KEY`/`DEEPSEEK_API_KEY` 等)。
本模組把 PanWatch 的 AIClient 配置橋接過去。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.platform.ai.ai_client import AIClient

logger = logging.getLogger(__name__)

# 這是 Agent 入口允許依賴的穩定執行時介面；相容補丁實現留在本檔案下半部。
__all__ = [
    "VALID_ANALYSTS",
    "apply_compat_patches",
    "build_ta_llm_config",
    "inject_api_key_env",
]


# TradingAgents selected_analysts 欄位的合法值(見上游 graph/trading_graph.py)
VALID_ANALYSTS = {"market", "social", "news", "fundamentals"}


def build_ta_llm_config(
    ai_client: AIClient,
    *,
    debate_rounds: int = 1,
    selected_analysts: list[str] | None = None,
    output_language: str = "Chinese",
    deep_model: str | None = None,
    quick_model: str | None = None,
    market: str = "",
    enable_sec_edgar: bool = False,
    runtime_dir: str | Path | None = None,
    holding_period_days: int = 5,
    llm_timeout_seconds: int = 120,
    llm_max_retries: int = 0,
    llm_max_tokens: int = 4096,
) -> dict[str, Any]:
    """生成 TradingAgents 期望的 config dict。

    繼承 tradingagents.default_config.DEFAULT_CONFIG (含 data_cache_dir / project_dir /
    memory_log_path 等必需欄位),再覆蓋 PanWatch 配置:
    - llm_provider: 統一走 openrouter 相容協議(走 chat completions,避開 OpenAI Responses API)
    - backend_url: PanWatch AI 服務的 base_url
    - deep_think_llm: 推理/辯論/風控/PM 用的"強模型"。預設走 ai_client.model;
      可由 deep_model 引數覆蓋,允許辯論用 claude-sonnet / o3 這種貴但準的模型
    - quick_think_llm: 分析師工具呼叫用的"快模型"。預設 deep_model;
      可由 quick_model 引數覆蓋,允許分析師用 haiku / gpt-4o-mini 等便宜模型
    - max_debate_rounds: 辯論輪次
    - selected_analysts: ["market", "social", "news", "fundamentals"]
    - output_language: "Chinese" / "English"

    注意:TA 上游 deep + quick 共用 backend_url,所以兩個模型必須在**同一個 endpoint** 後面。
    要混 Claude + GPT 推薦 LiteLLM proxy 把多 provider 聚合到一個 endpoint。
    """
    analysts = list(selected_analysts or VALID_ANALYSTS)
    invalid = [a for a in analysts if a not in VALID_ANALYSTS]
    if invalid:
        raise ValueError(
            f"非法 analyst 名: {invalid}; 合法值: {sorted(VALID_ANALYSTS)}"
        )

    # 繼承上游預設 config(含 data_cache_dir / project_dir / memory_log_path 等),
    # 否則 TradingAgentsGraph.__init__ 用 os.makedirs(config["data_cache_dir"]) 會 KeyError。
    try:
        from tradingagents.default_config import DEFAULT_CONFIG as _UPSTREAM_DEFAULT
        config = dict(_UPSTREAM_DEFAULT)
    except ImportError:
        config = {}

    # 上游 config 含巢狀 vendor 配置；先複製，避免單次執行汙染 DEFAULT_CONFIG。
    config["data_vendors"] = dict(config.get("data_vendors") or {})
    config["tool_vendors"] = dict(config.get("tool_vendors") or {})

    if runtime_dir is not None:
        root = Path(runtime_dir).expanduser().resolve()
        results_dir = root / "results"
        data_cache_dir = root / "cache"
        memory_dir = root / "memory"
        for directory in (results_dir, data_cache_dir, memory_dir):
            directory.mkdir(parents=True, exist_ok=True)
        config.update({
            "results_dir": str(results_dir),
            "data_cache_dir": str(data_cache_dir),
            "memory_log_path": str(memory_dir / "trading_memory.md"),
        })

    # SEC EDGAR 的三張財務報表具備 filing-date 語義，只在美股且使用者顯式啟用時
    # 作為首選；非 SEC 標的或暫時不可用時回退 yfinance。
    statement_vendor = "sec_edgar,yfinance" if enable_sec_edgar and market.upper() == "US" else "yfinance"
    # set_config() 對巢狀 dict 做 merge。即使本次不啟用 EDGAR，也必須顯式寫回
    # yfinance，避免前一次美股執行留下的 tool_vendors 洩漏到 A/HK 分析。
    config["tool_vendors"].update({
        "get_balance_sheet": statement_vendor,
        "get_cashflow": statement_vendor,
        "get_income_statement": statement_vendor,
    })

    # PanWatch 覆蓋。
    # ⚠️ llm_provider 故意不用 "openai":TA 檢測到 openai 會強制開 use_responses_api=True
    # (OpenAI Responses API,/v1/responses 端點),矽基流動/智譜/Ollama 等第三方 OpenAI 相容
    # 服務不支援這個端點,會 404。
    # 用 "openrouter" 走標準 chat completions (/v1/chat/completions),同時 backend_url
    # 覆蓋預設 openrouter 端點為 PanWatch 配置的真實 base_url。
    # 雙模型解析:
    # - deep_model 未指定 → 用 ai_client.model
    # - quick_model 未指定 → 用 deep_model(單模型場景退化)
    deep_llm = (deep_model or ai_client.model or "").strip() or ai_client.model
    quick_llm = (quick_model or deep_llm or "").strip() or deep_llm

    config.update({
        "llm_provider": "openrouter",
        "backend_url": ai_client.base_url,
        "deep_think_llm": deep_llm,
        "quick_think_llm": quick_llm,
        "max_debate_rounds": max(1, int(debate_rounds)),
        "max_risk_discuss_rounds": 1,
        "selected_analysts": analysts,
        "output_language": output_language,
        "online_tools": True,
        "checkpoint_enabled": False,  # 避免 sqlite checkpoint 檔案汙染
        "holding_period_days": max(1, int(holding_period_days)),
        # TradingAgents 0.5.0 預設把這些交給底層 SDK；不設邊界時，供應商
        # 連線斷開或模型持續輸出會讓整個 LangGraph 永久停在當前 analyst。
        "llm_timeout_seconds": max(1, int(llm_timeout_seconds)),
        "llm_max_retries": max(0, int(llm_max_retries)),
        "max_tokens": max(256, int(llm_max_tokens)),
    })
    return config


def inject_api_key_env(ai_client: AIClient) -> None:
    """把 PanWatch AI 服務的 API key 注入到環境變數。

    TradingAgents llm_clients 按 provider 讀不同 env var
    (OPENAI_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY 等)。
    我們 PanWatch 走 openrouter 相容模式(chat completions),所以注入
    OPENROUTER_API_KEY。同時也設 OPENAI_API_KEY 作 fallback。

    注意:這是程式級 env var,如果同程序併發跑多個不同 key 的請求,可能競態。
    P0 假設 max_workers=2 且只用一個 AI service,可接受。
    """
    if not ai_client.api_key:
        logger.warning("[TA] AIClient 沒有 api_key,TradingAgents LLM 呼叫大機率失敗")
        return
    # 覆蓋多個候選 env var,讓 TA 不管走哪條 provider 分支都能取到 key
    os.environ["OPENROUTER_API_KEY"] = ai_client.api_key
    os.environ["OPENAI_API_KEY"] = ai_client.api_key
    os.environ["DEEPSEEK_API_KEY"] = ai_client.api_key


# ============================================================================
# LangChain compatibility patches
# ============================================================================

_PATCH_APPLIED = False


def apply_compat_patches() -> None:
    """應用所有 LangChain 相容性補丁。冪等。"""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    _patch_tool_call_args_coercion()
    _patch_ai_message_init()
    _PATCH_APPLIED = True


def _coerce_tool_calls_args(tool_calls: Any) -> Any:
    """把 tool_calls 列表中每項的 args 欄位(若是 JSON 字串)轉成 dict。"""
    if not isinstance(tool_calls, list):
        return tool_calls
    fixed = []
    for tc in tool_calls:
        if isinstance(tc, dict) and "args" in tc:
            raw = tc.get("args")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        tc = {**tc, "args": parsed}
                    else:
                        tc = {**tc, "args": {}}
                except (json.JSONDecodeError, TypeError):
                    tc = {**tc, "args": {}}
        fixed.append(tc)
    return fixed


def _patch_ai_message_init() -> None:
    """Patch AIMessage.__init__ 讓 tool_calls 欄位在校驗前自動 coerce str args → dict。

    這是直接攔截 AIMessage 構造的可靠路徑,不論 tool_calls 走的哪個上游函式。
    """
    try:
        from langchain_core.messages.ai import AIMessage
    except ImportError:
        return

    if getattr(AIMessage, "_panwatch_patched", False):
        return

    original_init = AIMessage.__init__

    def _patched_init(self, *args, **kwargs):
        if "tool_calls" in kwargs:
            kwargs["tool_calls"] = _coerce_tool_calls_args(kwargs["tool_calls"])
        return original_init(self, *args, **kwargs)

    AIMessage.__init__ = _patched_init  # type: ignore[method-assign]
    AIMessage._panwatch_patched = True  # type: ignore[attr-defined]
    logger.info("[TA compat] 已 patch AIMessage.__init__ 容忍 tool_calls.args 字串")


def _patch_tool_call_args_coercion() -> None:
    """讓 ToolCall / AIMessage 接受 string 型別的 args 並自動 json.loads。"""
    try:
        from langchain_core.messages import tool as _tool_module
    except ImportError:
        logger.debug("[TA compat] langchain_core 未裝,跳過 tool_call 補丁")
        return

    # 找到 create_tool_call 工廠函式(langchain 1.x);舊版可能叫 ToolCall 類直接構造
    create_func = getattr(_tool_module, "create_tool_call", None)
    if create_func is None:
        logger.debug("[TA compat] create_tool_call 未找到,跳過")
        return

    if getattr(create_func, "_panwatch_patched", False):
        return  # 已經 patched

    original = create_func

    def _patched_create_tool_call(*args, **kwargs):
        # 取出 args 引數(可能位置或關鍵字)
        raw_args = kwargs.get("args")
        if raw_args is None and len(args) >= 2:
            # 位置引數:create_tool_call(name, args, ...) 順序假設
            # 實際簽名見 langchain_core.messages.tool 原始碼,這裡寬鬆處理
            try:
                # 重新構造 kwargs 讓上游嚴格 validator 拿到 dict
                pass
            except Exception:
                pass

        # 修正 args 型別
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    kwargs["args"] = parsed
                    logger.debug(
                        f"[TA compat] tool_call.args 字串已自動 parse 成 dict "
                        f"(原始長度 {len(raw_args)})"
                    )
                else:
                    kwargs["args"] = {}
            except (json.JSONDecodeError, TypeError):
                kwargs["args"] = {}
                logger.debug("[TA compat] tool_call.args 不是合法 JSON,降級為 {}")

        return original(*args, **kwargs)

    _patched_create_tool_call._panwatch_patched = True  # type: ignore[attr-defined]

    # 替換模組級符號 + 替換內部 import
    _tool_module.create_tool_call = _patched_create_tool_call
    try:
        # langchain_core.output_parsers.openai_tools 在檔案頂部 from . import create_tool_call
        # 但 import 語義是把物件繫結到本地,所以需要也替換那邊
        from langchain_core.output_parsers import openai_tools as _ot
        if hasattr(_ot, "create_tool_call"):
            _ot.create_tool_call = _patched_create_tool_call
    except ImportError:
        pass

    logger.info("[TA compat] 已 patch langchain_core.messages.tool.create_tool_call")


def _patch_ai_message_validator() -> None:
    """備用方案:直接 patch AIMessage.model_validate 在 args 是 str 時降級清洗。

    目前不啟用,只在 tool_call_coercion 不夠用時啟用。
    """
    pass
