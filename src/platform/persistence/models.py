from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.platform.persistence.database import Base


class AIService(Base):
    """AI 服務商（base_url + api_key）"""

    __tablename__ = "ai_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # "OpenAI", "智譜", "DeepSeek"
    base_url = Column(String, nullable=False)
    api_key = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    models = relationship(
        "AIModel", back_populates="service", cascade="all, delete-orphan"
    )


class AIModel(Base):
    """AI 模型（屬於某個服務商）"""

    __tablename__ = "ai_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # 顯示名，如 "GLM-4-Flash"
    service_id = Column(
        Integer, ForeignKey("ai_services.id", ondelete="CASCADE"), nullable=False
    )
    model = Column(String, nullable=False)  # 實際模型標識，如 "glm-4-flash"
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    service = relationship("AIService", back_populates="models")


class NotifyChannel(Base):
    __tablename__ = "notify_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "telegram"
    config = Column(JSON, default={})  # {"bot_token": "...", "chat_id": "..."}
    enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class Account(Base):
    """交易帳戶"""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # 帳戶名稱，如 "招商證券"、"華泰證券"
    available_funds = Column(Float, default=0)  # 可用資金
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    positions = relationship(
        "Position", back_populates="account", cascade="all, delete-orphan"
    )


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    name = Column(String, nullable=False)
    market = Column(String, nullable=False)  # CN / HK / US
    # 以下欄位已廢棄，持倉資訊移至 Position 表
    cost_price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)
    invested_amount = Column(Float, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    agents = relationship(
        "StockAgent", back_populates="stock", cascade="all, delete-orphan"
    )
    positions = relationship(
        "Position", back_populates="stock", cascade="all, delete-orphan"
    )


class Position(Base):
    """持倉記錄（多帳戶多股票）"""

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_id", "stock_id", name="uq_account_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    cost_price = Column(Float, nullable=False)  # 成本價
    quantity = Column(Integer, nullable=False)  # 持倉數量
    invested_amount = Column(Float, nullable=True)  # 投入資金（用於盤中監控）
    sort_order = Column(Integer, default=0)
    trading_style = Column(
        String, default="swing"
    )  # short: 短線, swing: 波段, long: 長線
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="positions")
    stock = relationship("Stock", back_populates="positions")


