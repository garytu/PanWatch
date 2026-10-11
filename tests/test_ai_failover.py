"""AI 模型執行時 failover 單測（全 mock，不發真實請求）。

覆蓋：
- 錯誤分類分流（摘參重試 / 換模型 / 直接拋）；
- 負快取冷卻與恢復探測；
- 流式路徑 failover（首 token 前可切、首 token 後透出）；
- 候選鏈構建（主模型 + 庫內備選）；
- run 記錄實際使用的模型（AgentContext.model_label 反映 used_model_label）。
"""

import asyncio

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.ai import ai_failover as m
from src.platform.ai.ai_failover import (
    ERR_FATAL,
    ERR_PARAM,
    ERR_SWITCH,
    FailoverAIClient,
    build_failover_client,
    classify_ai_error,
    clear_ai_failover_state,
)
from src.platform.persistence.database import Base
from src.platform.persistence.models import AIModel, AIService

_REQ = httpx.Request("POST", "http://test")


def _http_err(cls, status, msg="err"):
    return cls(msg, response=httpx.Response(status, request=_REQ), body=None)


class _GenericStatusError(Exception):
    """帶 status_code 的普通異常（走 classify 的通用狀態碼分支）。"""

    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


class _FakeClient:
    """指令碼化的假 AIClient：每次非流式呼叫彈出一個指令碼項（異常則拋，否則返回）。"""

    def __init__(self, model="m", script=None):
        self.model = model
        self.base_url = "http://b"
        self.api_key = "k"
        self.total_tokens_used = 0
        self._script = list(script or [])
        self.temps: list = []
        self.calls = 0

    def _pop(self, temperature):
        self.temps.append(temperature)
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_multi(self, messages, temperature=0.4):
        return self._pop(temperature)

    async def chat(self, system_prompt, user_content, images=None, temperature=0.4):
        return self._pop(temperature)

    async def chat_with_tools(self, messages, tools, temperature=0.4):
        return self._pop(temperature)


# ── 錯誤分類 ────────────────────────────────────────────────────────────


def test_classify_timeout_switch():
    """超時歸為「換模型」"""
    assert classify_ai_error(APITimeoutError(request=_REQ)) == ERR_SWITCH


def test_classify_connection_switch():
    """連線錯誤歸為「換模型」"""
    assert classify_ai_error(APIConnectionError(message="c", request=_REQ)) == ERR_SWITCH


def test_classify_rate_limit_switch():
    """限流(429)歸為「換模型」"""
    assert classify_ai_error(_http_err(RateLimitError, 429)) == ERR_SWITCH


def test_classify_server_error_switch():
    """伺服器端 5xx 歸為「換模型」"""
    assert classify_ai_error(_http_err(InternalServerError, 500)) == ERR_SWITCH


def test_classify_auth_switch():
    """鑑權失效(401)歸為「換模型」（備用服務商 key 可能有效）"""
    assert classify_ai_error(_http_err(AuthenticationError, 401)) == ERR_SWITCH


def test_classify_param_incompatible_retry_same():
    """引數不相容(400 + temperature 提示)歸為「摘參重試同模型」"""
    err = _http_err(BadRequestError, 400, "temperature is not supported for this model")
    assert classify_ai_error(err) == ERR_PARAM


def test_classify_business_400_fatal():
    """明確的 4xx 業務錯誤(非引數類)歸為「直接拋」"""
    err = _http_err(BadRequestError, 400, "content policy violation")
    assert classify_ai_error(err) == ERR_FATAL


def test_classify_generic_4xx_fatal():
    """通用異常帶 4xx 狀態碼歸為「直接拋」"""
    assert classify_ai_error(_GenericStatusError("not found", 404)) == ERR_FATAL


def test_classify_generic_5xx_switch():
    """通用異常帶 5xx 狀態碼歸為「換模型」"""
    assert classify_ai_error(_GenericStatusError("bad gateway", 503)) == ERR_SWITCH


def test_classify_unknown_defaults_switch():
    """無從判斷的未知異常保守歸為「換模型」（下一候選可能是不同服務商）"""
    assert classify_ai_error(RuntimeError("mystery")) == ERR_SWITCH


# ── FailoverAIClient 行為 ────────────────────────────────────────────────


def test_failover_param_strip_retry_same_model():
    """引數不相容：摘掉 temperature 重試同一模型，不切換"""
    clear_ai_failover_state()
    err = _http_err(BadRequestError, 400, "temperature unsupported")
    c = _FakeClient("m1", [err, "OK"])
    fc = FailoverAIClient([(c, "svc/m1")])

    result = asyncio.run(fc.chat_multi([{"role": "user", "content": "hi"}]))

    assert result == "OK"
    assert c.calls == 2
    assert c.temps == [0.4, None]  # 第二次摘除 temperature
    assert fc.used_model_label == "svc/m1"


def test_failover_switch_to_next_and_cooldown():
    """主模型 5xx：降級下一候選，失敗候選進冷卻，後續呼叫直接跳過"""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(InternalServerError, 500)])
    c2 = _FakeClient("m2", ["OK2"])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    r = asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert r == "OK2"
    assert fc.used_model_label == "svc/m2"
    assert m._is_cooling("svc/m1")  # 主模型進入冷卻

    # 第二次呼叫：m1 仍在冷卻視窗內，直接用 m2，不再觸碰 m1
    c2._script = ["OK3"]
    r2 = asyncio.run(fc.chat_multi([{"role": "user", "content": "y"}]))
    assert r2 == "OK3"
    assert c1.calls == 1  # m1 未被再次呼叫


