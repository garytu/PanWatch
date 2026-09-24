"""進度 SSE 與日誌 SSE tail 端點單測（mock 快照/記憶體庫，不依賴真實任務）。"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.automation.api.agents as agents_api
import src.modules.administration.api.logs as logs_api
from src.platform.persistence.database import Base
from src.platform.persistence.models import LogEntry


def _parse_events(body: str) -> list[tuple[str, dict]]:
    """把 SSE wire 文本解析成 [(event, data), ...]，跳過心跳註釋。"""
    events = []
    for block in body.split("\n\n"):
        lines = [l for l in block.split("\n") if l]
        if not lines or lines[0].startswith(":"):
            continue
        event = next((l.split(": ", 1)[1] for l in lines if l.startswith("event: ")), "")
        data_raw = "\n".join(l.split(": ", 1)[1] for l in lines if l.startswith("data: "))
        events.append((event, json.loads(data_raw) if data_raw else {}))
    return events


async def _drain(resp) -> str:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)
    return "".join(chunks)


def test_progress_sse_push_and_done(monkeypatch):
    """進度 SSE：快照變化才推 progress 事件，終態後推 done 並關流"""
    snapshots = [
        {"trace_id": "t1", "status": "running", "current_stage": "market_analyst"},
        {"trace_id": "t1", "status": "running", "current_stage": "market_analyst"},  # 無變化，不推
        {"trace_id": "t1", "status": "running", "current_stage": "trader"},
        {"trace_id": "t1", "status": "success", "current_stage": None},
    ]
    calls = {"n": 0}

    def fake_get_run_progress(trace_id, db):
        idx = min(calls["n"], len(snapshots) - 1)
        calls["n"] += 1
        return snapshots[idx]

    monkeypatch.setattr(agents_api, "get_run_progress", fake_get_run_progress)
    monkeypatch.setattr(agents_api, "PROGRESS_SSE_POLL_SEC", 0.01)

    async def run():
        resp = await agents_api.stream_run_progress("t1")
        assert resp.media_type == "text/event-stream"
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    kinds = [e for e, _ in events]
    # 4 次快照裡只有 3 個不同 payload → 3 條 progress + 1 條 done
    assert kinds == ["progress", "progress", "progress", "done"]
    assert events[0][1]["current_stage"] == "market_analyst"
    assert events[2][1]["status"] == "success"
    assert events[3][1]["status"] == "success"


def test_progress_sse_invalid_trace_id():
    """進度 SSE：非法 trace_id 返回 400"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(agents_api.stream_run_progress("x" * 65))
    assert ei.value.status_code == 400


def _make_log_db(monkeypatch):
    """記憶體 SQLite + 預置日誌行，並替換 SessionLocal。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("src.platform.persistence.database.SessionLocal", factory)

    db = factory()
    for level, msg in [("INFO", "啟動完成"), ("ERROR", "行情拉取失敗"), ("INFO", "排程執行")]:
        db.add(LogEntry(
            timestamp=datetime.now(timezone.utc),
            level=level,
            logger_name="panwatch.test",
            message=msg,
        ))
    db.commit()
    db.close()
    return factory


def test_logs_sse_resume_from_last_event_id(monkeypatch):
    """日誌 SSE：帶 Last-Event-ID 從缺口續推，事件 id 即日誌行 id"""
    _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.01)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 0.05)

    async def run():
        request = SimpleNamespace(headers={"last-event-id": "1"})
        resp = await logs_api.stream_logs(
            request, level="", q="", logger_name="", domain="all", since="", last_event_id=0,
        )
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    assert len(logs_events) == 1
    ids = [item["id"] for item in logs_events[0]["items"]]
    assert ids == [2, 3]
    # 事件 id 用最後一條日誌 id
    assert "id: 3\nevent: logs\n" in body
    # 超時後有 done 收尾
    assert events[-1][0] == "done"


def test_logs_sse_filters(monkeypatch):
    """日誌 SSE：level 過濾生效，只推匹配的行"""
    _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.01)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 0.05)

    async def run():
        request = SimpleNamespace(headers={})
        resp = await logs_api.stream_logs(
            request, level="ERROR", q="", logger_name="", domain="all", since="", last_event_id=1,
        )
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    assert len(logs_events) == 1
    items = logs_events[0]["items"]
    assert len(items) == 1
    assert items[0]["level"] == "ERROR"
    assert items[0]["message"] == "行情拉取失敗"


def test_logs_sse_tail_only_new(monkeypatch):
    """日誌 SSE：無 Last-Event-ID 時從當前最新開始，只 tail 增量"""
    factory = _make_log_db(monkeypatch)
    # 時序閾值放寬以抗環境負載:該用例依賴"先建立基線快照、再插入增量"的先後關係,
    # 原 0.05s 預留在高負載下可能讓首輪基線輪詢尚未跑完就插入,導致新日誌被併入基線
    # 而不被 tail(基線偶發 flaky,與本次改動無關)。加大 MAX_DURATION 與插入前等待,
    # 給事件迴圈足夠排程餘量。
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.02)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 1.5)

    async def run():
        request = SimpleNamespace(headers={})
        resp = await logs_api.stream_logs(
            request, level="", q="", logger_name="", domain="all", since="", last_event_id=0,
        )

        received: list[str] = []

        async def consume():
            async for chunk in resp.body_iterator:
                received.append(chunk)

        task = asyncio.create_task(consume())
        # 等首輪基線輪詢穩妥跑完後再插入新日誌(放寬到 0.3s 抗負載抖動)
        await asyncio.sleep(0.3)
        db = factory()
        db.add(LogEntry(
            timestamp=datetime.now(timezone.utc),
            level="WARNING",
            logger_name="panwatch.test",
            message="新增日誌",
        ))
        db.commit()
        db.close()
        await asyncio.wait_for(task, timeout=3)
        return "".join(received)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    # 只有新插入的那條，存量 3 條不重放
    assert len(logs_events) == 1
    assert [i["message"] for i in logs_events[0]["items"]] == ["新增日誌"]
