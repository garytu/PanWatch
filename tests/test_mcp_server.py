"""MCP Server + PAT 鑑權測試。

全用記憶體庫與 TestClient,不觸網:tools/call 只測純 DB 工具(get_watchlist),
覆蓋協議握手/發現/呼叫/鑑權拒絕(缺失/無效/吊銷)/JWT 不能進 MCP。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.administration.api import mcp as mcp_api
from src.modules.administration.api import pats as pats_api
from src.platform.persistence.database import Base, get_db


@pytest.fixture()
def client_and_session(monkeypatch):
    """記憶體庫 + 掛 pats(/api/pats)與 mcp(/mcp)的測試應用。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # 審計日誌寫庫也指向記憶體庫,避免汙染真實庫
    monkeypatch.setattr(mcp_api, "SessionLocal", TestSession)

    app = FastAPI()
    app.include_router(pats_api.router, prefix="/api/pats")
    app.include_router(mcp_api.router, prefix="/mcp")
    app.dependency_overrides[get_db] = _db
    return TestClient(app), TestSession


def _create_pat(client, name="test") -> str:
    resp = client.post("/api/pats", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    return body


def test_missing_auth_rejected(client_and_session):
    """缺少 Authorization → 401"""
    client, _ = client_and_session
    r = client.post("/mcp", json=_rpc("initialize"))
    assert r.status_code == 401


def test_jwt_cannot_enter_mcp(client_and_session):
    """非 PAT(如 JWT)的 Bearer → 403,JWT 不能進 MCP"""
    client, _ = client_and_session
    r = client.post(
        "/mcp",
        json=_rpc("initialize"),
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.fake.jwt"},
    )
    assert r.status_code == 403


def test_invalid_pat_rejected(client_and_session):
    """PAT 字首正確但庫裡查不到 → 401"""
    client, _ = client_and_session
    r = client.post(
        "/mcp",
        json=_rpc("initialize"),
        headers={"Authorization": "Bearer pwmcp_notarealtoken"},
    )
    assert r.status_code == 401


def test_initialize_handshake(client_and_session):
    """initialize 返回 protocolVersion / capabilities / serverInfo"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-06-18"}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    res = data["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"]


def test_tools_list_discovery(client_and_session):
    """tools/list 暴露 5 個只讀工具,含 inputSchema"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("tools/list"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "get_portfolio",
        "get_stock_quote",
        "get_technical_analysis",
        "get_stock_suggestions",
        "get_watchlist",
    }
    assert all("inputSchema" in t for t in tools)


def test_tools_call_and_audit_log(client_and_session):
    """tools/call 執行純 DB 工具並落審計日誌"""
    client, TestSession = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "get_watchlist", "arguments": {}}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["isError"] is False
    assert res["content"][0]["type"] == "text"
    assert isinstance(res["content"][0]["text"], str)

    # 審計日誌落庫
    from src.platform.persistence.models import MCPCallLog

    db = TestSession()
    try:
        logs = db.query(MCPCallLog).all()
        assert len(logs) == 1
        assert logs[0].tool_name == "get_watchlist"
        assert logs[0].status == "ok"
    finally:
        db.close()


def test_tools_call_unknown_tool(client_and_session):
    """呼叫不在白名單的工具 → JSON-RPC 引數錯誤"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "rm_rf", "arguments": {}}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


def test_revoked_pat_rejected(client_and_session):
    """吊銷後的 PAT 立即失效 → 401"""
    client, _ = client_and_session
    create = client.post("/api/pats", json={"name": "tmp"}).json()
    token = create["token"]
    pat_id = create["id"]
    # 吊銷
    assert client.delete(f"/api/pats/{pat_id}").status_code == 200
    r = client.post(
        "/mcp",
        json=_rpc("tools/list"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_notification_returns_202(client_and_session):
    """通知類訊息(無 id)無需回應 → 202"""
    client, _ = client_and_session
    token = _create_pat(client)
    r = client.post(
        "/mcp",
        json=_rpc("notifications/initialized", rid=None),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202
