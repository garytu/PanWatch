"""chat SSE 流式端點單測：事件序列 / 工具迴圈 / 降級 / 斷線續推（全 mock，不發真實請求）。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.assistant.chat_api as chat_api
from src.platform.events.sse import SSEStream
from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation, ChatMessage


def _make_session_factory():
    """記憶體 SQLite 會話工廠（StaticPool 保證同一連線）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _setup_conversation(session_factory, content="持倉怎麼樣"):
    """建一條對話 + 使用者訊息，返回 conversation_id。"""
    db = session_factory()
    conv = ChatConversation(title="test")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(ChatMessage(conversation_id=conv.id, role="user", content=content))
    db.commit()
    conv_id = conv.id
    db.close()
    return conv_id


class _FakeAIClient:
    """按預設指令碼逐輪回應的假 AI 使用者端。

    rounds 每項形如：
    - ("tokens", ["你", "好"])：本輪流式產出文本後結束（無工具呼叫）；
    - ("tools", [{"id","name","arguments"}, ...])：本輪要求呼叫工具；
    - "raise"：本輪流式呼叫直接拋異常（觸發降級路徑）。
    """

    def __init__(self, rounds, chat_multi_result="降級回答", chat_multi_raises=False):
        self._rounds = list(rounds)
        self._chat_multi_result = chat_multi_result
        self._chat_multi_raises = chat_multi_raises
        self.model = "fake-model"

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        assert self._rounds, "指令碼輪次已用盡"
        round_spec = self._rounds.pop(0)
        if round_spec == "raise":
            raise RuntimeError("stream unsupported")
        kind, payload = round_spec
        if kind == "tokens":
            for t in payload:
                yield ("token", t)
            yield ("message", {"content": "".join(payload), "tool_calls": []})
        else:
            yield ("message", {"content": "", "tool_calls": payload})

    async def chat_multi(self, messages, temperature=0.4):
        if self._chat_multi_raises:
            raise RuntimeError("multi also failed")
        return self._chat_multi_result


def _run_task_and_collect(monkeypatch, session_factory, ai_client, conv_id):
    """跑 _run_chat_stream_task 並收集全部事件，返回 [(event, data_dict), ...]。"""
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)
    monkeypatch.setattr(chat_api, "_get_ai_client", lambda db, model_id=None: ai_client)

    async def run():
        stream = SSEStream()
        await chat_api._run_chat_stream_task(conv_id, stream)
        events = []
        async for raw in stream.subscribe(after_seq=0):
            lines = raw.strip().split("\n")
            event = next(l.split(": ", 1)[1] for l in lines if l.startswith("event: "))
            data_raw = "\n".join(l.split(": ", 1)[1] for l in lines if l.startswith("data: "))
            events.append((event, json.loads(data_raw)))
        return events

    return asyncio.run(run())


def test_stream_task_plain_answer(monkeypatch):
    """無工具呼叫：token 事件逐個下發，done 攜帶完整回答且已落庫"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient([("tokens", ["你", "好"])])

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["token", "token", "done"]
    assert events[0][1]["text"] == "你"
    assert events[-1][1]["content"] == "你好"

    db = session_factory()
    saved = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .all()
    )
    db.close()
    assert len(saved) == 1
    assert saved[0].content == "你好"


def test_stream_task_tool_loop(monkeypatch):
    """工具迴圈：tool_call_start/tool_result 事件先推，最終回答走 token 流"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory, content="茅臺現在多少錢")
    ai = _FakeAIClient([
        ("tools", [{"id": "c1", "name": "get_stock_quote", "arguments": '{"symbol": "600519"}'}]),
        ("tokens", ["茅臺 1700 元"]),
    ])
    fake_exec = AsyncMock(return_value="即時行情：貴州茅臺 價格 1700")
    monkeypatch.setattr(chat_api, "_execute_tool", fake_exec)

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["tool_call_start", "tool_result", "token", "done"]
    assert events[0][1] == {"name": "get_stock_quote", "arguments": {"symbol": "600519"}}
    assert events[1][1]["ok"] is True
    assert "1700" in events[1][1]["preview"]
    assert events[-1][1]["content"] == "茅臺 1700 元"
    # 工具名與引數確實傳給了執行器
    fake_exec.assert_awaited_once()
    assert fake_exec.await_args.args[1] == "get_stock_quote"
    assert fake_exec.await_args.args[2] == {"symbol": "600519"}


def test_stream_task_fallback_to_chat_multi(monkeypatch):
    """流式不可用：降級 chat_multi，整段文本作為一個 token 事件下發並落庫"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient(["raise"], chat_multi_result="降級回答")

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["token", "done"]
    assert events[0][1]["text"] == "降級回答"
    assert events[-1][1]["content"] == "降級回答"


def test_stream_task_error_event(monkeypatch):
    """AI 徹底不可用：推 error 事件，錯誤文案照常落庫（與非流式行為一致）"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    ai = _FakeAIClient(["raise"], chat_multi_raises=True)

    events = _run_task_and_collect(monkeypatch, session_factory, ai, conv_id)

    kinds = [e for e, _ in events]
    assert kinds == ["error", "done"]
    assert "multi also failed" in events[0][1]["message"]
    assert "AI 服務暫時不可用" in events[-1][1]["content"]

    db = session_factory()
    saved = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .first()
    )
    db.close()
    assert "AI 服務暫時不可用" in saved.content


def test_send_message_stream_endpoint(monkeypatch):
    """流式端點：儲存使用者訊息，首條 meta 事件帶 stream_id，可透過 hub 續推"""
    session_factory = _make_session_factory()
    conv_id = _setup_conversation(session_factory)
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)

    async def fake_task(conversation_id, stream, task_id=None):
        await stream.publish("done", {"message_id": 1, "content": "x"})
        await stream.finish()

    monkeypatch.setattr(chat_api, "_run_chat_stream_task", fake_task)

    async def run():
        resp = await chat_api.send_message_stream(
            conv_id, chat_api.SendMessageBody(content="第二個問題")
        )
        assert resp.media_type == "text/event-stream"
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    body = asyncio.run(run())
    assert "event: meta\n" in body
    assert "event: done\n" in body

    # meta 裡的 stream_id 能從 hub 找回（斷線重連的依據）
    meta_line = next(
        l for l in body.split("\n") if l.startswith("data: ") and "stream_id" in l
    )
    stream_id = json.loads(meta_line[len("data: "):])["stream_id"]
    assert chat_api.chat_stream_hub.get(stream_id) is not None
    assert json.loads(meta_line[len("data: "):])["task_id"] > 0

    # 使用者訊息已落庫
    db = session_factory()
    user_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
        .all()
    )
    db.close()
    assert any(m.content == "第二個問題" for m in user_msgs)


def test_resume_stream_not_found():
    """斷線重連：未知/過期 stream_id 返回 404"""
    request = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat_api.resume_message_stream("nonexistent", request, 0))
    assert ei.value.status_code == 404


def test_resume_stream_last_event_id(monkeypatch):
    """斷線重連：Last-Event-ID header 優先於 query 引數，從其後續推"""
    async def run():
        stream = chat_api.chat_stream_hub.create()
        await stream.publish("token", {"text": "a"})
        await stream.publish("token", {"text": "b"})
        await stream.finish()

        request = SimpleNamespace(headers={"last-event-id": "1"})
        resp = await chat_api.resume_message_stream(stream.stream_id, request, 0)
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)

    body = asyncio.run(run())
    assert "id: 1\n" not in body
    assert "id: 2\n" in body