def test_failover_fatal_raises_without_switch():
    """致命錯誤(內容/prompt 類)：直接拋，不嘗試下一候選"""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(BadRequestError, 400, "content filter triggered")])
    c2 = _FakeClient("m2", ["unreachable"])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    with pytest.raises(BadRequestError):
        asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert c2.calls == 0  # 未降級到下一候選


def test_failover_all_cooling_recovery_probe():
    """所有候選都在冷卻時：用主候選做恢復探測，而非直接失敗"""
    clear_ai_failover_state()
    m._mark_fail("svc/m1")
    m._mark_fail("svc/m2")
    c1 = _FakeClient("m1", ["PRIMARY"])
    c2 = _FakeClient("m2", [])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    r = asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert r == "PRIMARY"
    assert c1.calls == 1
    assert not m._is_cooling("svc/m1")  # 探測成功後清除冷卻


def test_failover_chain_exhausted_raises_last_error():
    """所有候選均失敗(可降級類)：丟擲最後一個異常"""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(InternalServerError, 500, "boom1")])
    c2 = _FakeClient("m2", [_http_err(RateLimitError, 429, "boom2")])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    with pytest.raises(RateLimitError):
        asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))


# ── 流式 failover ────────────────────────────────────────────────────────


class _StreamRaiseBefore:
    """流開始前即拋錯的假使用者端（首 anext 拋異常）。"""

    model = "m1"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        raise _http_err(InternalServerError, 500, "stream boom")
        yield  # 使函式成為非同步生成器（不可達）


class _StreamOK:
    """正常產出一段 token 的假使用者端。"""

    model = "m2"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        yield ("token", "hi")
        yield ("message", {"content": "hi", "tool_calls": []})


class _StreamRaiseAfter:
    """產出一個 token 後才拋錯的假使用者端。"""

    model = "m1"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        yield ("token", "par")
        raise _http_err(InternalServerError, 500, "mid-stream boom")


def test_failover_stream_switch_before_first_token():
    """流式：首 token 前失敗可安全切換到下一候選"""
    clear_ai_failover_state()
    fc = FailoverAIClient([(_StreamRaiseBefore(), "svc/m1"), (_StreamOK(), "svc/m2")])

    async def run():
        return [ev async for ev in fc.chat_stream([{"role": "user", "content": "x"}])]

    events = asyncio.run(run())
    assert ("token", "hi") in events
    assert fc.used_model_label == "svc/m2"
    assert m._is_cooling("svc/m1")


def test_failover_stream_raise_after_started():
    """流式：已產出 token 後失敗無法回滾，直接透出異常"""
    clear_ai_failover_state()
    fc = FailoverAIClient([(_StreamRaiseAfter(), "svc/m1"), (_StreamOK(), "svc/m2")])

    async def run():
        out = []
        async for ev in fc.chat_stream([{"role": "user", "content": "x"}]):
            out.append(ev)
        return out

    with pytest.raises(InternalServerError):
        asyncio.run(run())


# ── 候選鏈構建 ───────────────────────────────────────────────────────────


def _mem_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_build_failover_client_chain_from_db():
    """構建候選鏈：主模型置首，其餘庫內模型按優先順序補齊為備選"""
    clear_ai_failover_state()
    db = _mem_session()
    svc = AIService(name="S", base_url="http://b", api_key="k")
    db.add(svc)
    db.commit()
    m1 = AIModel(name="M1", service_id=svc.id, model="glm-4", is_default=True)
    m2 = AIModel(name="M2", service_id=svc.id, model="glm-4-flash", is_default=False)
    db.add_all([m1, m2])
    db.commit()

    fc = build_failover_client(m1, svc, db=db)
    labels = [lbl for _, lbl in fc.candidates]
    assert labels[0] == "S/glm-4"  # 主模型置首
    assert "S/glm-4-flash" in labels  # 備選補齊
    db.close()


def test_build_failover_client_env_fallback(monkeypatch):
    """無庫內模型時回退環境變數單候選"""
    monkeypatch.setenv("AI_API_KEY", "test-key")
    clear_ai_failover_state()
    db = _mem_session()
    fc = build_failover_client(None, None, db=db)
    assert len(fc.candidates) == 1
    assert fc.candidates[0][1].startswith("env/")
    db.close()


# ── run 記錄實際使用的模型 ───────────────────────────────────────────────


def test_agent_context_model_label_reflects_used_model():
    """AgentContext.model_label 反映 failover 實際使用的模型（供 agent_runs 落庫）"""
    from src.modules.automation.base import AgentContext

    fc = FailoverAIClient([(_FakeClient("m1"), "svc/m1"), (_FakeClient("m2"), "svc/m2")])
    ctx = AgentContext(ai_client=fc, notifier=None, config=None, model_label="svc/m1")

    # 未發生切換：返回主模型標籤
    assert ctx.model_label == "svc/m1"

    # 發生 failover 後：反映實際跑通的模型
    fc.used_model_label = "svc/m2"
    assert ctx.model_label == "svc/m2"
