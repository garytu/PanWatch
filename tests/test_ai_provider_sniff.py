"""AI 服務商模型嗅探 + 測試 temperature 降級 的單元測試。"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models as _models  # noqa: F401  註冊 ORM
from src.platform.ai.ai_client import AIClient
from src.platform.persistence.database import Base
from src.platform.persistence.models import AIModel, AIService


@pytest.fixture
def db():
    """獨立記憶體 SQLite 會話,建全表。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_service_with_model(db) -> AIModel:
    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    m = AIModel(name="m", service_id=svc.id, model="m", is_default=False)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_client() -> AIClient:
    return AIClient(base_url="http://example.test", api_key="k", model="m")


def test_list_models_returns_sorted_ids():
    """list_models 呼叫 models.list() 並返回排序後的模型 id 列表。"""
    client = _make_client()

    class _FakeModels:
        async def list(self):
            data = [type("M", (), {"id": "gpt-4o"})(), type("M", (), {"id": "aaa"})()]
            return type("R", (), {"data": data})()

    client.client.models = _FakeModels()
    assert asyncio.run(client.list_models()) == ["aaa", "gpt-4o"]


def test_chat_omits_temperature_when_none():
    """temperature=None 時不向 create 下發該欄位。"""
    client = _make_client()
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        msg = type("Msg", (), {"content": "ok"})()
        choice = type("C", (), {"message": msg})()
        return type("Resp", (), {"usage": None, "choices": [choice]})()

    client.client.chat.completions.create = _fake_create
    asyncio.run(client.chat("s", "u", temperature=None))
    assert "temperature" not in seen


def test_chat_sends_temperature_when_float():
    """temperature 為數值時正常下發。"""
    client = _make_client()
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        msg = type("Msg", (), {"content": "ok"})()
        choice = type("C", (), {"message": msg})()
        return type("Resp", (), {"usage": None, "choices": [choice]})()

    client.client.chat.completions.create = _fake_create
    asyncio.run(client.chat("s", "u", temperature=0))
    assert seen["temperature"] == 0


def test_discover_models_returns_list(db, monkeypatch):
    """discover-models 用服務商憑證嗅探並返回模型 id 列表。"""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            return ["gpt-4o", "o1"]

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.discover_models(svc.id, db))
    assert res["models"] == ["gpt-4o", "o1"]


def test_discover_models_error_maps_to_400(db, monkeypatch):
    """嗅探失敗(服務商不支援/網路錯誤)返回 400。"""
    from fastapi import HTTPException

    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            raise RuntimeError("404 not found")

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    try:
        asyncio.run(providers.discover_models(svc.id, db))
        assert False, "應拋 HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


def test_batch_add_skips_duplicates_and_sets_default(db, monkeypatch):
    """批次新增:跳過已存在的 model 標識,設預設時清零其餘。"""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    db.add(AIModel(name="exists", service_id=svc.id, model="dup", is_default=True))
    db.commit()

    body = providers.BatchModelCreate(
        models=[
            providers.BatchModelItem(
                name="", model="dup", is_default=False
            ),  # 重複,跳過
            providers.BatchModelItem(name="新A", model="new-a", is_default=True),
            providers.BatchModelItem(name="", model="new-b", is_default=False),
        ]
    )
    res = providers.batch_add_models(
        svc.id, body, db
    )  # 同步端點(threadpool),不阻塞事件迴圈
    assert res["added"] == 2

    all_models = db.query(AIModel).filter(AIModel.service_id == svc.id).all()
    names = {m.model for m in all_models}
    assert names == {"dup", "new-a", "new-b"}
    # new-a 設為預設後,其餘(含原 dup)應被清零
    defaults = [m.model for m in all_models if m.is_default]
    assert defaults == ["new-a"]
    # 顯示名為空的回退為 model 標識
    assert next(m for m in all_models if m.model == "new-b").name == "new-b"


def test_batch_add_retries_a_transient_sqlite_lock(db, monkeypatch):
    """批次寫模型遇到短暫 SQLite 鎖時重試，不讓請求卡滿資料庫超時。"""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)
    body = providers.BatchModelCreate(
        models=[providers.BatchModelItem(model="new-model")]
    )
    original_commit = db.commit
    attempts = 0

    def commit_with_one_transient_lock():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise providers.OperationalError(
                "UPDATE ai_models SET is_default=?",
                {},
                sqlite3.OperationalError("database is locked"),
            )
        return original_commit()

    monkeypatch.setattr(db, "commit", commit_with_one_transient_lock)
    result = providers.batch_add_models(svc.id, body, db)

    assert result == {"added": 1}
    assert attempts == 2
    assert db.query(AIModel).filter(AIModel.model == "new-model").count() == 1


def test_test_model_omits_temperature(db, monkeypatch):
    """測試連通性時不下發 temperature(對不支援該引數的模型也安全)。"""
    from src.modules.administration.api import providers

    m = _seed_service_with_model(db)
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def chat(self, system_prompt, user_content, temperature=0.4):
            seen["temperature"] = temperature
            return "OK"

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.test_model(m.id, db))
    assert res["ok"] is True
    assert seen["temperature"] is None  # 未帶 temperature


def test_test_model_error_maps_to_400(db, monkeypatch):
    """測試呼叫報錯時對映為 400。"""
    from fastapi import HTTPException

    from src.modules.administration.api import providers

    m = _seed_service_with_model(db)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def chat(self, system_prompt, user_content, temperature=0.4):
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    try:
        asyncio.run(providers.test_model(m.id, db))
        assert False, "應拋 HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


def test_discover_models_empty_list(db, monkeypatch):
    """嗅探返回空列表時,介面正常返回空 models。"""
    from src.modules.administration.api import providers

    svc = AIService(name="s", base_url="http://x", api_key="k")
    db.add(svc)
    db.commit()
    db.refresh(svc)

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def list_models(self):
            return []

    monkeypatch.setattr(providers, "AIClient", _FakeClient)
    res = asyncio.run(providers.discover_models(svc.id, db))
    assert res["models"] == []