class StockAgent(Base):
    """多對多: 每隻股票可被多個 Agent 監控"""

    __tablename__ = "stock_agents"
    __table_args__ = (
        UniqueConstraint("stock_id", "agent_name", name="uq_stock_agent"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    agent_name = Column(String, nullable=False)
    schedule = Column(String, default="")
    ai_model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    notify_channel_ids = Column(JSON, default=[])
    created_at = Column(DateTime, server_default=func.now())

    stock = relationship("Stock", back_populates="agents")


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(String, default="")
    kind = Column(String, default="workflow")  # workflow / capability
    visible = Column(Boolean, default=True)
    lifecycle_status = Column(String, default="active")  # active / deprecated
    replaced_by = Column(String, default="")
    display_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    schedule = Column(String, default="")
    # 執行模式: batch=批次(多隻股票一起分析傳送) / single=單隻(逐只分析傳送，即時性高)
    execution_mode = Column(String, default="batch")
    ai_model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    notify_channel_ids = Column(JSON, default=[])
    config = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # running / success / failed
    trace_id = Column(String, default="")
    trigger_source = Column(String, default="")  # schedule / manual / api
    notify_attempted = Column(Boolean, default=False)
    notify_sent = Column(Boolean, default=False)
    context_chars = Column(Integer, default=0)
    model_label = Column(String, default="")
    result = Column(String, default="")
    error = Column(String, default="")
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_time_id", "timestamp", "id"),
        Index("ix_log_entries_trace", "trace_id"),
        Index("ix_log_entries_agent_event", "agent_name", "event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    level = Column(String, nullable=False)
    logger_name = Column(String, default="")
    message = Column(String, default="")
    trace_id = Column(String, default="")
    run_id = Column(String, default="")
    agent_name = Column(String, default="")
    event = Column(String, default="")
    tags = Column(JSON, default={})
    notify_status = Column(String, default="")
    notify_reason = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, default="")
    description = Column(String, default="")


class DataSource(Base):
    """資料來源配置（新聞、K線圖、行情）"""

    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # "雪球資訊"
    type = Column(
        String, nullable=False
    )  # "news" / "chart" / "quote" / "kline" / "capital_flow"
    provider = Column(String, nullable=False)  # "xueqiu" / "eastmoney" / "tencent"
    config = Column(JSON, default={})  # 配置引數
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)  # 越小優先順序越高
    supports_batch = Column(Boolean, default=False)  # 是否支援批次查詢
    test_symbols = Column(JSON, default=[])  # 測試用股票程式碼列表
    created_at = Column(DateTime, server_default=func.now())


class NewsCache(Base):
    """新聞快取（用於去重）"""

    __tablename__ = "news_cache"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_news_source_external"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)  # "cls" / "eastmoney"
    external_id = Column(String, nullable=False)  # 來源側 ID
    title = Column(String, nullable=False)
    content = Column(String, default="")
    publish_time = Column(DateTime, nullable=False)
    symbols = Column(JSON, default=[])  # 關聯股票程式碼列表
    importance = Column(Integer, default=0)  # 0-3 重要性
    created_at = Column(DateTime, server_default=func.now())


class NotifyThrottle(Base):
    """通知節流記錄（防止同一股票短時間內重複通知）"""

    __tablename__ = "notify_throttle"
    __table_args__ = (
        UniqueConstraint("agent_name", "stock_symbol", name="uq_agent_stock_throttle"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    last_notify_at = Column(DateTime, nullable=False)
    notify_count = Column(Integer, default=1)  # 當日通知次數


class AnalysisHistory(Base):
    """分析歷史記錄（盤後分析、盤前分析等）"""

    __tablename__ = "analysis_history"
    __table_args__ = (
        UniqueConstraint(
            "agent_name", "stock_symbol", "analysis_date", name="uq_agent_stock_date"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)  # "daily_report" / "premarket_outlook"
    stock_symbol = Column(String, nullable=False)  # 股票程式碼，"*" 表示全部
    analysis_date = Column(String, nullable=False)  # 分析日期 "YYYY-MM-DD"
    title = Column(String, default="")  # 分析標題
    content = Column(String, nullable=False)  # AI 分析結果
    raw_data = Column(JSON, default={})  # 原始資料快照
    agent_kind_snapshot = Column(String, default="workflow")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StockContextSnapshot(Base):
    """按股票/日期儲存結構化上下文快照（用於跨天記憶）"""

    __tablename__ = "stock_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "market",
            "snapshot_date",
            "context_type",
            name="uq_stock_context_snapshot",
        ),
        Index(
            "ix_stock_context_symbol_date",
            "symbol",
            "market",
            "snapshot_date",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    market = Column(String, nullable=False)  # CN/HK/US
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    context_type = Column(String, nullable=False)  # premarket_outlook/daily_report/...
    payload = Column(JSON, default={})
    quality = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class NewsTopicSnapshot(Base):
    """新聞主題快照（按日期和視窗聚合）"""

    __tablename__ = "news_topic_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "window_days",
            name="uq_news_topic_snapshot_date_window",
        ),
        Index("ix_news_topic_snapshot_date", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    window_days = Column(Integer, nullable=False, default=7)
    symbols = Column(JSON, default=[])
    summary = Column(String, default="")
    topics = Column(JSON, default=[])
    sentiment = Column(String, default="neutral")
    coverage = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class AgentContextRun(Base):
    """每次 Agent 執行時使用的上下文摘要"""

    __tablename__ = "agent_context_runs"
    __table_args__ = (
        Index("ix_agent_context_agent_date", "agent_name", "analysis_date"),
        Index("ix_agent_context_stock_date", "stock_symbol", "analysis_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False, default="*")
    analysis_date = Column(String, nullable=False)  # YYYY-MM-DD
    context_payload = Column(JSON, default={})
    quality = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class AgentPredictionOutcome(Base):
    """建議後驗評估記錄（用於回放與效果統計）"""

    __tablename__ = "agent_prediction_outcomes"
    __table_args__ = (
        Index(
            "ix_prediction_agent_stock_date",
            "agent_name",
            "stock_symbol",
            "prediction_date",
        ),
        Index("ix_prediction_status_horizon", "outcome_status", "horizon_days"),
        Index("ix_prediction_group_horizon", "prediction_group_id", "horizon_days"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    prediction_date = Column(String, nullable=False)  # YYYY-MM-DD
    horizon_days = Column(Integer, nullable=False, default=1)  # 1/5/10...
    # 同一次建議的各 horizon 共用 UUID；歷史記錄為空時由查詢側相容聚合。
    prediction_group_id = Column(String, nullable=True)
    # 舊資料按自然日評估；新寫入統一按實際 K 線交易日評估。
    horizon_unit = Column(String, nullable=False, default="trading_days")
    action = Column(String, nullable=False, default="watch")
    action_label = Column(String, nullable=False, default="觀望")
    confidence = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StockSuggestion(Base):
    """股票建議池 - 彙總各 Agent 建議"""

    __tablename__ = "stock_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False, index=True)
    stock_market = Column(String, nullable=False, default="CN", index=True)
    stock_name = Column(String, default="")

    # 建議內容
    action = Column(
        String, nullable=False
    )  # buy/add/reduce/sell/hold/watch/alert/avoid
    action_label = Column(
        String, nullable=False
    )  # 中文標籤：建倉/加碼/減碼/出清/持有/觀望
    signal = Column(String, default="")  # 訊號描述
    reason = Column(String, default="")  # 建議理由

    # 來源追蹤
    agent_name = Column(
        String, nullable=False
    )  # intraday_monitor/daily_report/premarket_outlook
    agent_label = Column(String, default="")  # 盤中監測/盤後日報/盤前分析

    # 上下文資訊
    prompt_context = Column(String, default="")  # Prompt 上下文摘要
    ai_response = Column(String, default="")  # AI 原始回應

    # 後設資料（輸入快照/觸發原因等）
    meta = Column(JSON, default={})

    # 時間資訊
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)  # 建議過期時間

    # 索引：按市場+股票+時間快速查詢
    __table_args__ = (
        Index(
            "ix_suggestion_market_symbol_time",
            "stock_market",
            "stock_symbol",
            "created_at",
        ),
        Index("ix_suggestion_market_expires", "stock_market", "expires_at"),
    )


class EntryCandidate(Base):
    """入場候選榜快照（按天去重，可追溯來源建議與證據）。"""

    __tablename__ = "entry_candidates"
    __table_args__ = (
        UniqueConstraint(
            "stock_symbol",
            "stock_market",
            "snapshot_date",
            name="uq_entry_candidate_stock_date",
        ),
        Index("ix_entry_candidate_score_date", "snapshot_date", "score"),
        Index("ix_entry_candidate_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    status = Column(String, default="active")  # active / inactive / invalidated
    score = Column(Float, nullable=False, default=0)
    confidence = Column(Float, nullable=True)
    action = Column(String, nullable=False, default="watch")
    action_label = Column(String, nullable=False, default="觀望")
    signal = Column(String, default="")
    reason = Column(String, default="")
    candidate_source = Column(String, nullable=False, default="watchlist")  # watchlist / market_scan
    strategy_tags = Column(JSON, default=[])
    is_holding_snapshot = Column(Boolean, default=False)
    plan_quality = Column(Integer, default=0)  # 0-100
    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation = Column(String, default="")
    source_agent = Column(String, default="")
    source_suggestion_id = Column(Integer, nullable=True)
    source_trace_id = Column(String, default="")
    evidence = Column(JSON, default=[])
    plan = Column(JSON, default={})
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MarketScanSnapshot(Base):
    """市場池候選快照（用於多源回退與覆蓋診斷）。"""

    __tablename__ = "market_scan_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "stock_symbol",
            "stock_market",
            name="uq_market_scan_snapshot_symbol",
        ),
        Index("ix_market_scan_snapshot_day_market", "snapshot_date", "stock_market"),
        Index("ix_market_scan_snapshot_source", "snapshot_date", "source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    source = Column(String, nullable=False, default="market_scan")
    score_seed = Column(Float, nullable=False, default=0.0)
    quote = Column(JSON, default={})
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EntryCandidateFeedback(Base):
    """入場候選回饋（用於策略迭代與質量評估）。"""

    __tablename__ = "entry_candidate_feedback"
    __table_args__ = (
        Index("ix_entry_feedback_time", "created_at"),
        Index("ix_entry_feedback_symbol_day", "stock_market", "stock_symbol", "snapshot_date"),
        Index("ix_entry_feedback_source", "candidate_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    candidate_source = Column(String, nullable=False, default="watchlist")
    strategy_tags = Column(JSON, default=[])
    useful = Column(Boolean, default=True)
    reason = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now(), index=True)


class EntryCandidateOutcome(Base):
    """入場候選後驗結果（自動評估）。"""

    __tablename__ = "entry_candidate_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "horizon_days",
            name="uq_entry_outcome_candidate_horizon",
        ),
        Index("ix_entry_outcome_status_horizon", "outcome_status", "horizon_days"),
        Index("ix_entry_outcome_symbol_day", "stock_market", "stock_symbol", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("entry_candidates.id", ondelete="CASCADE"), nullable=False)
    snapshot_date = Column(String, nullable=False, default="")
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    candidate_source = Column(String, nullable=False, default="watchlist")
    strategy_tags = Column(JSON, default=[])
    horizon_days = Column(Integer, nullable=False, default=1)
    target_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    base_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StrategyCatalog(Base):
    """策略目錄（可版本化、可啟停、可調權重）。"""

    __tablename__ = "strategy_catalog"
    __table_args__ = (
        UniqueConstraint("code", name="uq_strategy_catalog_code"),
        Index("ix_strategy_catalog_enabled", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    version = Column(String, nullable=False, default="v1")
    enabled = Column(Boolean, default=True)
    market_scope = Column(String, default="ALL")  # ALL/CN/HK/US
    risk_level = Column(String, default="medium")  # low/medium/high
    params = Column(JSON, default={})
    default_weight = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategySignalRun(Base):
    """策略訊號執行快照（按日/股票/策略去重）。"""

    __tablename__ = "strategy_signal_runs"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "stock_symbol",
            "stock_market",
            "strategy_code",
            "source_candidate_id",
            name="uq_strategy_signal_daily_unique",
        ),
        Index("ix_strategy_signal_snapshot_rank", "snapshot_date", "rank_score"),
        Index("ix_strategy_signal_strategy_market", "strategy_code", "stock_market"),
        Index("ix_strategy_signal_status", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")

    strategy_code = Column(String, nullable=False)
    strategy_name = Column(String, default="")
    strategy_version = Column(String, default="v1")
    risk_level = Column(String, default="medium")
    source_pool = Column(String, default="watchlist")  # watchlist/market_scan

    score = Column(Float, nullable=False, default=0)
    rank_score = Column(Float, nullable=False, default=0)
    confidence = Column(Float, nullable=True)
    status = Column(String, default="active")  # active/inactive/invalidated
    action = Column(String, default="watch")
    action_label = Column(String, default="觀望")
    signal = Column(String, default="")
    reason = Column(String, default="")
    evidence = Column(JSON, default=[])
    holding_days = Column(Integer, default=3)

    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation = Column(String, default="")
    plan_quality = Column(Integer, default=0)

    source_agent = Column(String, default="")
    source_suggestion_id = Column(Integer, nullable=True)
    source_candidate_id = Column(Integer, nullable=True)
    trace_id = Column(String, default="")
    is_holding_snapshot = Column(Boolean, default=False)
    context_quality_score = Column(Float, nullable=True)
    payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyOutcome(Base):
    """策略後驗結果。"""

    __tablename__ = "strategy_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "signal_run_id",
            "horizon_days",
            name="uq_strategy_outcome_signal_horizon",
        ),
        Index("ix_strategy_outcome_strategy_horizon", "strategy_code", "horizon_days"),
        Index("ix_strategy_outcome_market_date", "stock_market", "target_date"),
        Index("ix_strategy_outcome_status", "outcome_status", "evaluated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_run_id = Column(
        Integer, ForeignKey("strategy_signal_runs.id", ondelete="CASCADE"), nullable=False
    )
    strategy_code = Column(String, nullable=False)
    snapshot_date = Column(String, nullable=False, default="")
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    source_pool = Column(String, default="watchlist")
    horizon_days = Column(Integer, nullable=False, default=1)
    target_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    base_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class BacktestRun(Base):
    """一次可回看的策略歷史回測。"""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("ix_backtest_runs_created", "created_at"),
        Index("ix_backtest_runs_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String, nullable=False, default="queued")  # queued/running/succeeded/failed
    strategy_code = Column(String, nullable=False)
    strategy_version = Column(String, default="")
    market = Column(String, nullable=False, default="CN")
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    config = Column(JSON, default={})
    input_snapshot = Column(JSON, default={})
    result = Column(JSON, default={})
    error_message = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class StrategyWeight(Base):
    """策略權重（當前生效值）。"""

    __tablename__ = "strategy_weights"
    __table_args__ = (
        UniqueConstraint(
            "strategy_code",
            "market",
            "regime",
            name="uq_strategy_weight_key",
        ),
        Index("ix_strategy_weight_effective", "effective_from"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="ALL")
    regime = Column(String, nullable=False, default="default")
    weight = Column(Float, nullable=False, default=1.0)
    reason = Column(String, default="")
    meta = Column(JSON, default={})
    effective_from = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyWeightHistory(Base):
    """策略調權歷史。"""

    __tablename__ = "strategy_weight_history"
    __table_args__ = (
        Index("ix_strategy_weight_history_time", "created_at"),
        Index("ix_strategy_weight_history_strategy_market", "strategy_code", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="ALL")
    regime = Column(String, nullable=False, default="default")
    old_weight = Column(Float, nullable=False, default=1.0)
    new_weight = Column(Float, nullable=False, default=1.0)
    reason = Column(String, default="")
    window_days = Column(Integer, default=45)
    sample_size = Column(Integer, default=0)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class FactorWeight(Base):
    """因子權重（當前生效值）——每因子 × 市場,由 IC/IR 自動標定 + 可手動覆蓋。

    映象 StrategyWeight,但作用於因子級(alpha/catalyst/quality/risk/crowd),
    讓訊號合成從「隱式權重=1 的黑盒」變成「外接可標定」。
    """

    __tablename__ = "factor_weights"
    __table_args__ = (
        UniqueConstraint("factor_code", "market", name="uq_factor_weight_key"),
        Index("ix_factor_weight_effective", "effective_from"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="CN")  # CN/HK/US
    weight = Column(Float, nullable=False, default=1.0)
    is_pinned = Column(Boolean, nullable=False, default=False)  # 手動鎖定,標定跳過
    auto_calibrate = Column(Boolean, nullable=False, default=True)  # 關掉則標定跳過
    reason = Column(String, default="")
    meta = Column(JSON, default={})
    effective_from = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FactorWeightHistory(Base):
    """因子調權歷史(審計)。"""

    __tablename__ = "factor_weight_history"
    __table_args__ = (
        Index("ix_factor_weight_history_time", "created_at"),
        Index("ix_factor_weight_history_factor_market", "factor_code", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="CN")
    old_weight = Column(Float, nullable=False, default=1.0)
    new_weight = Column(Float, nullable=False, default=1.0)
    ic = Column(Float, nullable=True)
    ir = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    reason = Column(String, default="")  # auto/manual/pinned_skip/auto_off/insufficient_samples
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class MarketRegimeSnapshot(Base):
    """市場狀態快照（用於按市場動態調權與解釋）。"""

    __tablename__ = "market_regime_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "market",
            name="uq_market_regime_day_market",
        ),
        Index("ix_market_regime_snapshot", "snapshot_date", "market"),
        Index("ix_market_regime_type", "regime"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    market = Column(String, nullable=False, default="CN")  # CN/HK/US
    regime = Column(String, nullable=False, default="neutral")  # bullish/neutral/bearish
    regime_score = Column(Float, nullable=False, default=0.0)  # [-1, 1]
    confidence = Column(Float, nullable=False, default=0.0)  # [0, 1]
    breadth_up_pct = Column(Float, nullable=True)
    avg_change_pct = Column(Float, nullable=True)
    volatility_pct = Column(Float, nullable=True)
    active_ratio = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyFactorSnapshot(Base):
    """每條策略訊號的因子分解快照。"""

    __tablename__ = "strategy_factor_snapshots"
    __table_args__ = (
        UniqueConstraint("signal_run_id", name="uq_strategy_factor_signal"),
        Index("ix_strategy_factor_snapshot_score", "snapshot_date", "final_score"),
        Index("ix_strategy_factor_strategy_market", "strategy_code", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_run_id = Column(
        Integer, ForeignKey("strategy_signal_runs.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    strategy_code = Column(String, nullable=False)
    alpha_score = Column(Float, default=0.0)
    catalyst_score = Column(Float, default=0.0)
    quality_score = Column(Float, default=0.0)
    risk_penalty = Column(Float, default=0.0)
    crowd_penalty = Column(Float, default=0.0)
    source_bonus = Column(Float, default=0.0)
    regime_multiplier = Column(Float, default=1.0)
    final_score = Column(Float, nullable=False, default=0.0)
    factor_payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PortfolioRiskSnapshot(Base):
    """按快照/市場聚合的組合風險畫像。"""

    __tablename__ = "portfolio_risk_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "market",
            name="uq_portfolio_risk_day_market",
        ),
        Index("ix_portfolio_risk_snapshot", "snapshot_date", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    market = Column(String, nullable=False, default="CN")
    total_signals = Column(Integer, default=0)
    active_signals = Column(Integer, default=0)
    held_signals = Column(Integer, default=0)
    unheld_signals = Column(Integer, default=0)
    high_risk_ratio = Column(Float, nullable=True)
    concentration_top5 = Column(Float, nullable=True)
    avg_rank_score = Column(Float, nullable=True)
    risk_level = Column(String, default="medium")  # low/medium/high
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SuggestionFeedback(Base):
    """建議回饋（匿名、輕量）"""

    __tablename__ = "suggestion_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suggestion_id = Column(
        Integer,
        ForeignKey("stock_suggestions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    useful = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class PriceAlertRule(Base):
    """價格提醒規則"""

    __tablename__ = "price_alert_rules"
    __table_args__ = (
        Index("ix_price_alert_enabled", "enabled"),
        Index("ix_price_alert_stock_enabled", "stock_id", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String, nullable=False, default="")
    enabled = Column(Boolean, default=True)
    condition_group = Column(JSON, default={})
    market_hours_mode = Column(String, default="trading_only")  # always/trading_only
    cooldown_minutes = Column(Integer, default=30)
    max_triggers_per_day = Column(Integer, default=3)
    repeat_mode = Column(String, default="repeat")  # once/repeat
    expire_at = Column(DateTime, nullable=True)
    notify_channel_ids = Column(JSON, default=[])
    last_trigger_at = Column(DateTime, nullable=True)
    last_trigger_price = Column(Float, nullable=True)
    trigger_count_today = Column(Integer, default=0)
    trigger_date = Column(String, default="")  # YYYY-MM-DD
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    stock = relationship("Stock")


class PriceAlertHit(Base):
    """價格提醒命中記錄"""

    __tablename__ = "price_alert_hits"
    __table_args__ = (
        Index("ix_price_alert_hits_rule_time", "rule_id", "trigger_time"),
        UniqueConstraint(
            "rule_id",
            "trigger_bucket",
            name="uq_price_alert_rule_bucket",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(
        Integer, ForeignKey("price_alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    trigger_time = Column(DateTime, server_default=func.now(), nullable=False)
    trigger_bucket = Column(String, nullable=False, default="")  # YYYYMMDDHHMM
    trigger_snapshot = Column(JSON, default={})
    notify_success = Column(Boolean, default=False)
    notify_error = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    rule = relationship("PriceAlertRule")
    stock = relationship("Stock")


class PaperTradingAccount(Base):
    """模擬交易帳戶（單例）"""

    __tablename__ = "paper_trading_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    initial_capital = Column(Float, nullable=False, default=1000000.0)
    current_capital = Column(Float, nullable=False, default=1000000.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    total_trades = Column(Integer, nullable=False, default=0)
    winning_trades = Column(Integer, nullable=False, default=0)
    max_drawdown_pct = Column(Float, nullable=False, default=0.0)
    peak_capital = Column(Float, nullable=False, default=1000000.0)
    enabled = Column(Boolean, default=True)
    excluded_markets = Column(JSON, default=[])  # 排除的市場，如 ["US"]（相容舊欄位，由 market_allocations 派生）
    # 各市場投資比例 {"CN":0.5,"HK":0.3,"US":0.2}，比例 0~1、合計 ≤ 1；比例 0 表示不投入該市場
    market_allocations = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PaperTradingPosition(Base):
    """模擬交易持倉"""

    __tablename__ = "paper_trading_positions"
    __table_args__ = (
        Index("ix_paper_pos_status", "status"),
        Index("ix_paper_pos_symbol_market", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=100)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    highest_price = Column(Float, nullable=True)  # 持倉期最高價(移動停損用)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="open")  # open/closed
    signal_run_id = Column(Integer, nullable=True)
    signal_snapshot_date = Column(String, default="")
    signal_action = Column(String, default="")
    strategy_code = Column(String, default="")
    opened_at = Column(DateTime, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PaperTradingTrade(Base):
    """模擬交易已平倉記錄"""

    __tablename__ = "paper_trading_trades"
    __table_args__ = (
        Index("ix_paper_trade_closed", "closed_at"),
        Index("ix_paper_trade_symbol", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=100)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    pnl = Column(Float, nullable=False, default=0.0)
    pnl_pct = Column(Float, nullable=False, default=0.0)
    exit_reason = Column(String, nullable=False, default="")  # stop_loss/target_price/signal_reversal/manual
    signal_run_id = Column(Integer, nullable=True)
    signal_snapshot_date = Column(String, default="")
    strategy_code = Column(String, default="")
    holding_days = Column(Integer, default=0)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, server_default=func.now())
    meta = Column(JSON, default={})


class ChatConversation(Base):
    """AI 對話會話"""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("ix_chat_conv_updated", "updated_at"),
        Index("ix_chat_conv_stock", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, default="")
    stock_symbol = Column(String, nullable=True)
    stock_market = Column(String, nullable=True)
    ai_model_id = Column(Integer, nullable=True)
    initial_context = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    """AI 對話訊息"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_msg_conv", "conversation_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    role = Column(String, nullable=False, default="user")  # user/assistant/system
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())


class AssistantContextSnapshot(Base):
    """The latest provider-neutral summary used to build assistant context."""

    __tablename__ = "assistant_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "version", name="ux_assistant_context_snapshot_version"
        ),
        Index(
            "ix_assistant_context_snapshot_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)
    mode = Column(String, nullable=False, default="balanced")
    summary = Column(JSON, nullable=False, default={})
    covered_until_message_id = Column(Integer, nullable=True)
    source_message_count = Column(Integer, nullable=False, default=0)
    usage_before = Column(JSON, nullable=False, default={})
    usage_after = Column(JSON, nullable=False, default={})
    created_at = Column(DateTime, server_default=func.now())


class AssistantTaskRun(Base):
    """Durable execution snapshot for an interactive assistant request."""

    __tablename__ = "assistant_task_runs"
    __table_args__ = (
        Index("ix_assistant_task_run_conversation_created", "conversation_id", "created_at"),
        Index("ix_assistant_task_run_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    user_message_id = Column(Integer, nullable=True)
    final_message_id = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="pending")
    context = Column(JSON, default={})
    checkpoint = Column(JSON, nullable=True)
    state_version = Column(Integer, nullable=False, default=0)
    current_step = Column(Integer, nullable=False, default=0)
    last_event_id = Column(String, nullable=False, default="")
    checkpoint_id = Column(String, nullable=False, default="")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    retry_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class AssistantTaskEvent(Base):
    """Append-only facts used to replay a durable assistant task."""

    __tablename__ = "assistant_task_events"
    __table_args__ = (
        UniqueConstraint("task_run_id", "sequence", name="ux_assistant_task_event_sequence"),
        UniqueConstraint("event_id", name="ux_assistant_task_event_id"),
        Index("ix_assistant_task_event_run_sequence", "task_run_id", "sequence"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    sequence = Column(Integer, nullable=False)
    event_id = Column(String, nullable=False)
    run_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    status = Column(String, nullable=True)
    step_index = Column(Integer, nullable=True)
    data = Column(JSON, default={})
    occurred_at = Column(DateTime, server_default=func.now())


class AssistantTaskStep(Base):
    """A planned or executed step in an assistant task."""

    __tablename__ = "assistant_task_steps"
    __table_args__ = (Index("ix_assistant_task_step_run_order", "task_run_id", "step_index"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    step_index = Column(Integer, nullable=False)
    title = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="pending")
    detail = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AssistantToolInvocation(Base):
    """One safe tool call, retained after the live event stream expires."""

    __tablename__ = "assistant_tool_invocations"
    __table_args__ = (Index("ix_assistant_tool_invocation_run", "task_run_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    call_id = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)
    risk = Column(String, nullable=False, default="read")
    arguments = Column(JSON, default={})
    status = Column(String, nullable=False, default="started")
    summary = Column(Text, nullable=False, default="")
    source_data = Column(JSON, default=[])
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)


class AssistantToolApproval(Base):
    """One human decision required before an agent tool call can execute."""

    __tablename__ = "assistant_tool_approvals"
    __table_args__ = (
        UniqueConstraint("task_run_id", "call_id", name="ux_assistant_approval_run_call"),
        Index("ix_assistant_approval_run_status", "task_run_id", "status"),
        Index("ix_assistant_approval_pending_expiry", "status", "expires_at"),
    )

    id = Column(String, primary_key=True)
    task_run_id = Column(Integer, nullable=False)
    call_id = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)
    risk = Column(String, nullable=False)
    arguments = Column(JSON, default={})
    presentation = Column(JSON, default={})
    status = Column(String, nullable=False, default="pending")
    expires_at = Column(DateTime, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AssistantToolPermission(Base):
    """A local principal's durable preference for a tool or risk category."""

    __tablename__ = "assistant_tool_permissions"
    __table_args__ = (
        UniqueConstraint(
            "principal_scope",
            "selector_kind",
            "selector_value",
            name="ux_assistant_permission_selector",
        ),
        Index("ix_assistant_permission_principal", "principal_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    principal_scope = Column(String, nullable=False, default="local")
    selector_kind = Column(String, nullable=False)  # tool | risk
    selector_value = Column(String, nullable=False)
    mode = Column(String, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AssistantArtifact(Base):
    """A durable user-visible output generated by an assistant task."""

    __tablename__ = "assistant_artifacts"
    __table_args__ = (Index("ix_assistant_artifact_run", "task_run_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_run_id = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False, default="")
    content = Column(Text, nullable=False, default="")
    payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class PersonalAccessToken(Base):
    """個人訪問令牌(PAT)—— MCP 端點專用的獨立長期憑據。

    與登入 JWT 分流:JWT 是單使用者會話態(30 天、不可吊銷、無 scope),不適合作為
    分發給外部 MCP client 的長期憑據;PAT 可獨立吊銷/審計、天然只讀 scope。
    庫裡只存 sha256(token_hash),明文僅建立時返回一次。單使用者應用,不設 user_id。
    """

    __tablename__ = "personal_access_tokens"
    __table_args__ = (
        Index("ix_pat_revoked", "revoked_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, default="")  # 使用者可讀的用途備註
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    prefix = Column(String(32), nullable=False, default="")  # 明文字首,列表展示用
    scopes_json = Column(Text, nullable=False, default="[]")  # JSON: ["mcp:read"]
    expires_at = Column(DateTime, nullable=True)  # None = 永不過期
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String, nullable=True)
    revoked_at = Column(DateTime, nullable=True)  # 非空即已吊銷
    created_at = Column(DateTime, server_default=func.now())


class MCPCallLog(Base):
    """每次 MCP tool 呼叫的審計記錄(只存後設資料,不存引數/結果明文)。"""

    __tablename__ = "mcp_call_logs"
    __table_args__ = (
        Index("ix_mcp_log_tool", "tool_name"),
        Index("ix_mcp_log_called", "called_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pat_id = Column(Integer, nullable=True)  # 軟引用,PAT 刪除後仍保留歷史
    pat_prefix = Column(String, nullable=True)
    tool_name = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="ok")  # ok / error
    error_message = Column(Text, nullable=True)
    args_summary = Column(Text, nullable=True)  # 脫敏摘要,截斷
    duration_ms = Column(Integer, default=0)
    client_ip = Column(String, nullable=True)
    called_at = Column(DateTime, server_default=func.now())
