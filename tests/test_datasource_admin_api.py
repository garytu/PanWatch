"""資料來源管理端點新增能力:is_orphan 標記 + POST /reset-to-seed 溫和對帳。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.administration.api.datasources as ds
import src.platform.persistence.models as M  # noqa: F401  確保模型註冊到 Base.metadata
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import DataSource


def _client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(ds.router, prefix="/api/datasources")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session


def test_is_orphan_flags_orphan_and_normal_rows_correctly():
    """is_orphan: 孤兒 provider(news/cls)標 True,正常 seed provider(quote/tencent)標 False。"""
    from types import SimpleNamespace

    orphan_row = SimpleNamespace(
        id=1, name="財聯社電報", type="news", provider="cls",
        config={}, enabled=True, priority=0, supports_batch=False, test_symbols=[],
    )
    normal_row = SimpleNamespace(
        id=2, name="騰訊行情", type="quote", provider="tencent",
        config={}, enabled=True, priority=0, supports_batch=True, test_symbols=[],
    )

    assert ds._to_response(orphan_row)["is_orphan"] is True
    assert ds._to_response(normal_row)["is_orphan"] is False


def test_reset_to_seed_endpoint_deletes_orphan_and_returns_summary():
    """POST /reset-to-seed: 冒煙 —— 刪孤兒行、返回 summary(經中介軟體包裹前的原始 dict)。"""
    client, Session = _client()

    db = Session()
    db.add(
        DataSource(
            name="財聯社電報", type="news", provider="cls", config={},
            enabled=True, priority=0, supports_batch=False, test_symbols=[],
        )
    )
    db.commit()
    db.close()

    resp = client.post("/api/datasources/reset-to-seed")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    deleted_pairs = {(d["type"], d["provider"]) for d in body["deleted"]}
    assert ("news", "cls") in deleted_pairs
    assert "seeded_missing" in body

    db2 = Session()
    remaining = {(s.type, s.provider) for s in db2.query(DataSource).all()}
    assert ("news", "cls") not in remaining
    # 缺失的預設(如東財K線)應被補回
    assert ("kline", "eastmoney") in remaining
    db2.close()
