"""設定頁頭像:圖片落 data/avatars 檔案,DB 僅存檔名;data URL 讀寫,清空刪檔案。"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M  # noqa: F401 (確保模型註冊到 Base)
from src.modules.administration.api import settings as settings_api
from src.platform.persistence.database import Base, get_db

# 任意有效 base64;後端按位元組落檔案,GET 再讀回同樣的 data URL
_IMG = "data:image/jpeg;base64,AAAA"


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/settings")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_avatar_default_empty(tmp_path, monkeypatch):
    """未設定時頭像為空字串。"""
    c = _client(tmp_path, monkeypatch)
    assert c.get("/settings/avatar").json()["value"] == ""


def test_avatar_saved_as_file_db_stores_filename(tmp_path, monkeypatch):
    """上傳後:圖片落 data/avatars 檔案,DB 僅記檔名,GET 以 data URL 讀回。"""
    c = _client(tmp_path, monkeypatch)
    r = c.put("/settings/avatar", json={"value": _IMG})
    assert r.status_code == 200, r.text
    # 檔案已落盤到 data/avatars
    assert os.listdir(os.path.join(str(tmp_path), "avatars")) == ["avatar.jpg"]
    # DB 僅存檔名(短,非 base64)
    assert r.json()["value"] == "avatar.jpg"
    # GET 讀回 data URL
    assert c.get("/settings/avatar").json()["value"] == _IMG


def test_avatar_clear_deletes_file(tmp_path, monkeypatch):
    """傳空字串清空:刪除檔案 + GET 返回空。"""
    c = _client(tmp_path, monkeypatch)
    c.put("/settings/avatar", json={"value": _IMG})
    c.put("/settings/avatar", json={"value": ""})
    assert c.get("/settings/avatar").json()["value"] == ""
    assert os.listdir(os.path.join(str(tmp_path), "avatars")) == []


def test_avatar_rejects_non_dataurl(tmp_path, monkeypatch):
    """非 data URL 的頭像值應被拒絕(400)。"""
    c = _client(tmp_path, monkeypatch)
    assert c.put("/settings/avatar", json={"value": "http://x/a.png"}).status_code == 400


def test_avatar_key_not_in_generic_list(tmp_path, monkeypatch):
    """頭像鍵不混進通用設定列表。"""
    c = _client(tmp_path, monkeypatch)
    c.put("/settings/avatar", json={"value": _IMG})
    keys = [s["key"] for s in c.get("/settings").json()]
    assert "ui_avatar" not in keys
