"""系統自檢:classify_hint(中文修復提示庫)+ run_selfcheck(併發聚合)。"""

from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401
from src.platform.persistence.database import Base


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# --------------------------- classify_hint(純函式)---------------------------

def test_hint_datasource_proxy():
    """CN 資料來源連線類錯誤 → 提示代理 / trust_env。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("datasource", "Server disconnected without sending a response")
    assert "代理" in h or "trust_env" in h


def test_hint_db_locked():
    """database is locked → 提示併發 / 鎖。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("datasource", "sqlite3.OperationalError: database is locked")
    assert "鎖" in h or "併發" in h


def test_hint_ai_auth():
    """AI 401 → 提示 API Key / 鑑權。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("ai", "Error code: 401 - invalid_api_key")
    assert "Key" in h or "key" in h or "鑑權" in h


def test_hint_ai_model_not_found():
    """AI model 不存在 → 提示模型名。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("ai", "The model `gpt-x` does not exist (404)")
    assert "模型" in h


def test_hint_notify_invalid():
    """通知 URI 無效 → 提示配置 / 格式。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("notify", "Unsupported URL or invalid scheme")
    assert "配置" in h or "URI" in h or "格式" in h


def test_hint_system_disk():
    """磁碟類錯誤 → 提示空間/清理。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("system", "disk space low: only 50MB free")
    assert "磁碟" in h or "空間" in h


def test_hint_system_scheduler():
    """排程器停止 → 提示重啟。"""
    from src.modules.administration.selfcheck import classify_hint

    h = classify_hint("system", "scheduler stopped")
    assert "排程" in h


# --------------------------- 基礎項探測(DB/磁碟/排程)---------------------------

def test_probe_db_ok():
    """DB 探測對真實庫執行 SELECT 1,應通。"""
    from src.modules.administration.selfcheck import probe_db

    r = asyncio.run(probe_db())
    assert r["category"] == "system" and r["key"] == "sys:db"
    assert r["status"] in ("ok", "slow")


def test_probe_disk_ok():
    """磁碟探測返回用量,正常機器應通且帶 note。"""
    from src.modules.administration.selfcheck import probe_disk

    r = asyncio.run(probe_disk())
    assert r["key"] == "sys:disk"
    assert r["status"] in ("ok", "slow", "fail")
    assert r["note"]  # 顯示可用/總量


def test_probe_scheduler_empty_registry_ok():
    """無註冊排程器(如 CLI/未啟動)→ ok 但帶說明,不誤報斷。"""
    from src.platform.scheduling import scheduler_registry
    from src.modules.administration.selfcheck import probe_scheduler

    scheduler_registry.clear()
    r = asyncio.run(probe_scheduler())
    assert r["key"] == "sys:scheduler" and r["status"] == "ok"
    assert r["note"]


def test_probe_scheduler_running():
    """註冊了執行中的排程器 → ok,note 含任務數。"""
    from src.platform.scheduling import scheduler_registry
    from src.modules.administration.selfcheck import probe_scheduler

    class _FakeSched:
        running = True

        def get_jobs(self):
            return [1, 2, 3]

    scheduler_registry.clear()
    scheduler_registry.register("agent", _FakeSched())
    try:
        r = asyncio.run(probe_scheduler())
        assert r["status"] == "ok"
        assert "3" in r["note"]
    finally:
        scheduler_registry.clear()


def test_run_selfcheck_always_includes_system_items():
    """空庫也含 3 個系統基礎項(資料庫/磁碟/排程)。"""
    from src.modules.administration.selfcheck import run_selfcheck

    db = _mem_db()
    try:
        from src.platform.scheduling import scheduler_registry
        scheduler_registry.clear()
        res = asyncio.run(run_selfcheck(db=db))
        keys = {i["key"] for i in res["items"]}
        assert {"sys:db", "sys:disk", "sys:scheduler"} <= keys
    finally:
        db.close()


# --------------------------- run_selfcheck(聚合)---------------------------

def test_run_selfcheck_aggregates(monkeypatch):
    """列舉啟用項 → 併發 probe → 聚合 summary(total/ok/slow/fail)。"""
    from src.modules.administration import selfcheck
    from src.platform.persistence.models import AIModel, AIService, DataSource, NotifyChannel

    db = _mem_db()
    try:
        db.add(DataSource(name="東財", type="quote", provider="eastmoney", config={}, enabled=True))
        db.add(NotifyChannel(name="TG", type="telegram", config={}, enabled=True))
        svc = AIService(name="deepseek", base_url="https://x", api_key="k")
        db.add(svc)
        db.flush()
        db.add(AIModel(name="ds-chat", model="deepseek-chat", service_id=svc.id))
        db.commit()

        async def fake_ds(source):
            return {"category": "datasource", "key": f"ds:{source.id}", "name": source.name,
                    "status": "ok", "latency_ms": 10, "error": None, "hint": ""}

        async def fake_ai(model, service):
            return {"category": "ai", "key": f"ai:{model.id}", "name": model.name,
                    "status": "fail", "latency_ms": 20, "error": "401", "hint": "key 錯"}

        async def fake_nc(channel, send=False):
            return {"category": "notify", "key": f"nc:{channel.id}", "name": channel.name,
                    "status": "ok", "latency_ms": 5, "error": None, "hint": ""}

        monkeypatch.setattr(selfcheck, "probe_datasource", fake_ds)
        monkeypatch.setattr(selfcheck, "probe_ai_model", fake_ai)
        monkeypatch.setattr(selfcheck, "probe_notify_channel", fake_nc)

        res = asyncio.run(selfcheck.run_selfcheck(db=db, include_system=False))
        assert res["summary"] == {"total": 3, "ok": 2, "slow": 0, "fail": 1}
        assert {i["category"] for i in res["items"]} == {"datasource", "ai", "notify"}
    finally:
        db.close()


