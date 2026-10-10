"""SSE 基建單測：事件編碼 / 流緩衝續推 / 中介軟體直通。"""

import asyncio
import json

from src.platform.events.sse import SSEHub, SSEStream, format_sse_event
from src.web.response import ResponseWrapperMiddleware


def test_format_sse_event():
    """SSE 事件編碼：帶 id/event/data，dict 自動 JSON 化"""
    text = format_sse_event(3, "token", {"text": "你好"})
    assert "id: 3\n" in text
    assert "event: token\n" in text
    assert 'data: {"text": "你好"}\n' in text
    assert text.endswith("\n\n")


def test_format_sse_event_multiline():
    """SSE 事件編碼：data 含換行時拆成多個 data: 行"""
    text = format_sse_event(1, "token", "a\nb")
    assert "data: a\ndata: b\n" in text


def test_stream_replay_and_resume():
    """事件流：先發布後訂閱可重放全部事件；帶 after_seq 只續推之後的"""

    async def run():
        stream = SSEStream()
        await stream.publish("token", {"text": "a"})
        await stream.publish("token", {"text": "b"})
        await stream.publish("done", {})
        await stream.finish()

        # 從頭訂閱：3 條全收到
        all_events = [ev async for ev in stream.subscribe(after_seq=0)]
        assert len(all_events) == 3
        assert "id: 1\n" in all_events[0]

        # 斷線重連：Last-Event-ID=2 → 只收到第 3 條
        resumed = [ev async for ev in stream.subscribe(after_seq=2)]
        assert len(resumed) == 1
        assert "id: 3\n" in resumed[0]
        assert "event: done\n" in resumed[0]

    asyncio.run(run())


def test_stream_live_subscribe():
    """事件流：訂閱者阻塞等待，生產者釋出後立即收到，finish 後退出"""

    async def run():
        stream = SSEStream()
        received: list[str] = []

        async def consumer():
            async for ev in stream.subscribe(after_seq=0):
                received.append(ev)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        await stream.publish("token", {"text": "hi"})
        await asyncio.sleep(0.01)
        await stream.finish()
        await asyncio.wait_for(task, timeout=2)
        assert len(received) == 1
        assert "event: token\n" in received[0]

    asyncio.run(run())


def test_hub_create_get_prune():
    """Hub：create/get 正常，超 TTL 的流被清理"""
    hub = SSEHub(ttl_sec=0.0)  # TTL=0 → 下次 prune 即清理
    stream = hub.create()
    # TTL 為 0，get 時觸發 prune 已經清掉
    assert hub.get(stream.stream_id) is None

    hub2 = SSEHub(ttl_sec=60)
    s2 = hub2.create()
    assert hub2.get(s2.stream_id) is s2


def _make_scope(path="/api/chat/x"):
    return {"type": "http", "path": path}


def test_middleware_sse_passthrough():
    """中介軟體：text/event-stream 回應逐塊直通，不緩衝"""

    async def run():
        sent_during_app: list[int] = []

        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            })
            await send({"type": "http.response.body", "body": b"id: 1\n\n", "more_body": True})
            # 記錄此刻下游已收到多少條訊息——直通模式下應該已即時轉發
            sent_during_app.append(len(sent_messages))
            await send({"type": "http.response.body", "body": b"id: 2\n\n", "more_body": False})

        sent_messages: list[dict] = []

        async def send(message):
            sent_messages.append(message)

        mw = ResponseWrapperMiddleware(app)
        await mw(_make_scope(), None, send)

        # app 傳送第二塊前，start + 第一塊已經轉發到下游（證明未緩衝）
        assert sent_during_app == [2]
        assert len(sent_messages) == 3
        assert sent_messages[1]["body"] == b"id: 1\n\n"

    asyncio.run(run())


def test_middleware_json_still_wrapped():
    """中介軟體：普通 JSON 回應仍被包裝為 {code, success, data, message}"""

    async def run():
        async def app(scope, receive, send):
            body = json.dumps({"hello": "world"}).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})

        sent_messages: list[dict] = []

        async def send(message):
            sent_messages.append(message)

        mw = ResponseWrapperMiddleware(app)
        await mw(_make_scope(), None, send)

        body = json.loads(sent_messages[-1]["body"])
        assert body["code"] == 0
        assert body["success"] is True
        assert body["data"] == {"hello": "world"}

    asyncio.run(run())
