"""trigger API 冪等性 + 狀態機兜底測試。

3 條原則:
1. 任務重啟後(stale),允許重新分析觸發
2. 每次觸發先檢查"是否有真正在跑的任務",有則返回現有 trace_id 不啟新任務
3. force_refresh=true 時無條件啟新任務(允許"忽略快取重新分析")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.modules.automation import find_active_tradingagents_trace


def _fake_log(timestamp, trace_id="man-tradingagents-601127-1234"):
    le = MagicMock()
    le.id = 1
    le.timestamp = timestamp
    le.event = "ta_progress"
    le.trace_id = trace_id
    le.agent_name = "tradingagents"
    return le


def _fake_run(status: str):
    r = MagicMock()
    r.status = status
    r.trace_id = "man-tradingagents-601127-1234"
    r.created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    return r


def _setup_db(latest_log, run=None):
    """構造 running AgentRun → 最新日誌 → trace 最終記錄的 mock。"""
    db = MagicMock()
    active_query = MagicMock()
    active_query.filter.return_value.order_by.return_value.first.return_value = None
    log_query = MagicMock()
    log_query.filter.return_value.order_by.return_value.first.return_value = latest_log
    run_query = MagicMock()
    run_query.filter.return_value.order_by.return_value.first.return_value = run
    db.query.side_effect = [active_query, log_query, run_query]
    return db


def test_no_running_task_returns_none():
    """沒日誌 = 沒在跑 → 允許新觸發"""
    db = _setup_db(latest_log=None)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_recent_log_no_run_returns_trace():
    """最近 1 分鐘有日誌且無 AgentRun → 在跑,返回 trace_id"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    db = _setup_db(latest_log=log, run=None)
    assert find_active_tradingagents_trace(db, "601127") == log.trace_id


def test_stale_log_returns_none():
    """5 分鐘無新進度 → stale → 允許新觸發(返回 None)"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(minutes=10))
    db = _setup_db(latest_log=log, run=None)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_completed_success_returns_none():
    """AgentRun.status=success → 不在跑(允許新觸發,例如重新分析)"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("success")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_completed_failed_returns_none():
    """AgentRun.status=failed → 不在跑(允許重新分析)"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("failed")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") is None


def test_running_status_returns_trace():
    """AgentRun.status=running 且日誌新 → 在跑"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(seconds=30))
    run = _fake_run("running")
    db = _setup_db(latest_log=log, run=run)
    assert find_active_tradingagents_trace(db, "601127") == log.trace_id


def test_running_status_but_stale_returns_none():
    """AgentRun.status=running 但日誌已過 5 分鐘 → 視為 stale 不在跑"""
    now = datetime.now(timezone.utc)
    log = _fake_log(now - timedelta(minutes=10))
    run = _fake_run("running")
    db = _setup_db(latest_log=log, run=run)
    # 注意:當前實現 run.status in ('success','failed') 才提前 return,
    # 'running' 但 stale 應該按 stale 處理 → None
    assert find_active_tradingagents_trace(db, "601127") is None
