"""Agent 執行記錄 - 寫入 agent_runs 表（供 UI 查詢）"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AgentRun, LogEntry

logger = logging.getLogger(__name__)

# 採集階段可能在外部資料來源限流/重試時暫時沒有進度日誌，不能沿用
# “5 分鐘無日誌即 stale”的規則；但服務重啟後也不能無限恢復舊任務。
ACTIVE_RUN_TTL_SEC = 45 * 60


def _as_utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def start_agent_run(
    agent_name: str,
    trace_id: str,
    trigger_source: str = "",
    model_label: str = "",
) -> None:
    """在任務真正開始前寫入 running 生命週期記錄。

    同一 trace 可能同時從 API 包裝器和執行入口呼叫，因此寫入是冪等的。
    """
    if not trace_id:
        return
    db = SessionLocal()
    try:
        existing = (
            db.query(AgentRun)
            .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
            .order_by(AgentRun.id.desc())
            .first()
        )
        if existing:
            return
        db.add(AgentRun(
            agent_name=agent_name,
            status="running",
            trace_id=trace_id[:64],
            trigger_source=(trigger_source or "")[:32],
            model_label=(model_label or "")[:255],
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"寫入 AgentRun running 狀態失敗: {e}")
        db.rollback()
    finally:
        db.close()


def record_agent_run(
    agent_name: str,
    status: str,
    result: str = "",
    error: str = "",
    duration_ms: int = 0,
    trace_id: str = "",
    trigger_source: str = "",
    notify_attempted: bool = False,
    notify_sent: bool = False,
    context_chars: int = 0,
    model_label: str = "",
) -> None:
    """記錄一次 Agent 執行結果到資料庫。

    Args:
        agent_name: Agent 名稱
        status: success / failed
        result: 簡要結果（會截斷）
        error: 錯誤資訊（會截斷）
        duration_ms: 執行耗時（毫秒）
        trace_id: 執行鏈路追蹤 id
        trigger_source: schedule / manual / api
        notify_attempted: 是否嘗試傳送通知
        notify_sent: 通知是否傳送成功
        context_chars: prompt/context 字元數
        model_label: 本次執行使用的模型標識
    """
    db = SessionLocal()
    try:
        existing = None
        if trace_id:
            existing = (
                db.query(AgentRun)
                .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
                .order_by(AgentRun.id.desc())
                .first()
            )
        values = {
            "agent_name": agent_name,
            "status": status,
            "trace_id": (trace_id or "")[:64],
            "trigger_source": (trigger_source or "")[:32],
            "notify_attempted": bool(notify_attempted),
            "notify_sent": bool(notify_sent),
            "context_chars": max(0, int(context_chars or 0)),
            "model_label": (model_label or "")[:255],
            "result": (result or "")[:2000],
            "error": (error or "")[:2000],
            "duration_ms": duration_ms,
        }
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            db.add(AgentRun(**values))
        db.commit()
    except Exception as e:
        logger.warning(f"寫入 AgentRun 失敗: {e}")
        db.rollback()
    finally:
        db.close()


def find_active_tradingagents_trace(db: Session, stock_symbol: str) -> str | None:
    """返回標的仍在執行的 TradingAgents trace，用於跨模組冪等觸發。

    執行狀態屬於自動化模組，市場模組只能透過這個公開查詢判斷是否需要建立新任務，
    不應匯入自動化 HTTP router 或直接查詢其內部實現。
    """
    now = datetime.now(timezone.utc)

    # 生命週期記錄是首選資料來源：採集階段還沒有 ta_progress 時也能恢復，
    # 且不會因為某個外部源 5 分鐘沒有日誌就重複觸發任務。
    active_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.agent_name == "tradingagents",
            AgentRun.status == "running",
            AgentRun.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .first()
    )
    if active_run and active_run.trace_id:
        created_at = _as_utc(active_run.created_at)
        if created_at is None or (now - created_at).total_seconds() <= ACTIVE_RUN_TTL_SEC:
            return active_run.trace_id
        # 已超過整個任務安全視窗時，不能再被舊日誌重新判成 running。
        return None

    cutoff = now - timedelta(minutes=30)
    latest_log = (
        db.query(LogEntry)
        .filter(
            LogEntry.event == "ta_progress",
            LogEntry.agent_name == "tradingagents",
            LogEntry.timestamp >= cutoff,
            LogEntry.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(LogEntry.timestamp.desc())
        .first()
    )
    if not latest_log or not latest_log.trace_id:
        return None

    trace_id = latest_log.trace_id
    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )
    if run and run.status in ("success", "failed"):
        return None

    last_ts = _as_utc(latest_log.timestamp)
    if last_ts and (now - last_ts).total_seconds() > 300:
        return None
    return trace_id
