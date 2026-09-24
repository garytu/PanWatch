"""Agent catalog and kind helpers.

Workflow agents are user-facing, schedulable pipelines.
Capability agents are internal/manual tools and should not be auto-scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass


AGENT_KIND_WORKFLOW = "workflow"
AGENT_KIND_CAPABILITY = "capability"

WORKFLOW_AGENT_NAMES: tuple[str, ...] = (
    "premarket_outlook",
    "intraday_monitor",
    "daily_report",
)

CAPABILITY_AGENT_NAMES: tuple[str, ...] = (
    "news_digest",
    "chart_analyst",
)


def infer_agent_kind(agent_name: str | None) -> str:
    name = (agent_name or "").strip()
    if name in CAPABILITY_AGENT_NAMES:
        return AGENT_KIND_CAPABILITY
    return AGENT_KIND_WORKFLOW


def is_workflow_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_WORKFLOW


def is_capability_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_CAPABILITY


@dataclass(frozen=True)
class AgentSeedSpec:
    name: str
    display_name: str
    description: str
    enabled: bool
    schedule: str
    execution_mode: str
    kind: str
    visible: bool
    lifecycle_status: str = "active"
    replaced_by: str = ""
    display_order: int = 0
    config: dict | None = None


AGENT_SEED_SPECS: tuple[AgentSeedSpec, ...] = (
    AgentSeedSpec(
        name="premarket_outlook",
        display_name="盤前分析",
        description="開盤前綜合昨日分析和隔夜資訊，展望今日走勢",
        enabled=False,
        schedule="0 9 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=10,
    ),
    AgentSeedSpec(
        name="intraday_monitor",
        display_name="盤中監測",
        description="交易時段即時監控，AI 智慧判斷是否有值得關注的訊號",
        enabled=False,
        schedule="*/5 9-15 * * 1-5",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=20,
        config={
            "event_only": True,
            "price_alert_threshold": 3.0,
            "volume_alert_ratio": 2.0,
            "stop_loss_warning": -5.0,
            "take_profit_warning": 10.0,
            "throttle_minutes": 30,
        },
    ),
    AgentSeedSpec(
        name="daily_report",
        display_name="收盤覆盤",
        description="每日收盤後生成覆盤報告，包含市場回顧、個股覆盤和次日關注",
        enabled=True,
        schedule="30 15 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=30,
    ),
    AgentSeedSpec(
        name="news_digest",
        display_name="新聞速遞（能力）",
        description="內部能力：提供新聞抓取、去重與主題聚合，不獨立排程",
        enabled=False,
        schedule="",
        execution_mode="batch",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="premarket_outlook,daily_report,intraday_monitor",
        display_order=110,
        config={
            "since_hours": 12,
            "fallback_since_hours": 24,
        },
    ),
    AgentSeedSpec(
        name="chart_analyst",
        display_name="技術分析（能力）",
        description="內部能力：詳細資訊頁按需觸發影像技術分析，不獨立排程",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="intraday_monitor,daily_report,premarket_outlook",
        display_order=120,
    ),
    AgentSeedSpec(
        name="tradingagents",
        display_name="TradingAgents 深度分析",
        description="多 Agent 投資決策框架(基本面/情緒/新聞/技術 + 看多看空辯論 + 風控 + PM)。"
        "單次 3-5 分鐘、~$0.05 (deepseek-chat)。需手動觸發,預設關閉。",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=40,
        config={
            "analyst_types": ["market", "social", "news", "fundamentals"],
            "debate_rounds": 1,
            "monthly_budget_usd": 10.0,
            "over_budget_action": "reject",
            "cache_ttl_hours": 12,
            "output_language": "Chinese",
            "deep_model": "",       # 留空走預設 AI Service 的 model;可填如 "claude-sonnet-4"
            "quick_model": "",      # 留空 = deep_model;可填便宜模型如 "deepseek-chat"
            "timeout_minutes": 15,
            "llm_timeout_seconds": 120,  # 單次 LLM 請求超時，防止 analyst 永久阻塞
            "llm_max_retries": 0,         # 深度分析失敗快速落終態，不在圖內重複重試
            "llm_max_tokens": 4096,       # 限制模型輸出，避免閘道器空閒超時
            "emit_paper_trading_signal": False,  # 是否把 BUY 決策寫入 StrategySignalRun
                                                  # 驅動模擬交易自動開倉 (預設關,需使用者主動啟用)
            "enable_sec_edgar": False,  # 僅美股：優先使用有 filing-date 語義的 SEC EDGAR 財報
            "holding_period_days": 5,   # 上游決策質量回測使用的預設持倉期限
        },
    ),
)
