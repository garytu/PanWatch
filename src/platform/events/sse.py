"""SSE（Server-Sent Events）基礎設施。

提供兩塊能力：
1. `format_sse_event`：把事件編碼為 SSE wire 格式（帶自增序號 id，供 Last-Event-ID 續推）。
2. `SSEStream` / `SSEHub`：生成過程與連線解耦的事件緩衝。
   - 生產者（後臺任務）往 `SSEStream` publish 事件，與 HTTP 連線無關，斷線不中斷生成；
   - 消費者（SSE 端點）從任意序號開始 subscribe，斷線重連帶 Last-Event-ID 即可續推；
   - 流結束（finish）後仍保留一段時間（TTL），供遲到的重連讀取完整事件。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

# 流結束後保留時長（秒）：足夠前端斷線重連拿到完整結果
STREAM_TTL_SEC = 600
# 單條流的事件數量上限（防禦性兜底，防止異常任務撐爆記憶體）
MAX_EVENTS_PER_STREAM = 10000


def format_sse_event(seq: int, event: str, data: dict | str) -> str:
    """編碼單條 SSE 事件（id + event + data，data 統一 JSON）。"""
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    # data 含換行時按 SSE 協議拆成多個 data: 行
    data_lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"id: {seq}\nevent: {event}\n{data_lines}\n"


def format_sse_comment(text: str = "keepalive") -> str:
    """編碼 SSE 註釋行（心跳，防止代理斷開空閒連線）。"""
    return f": {text}\n\n"


@dataclass
class _Event:
    seq: int
    event: str
    data: dict | str


@dataclass
class SSEStream:
    """一條可重放的事件流（生產端與消費端解耦）。"""

    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.monotonic)
    done: bool = False

    def __post_init__(self):
        self._events: list[_Event] = []
        self._cond = asyncio.Condition()

    async def publish(self, event: str, data: dict | str) -> int:
        """追加一條事件，返回其序號（從 1 開始）。"""
        async with self._cond:
            if len(self._events) >= MAX_EVENTS_PER_STREAM:
                # 超限直接置為結束，避免無界增長
                self.done = True
                self._cond.notify_all()
                return len(self._events)
            seq = len(self._events) + 1
            self._events.append(_Event(seq=seq, event=event, data=data))
            self._cond.notify_all()
            return seq

    async def finish(self) -> None:
        """標記流結束（訂閱者讀完緩衝後自然退出）。"""
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self, after_seq: int = 0, heartbeat_sec: float = 15.0):
        """從 after_seq 之後開始消費事件（非同步生成器，產出 SSE wire 格式字串）。

        - 先重放緩衝中已存在的事件（斷線重連 Last-Event-ID 續推的關鍵）；
        - 追平後阻塞等待新事件；等待超過 heartbeat_sec 則產出心跳註釋；
        - 流 done 且緩衝讀完後結束。
        """
        cursor = max(0, int(after_seq))
        while True:
            batch: list[_Event] = []
            async with self._cond:
                if cursor < len(self._events):
                    batch = self._events[cursor:]
                    cursor = len(self._events)
                elif self.done:
                    return
                else:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=heartbeat_sec)
                    except asyncio.TimeoutError:
                        pass
            if batch:
                for ev in batch:
                    yield format_sse_event(ev.seq, ev.event, ev.data)
            else:
                async with self._cond:
                    idle = not (cursor < len(self._events) or self.done)
                if idle:
                    yield format_sse_comment()


class SSEHub:
    """按 stream_id 管理多條 SSEStream，帶 TTL 清理。"""

    def __init__(self, ttl_sec: float = STREAM_TTL_SEC):
        self._streams: dict[str, SSEStream] = {}
        self._ttl_sec = ttl_sec

    def create(self) -> SSEStream:
        self._prune()
        stream = SSEStream()
        self._streams[stream.stream_id] = stream
        return stream

    def get(self, stream_id: str) -> SSEStream | None:
        self._prune()
        return self._streams.get(stream_id)

    def _prune(self) -> None:
        """清掉超過 TTL 的舊流。"""
        now = time.monotonic()
        expired = [
            sid for sid, s in self._streams.items()
            if now - s.created_at > self._ttl_sec
        ]
        for sid in expired:
            self._streams.pop(sid, None)


# chat 對話流的全域性 hub（程式內單例；生成任務與 SSE 連線透過它解耦）
chat_stream_hub = SSEHub()