def test_run_selfcheck_empty_db():
    """無啟用項 → 空看板,不報錯。"""
    from src.modules.administration.selfcheck import run_selfcheck

    db = _mem_db()
    try:
        res = asyncio.run(run_selfcheck(db=db, include_system=False))
        assert res["summary"]["total"] == 0
        assert res["items"] == []
    finally:
        db.close()


def test_list_selfcheck_items_no_probe(monkeypatch):
    """list 模式只列舉待檢身份(category/key/name),不跑探測。"""
    from src.modules.administration import selfcheck
    from src.platform.persistence.models import DataSource, NotifyChannel

    db = _mem_db()
    try:
        db.add(DataSource(name="東財", type="quote", provider="eastmoney", config={}, enabled=True))
        db.add(NotifyChannel(name="TG", type="telegram", config={}, enabled=True))
        db.commit()

        called = {"n": 0}

        async def boom(*a, **k):
            called["n"] += 1
            return {}

        monkeypatch.setattr(selfcheck, "probe_datasource", boom)
        monkeypatch.setattr(selfcheck, "probe_notify_channel", boom)

        items = selfcheck.list_selfcheck_items(db=db, include_system=False)
        assert {i["key"] for i in items} == {"ds:1", "nc:1"}
        assert all({"category", "key", "name", "group"} <= set(i) for i in items)
        assert called["n"] == 0  # 沒觸發任何探測
    finally:
        db.close()


def test_list_items_ai_has_service_group():
    """AI 項帶 group=服務商名(供前端「服務商 → 模型」層級)。"""
    from src.modules.administration.selfcheck import list_selfcheck_items
    from src.platform.persistence.models import AIModel, AIService

    db = _mem_db()
    try:
        svc = AIService(name="DeepSeek", base_url="https://x", api_key="k")
        db.add(svc)
        db.flush()
        db.add(AIModel(name="ds-chat", model="deepseek-chat", service_id=svc.id))
        db.add(AIModel(name="ds-reasoner", model="deepseek-reasoner", service_id=svc.id))
        db.commit()
        items = list_selfcheck_items(db=db)
        ai = [i for i in items if i["category"] == "ai"]
        assert len(ai) == 2
        assert all(i["group"] == "DeepSeek" for i in ai)
    finally:
        db.close()


def test_run_selfcheck_keys_filter(monkeypatch):
    """keys 過濾:只探測指定 key 的項(供前端逐項更新進度)。"""
    from src.modules.administration import selfcheck
    from src.platform.persistence.models import DataSource, NotifyChannel

    db = _mem_db()
    try:
        db.add(DataSource(name="東財", type="quote", provider="eastmoney", config={}, enabled=True))
        db.add(NotifyChannel(name="TG", type="telegram", config={}, enabled=True))
        db.commit()

        async def fake_ds(s):
            return {"category": "datasource", "key": f"ds:{s.id}", "name": s.name,
                    "status": "ok", "latency_ms": 1, "error": None, "hint": ""}

        async def fake_nc(c, send=False):
            return {"category": "notify", "key": f"nc:{c.id}", "name": c.name,
                    "status": "ok", "latency_ms": 1, "error": None, "hint": ""}

        monkeypatch.setattr(selfcheck, "probe_datasource", fake_ds)
        monkeypatch.setattr(selfcheck, "probe_notify_channel", fake_nc)

        res = asyncio.run(selfcheck.run_selfcheck(db=db, keys=["ds:1"]))
        assert res["summary"]["total"] == 1
        assert res["items"][0]["key"] == "ds:1"
    finally:
        db.close()


# --------------------------- 端點 ---------------------------

def test_selfcheck_endpoint(monkeypatch):
    """端點呼叫 run_selfcheck 並原樣返回看板。"""
    from src.modules.administration.api import health

    async def fake_run(*, notify_send=False, keys=None):
        return {"items": [], "summary": {"total": 0, "ok": 0, "slow": 0, "fail": 0},
                "notify_send": notify_send}

    monkeypatch.setattr(health, "run_selfcheck", fake_run)
    # 直接呼叫路由函式需顯式傳參(Query 預設值僅在 HTTP 請求時解析)
    res = asyncio.run(health.selfcheck(notify_send=True, list_only=False, keys=None))
    assert res["summary"]["total"] == 0
    assert res["notify_send"] is True


def test_selfcheck_route_mounted():
    """/api/health/selfcheck 已掛載到 app。"""
    from src.bootstrap.application import app

    assert "/api/health/selfcheck" in set(app.openapi().get("paths", {}).keys())


# --------------------------- CLI doctor ---------------------------

def test_doctor_print_report(capsys):
    """make doctor 的報告:分組列印 + 失敗項帶錯誤與中文建議。"""
    from src.modules.administration.doctor import _print_report

    res = {
        "summary": {"total": 2, "ok": 1, "slow": 0, "fail": 1},
        "items": [
            {"category": "system", "key": "sys:db", "name": "資料庫", "group": None,
             "status": "ok", "latency_ms": 5, "error": None, "hint": "", "note": None},
            {"category": "datasource", "key": "ds:1", "name": "東財", "group": None,
             "status": "fail", "latency_ms": 0, "error": "timeout", "hint": "檢查代理設定", "note": None},
        ],
    }
    _print_report(res)
    out = capsys.readouterr().out
    assert "系統自檢" in out
    assert "【系統】" in out and "【資料來源】" in out
    assert "資料庫" in out and "東財" in out
    assert "檢查代理設定" in out and "❌" in out


# 已移除:自檢結果的定時告警 selfcheck_and_notify(使用者要求自檢不髮結果通知);彈跳視窗裡「含真實傳送通知」開關保留。
