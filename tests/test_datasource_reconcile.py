"""資料來源表溫和對帳(server.reconcile_data_sources):補齊缺失預設 + 刪孤兒,保留使用者自定義不動。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M  # noqa: F401  確保模型註冊到 Base.metadata
from src.platform.persistence.database import Base
from src.platform.persistence.models import DataSource

import server


def _make_session():
    """獨立記憶體 sqlite,不碰真實 data/panwatch.db。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_reconcile_deletes_orphans_keeps_user_custom_and_fills_missing_defaults():
    """對帳:刪孤兒(news/cls、kline/tushare),保留使用者自定義 quote/tencent(config/priority 原樣),補回缺失預設。"""
    db = _make_session()

    # 孤兒 1:news/cls —— 既不在 marketdata 包內引擎集合,也不在當前 seed 列表裡
    db.add(
        DataSource(
            name="財聯社電報",
            type="news",
            provider="cls",
            config={"rn": 50},
            enabled=True,
            priority=2,
            supports_batch=False,
            test_symbols=[],
        )
    )
    # 孤兒 2:kline/tushare —— Step3 已從 seed 裡刪除,包內也沒有對應 vendor
    db.add(
        DataSource(
            name="Tushare K線",
            type="kline",
            provider="tushare",
            config={"token": "", "description": "舊配置"},
            enabled=False,
            priority=10,
            supports_batch=False,
            test_symbols=["600519"],
        )
    )
    # 有效自定義:quote/tencent 是合法 seed 預設,但使用者改過 config/priority —— 應原樣保留
    db.add(
        DataSource(
            name="騰訊行情",
            type="quote",
            provider="tencent",
            config={"foo": 1},
            enabled=True,
            priority=99,
            supports_batch=True,
            test_symbols=["600519"],
        )
    )
    db.commit()
    # 故意不插入某個 seed 預設(例如東方財富 K線),驗證 reconcile 會補回

    result = server.reconcile_data_sources(db)

    remaining = {(s.type, s.provider): s for s in db.query(DataSource).all()}

    # 孤兒被刪
    assert ("news", "cls") not in remaining
    assert ("kline", "tushare") not in remaining

    # 使用者自定義配置原樣保留
    kept = remaining[("quote", "tencent")]
    assert kept.config == {"foo": 1}
    assert kept.priority == 99

    # 缺失的預設被補回
    assert ("kline", "eastmoney") in remaining

    # summary 裡能看到刪除記錄
    deleted_pairs = {(d["type"], d["provider"]) for d in result["deleted"]}
    assert ("news", "cls") in deleted_pairs
    assert ("kline", "tushare") in deleted_pairs
    assert ("quote", "tencent") not in deleted_pairs

    db.close()


def test_reconcile_refreshes_legacy_seed_test_symbols_but_keeps_custom_values():
    """升級預設測試股票時,舊種子/舊 APPL 拼寫應遷移,真正自定義值不能被覆蓋。"""
    db = _make_session()
    db.add(
        DataSource(
            name="騰訊K線",
            type="kline",
            provider="tencent",
            config={},
            enabled=True,
            priority=0,
            supports_batch=False,
            test_symbols=["601127", "600519", "300750", "APPL"],
        )
    )
    db.add(
        DataSource(
            name="自定義 K線",
            type="kline",
            provider="tencent",
            config={"custom": True},
            enabled=True,
            priority=99,
            supports_batch=False,
            test_symbols=["688981"],
        )
    )
    db.commit()

    server.reconcile_data_sources(db, reset_test_symbols=True)
    rows = db.query(DataSource).filter(DataSource.type == "kline").all()
    seeded = next(row for row in rows if row.name == "騰訊K線")
    custom = next(row for row in rows if row.name == "自定義 K線")

    assert seeded.test_symbols == ["600519", "601127", "00700", "00386", "AAPL", "NVDA"]
    assert custom.test_symbols == ["688981"]
    db.close()
