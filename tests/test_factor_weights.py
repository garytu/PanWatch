"""因子權重存取層(M1):lazy seed + 讀取,使用隔離的記憶體庫。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  註冊所有 ORM 模型到 Base.metadata
from src.platform.persistence.database import Base


def _mem_db():
    """每個用例一個全新的記憶體 SQLite 會話,避免汙染開發庫。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_get_factor_weights_lazy_seeds_defaults():
    """首次讀取某市場:5 個可標定因子全部 lazy seed 為 1.0。"""
    from src.modules.strategy.factor_weights import CALIBRATABLE_FACTORS, get_factor_weights

    db = _mem_db()
    try:
        w = get_factor_weights("CN", db=db)
        assert set(w) == set(CALIBRATABLE_FACTORS)
        assert all(v == 1.0 for v in w.values())
    finally:
        db.close()


def test_get_factor_weights_idempotent_no_dup_rows():
    """重複讀取不產生重複行(冪等 seed)。"""
    from src.modules.strategy.factor_weights import CALIBRATABLE_FACTORS, get_factor_weights
    from src.platform.persistence.models import FactorWeight

    db = _mem_db()
    try:
        get_factor_weights("CN", db=db)
        get_factor_weights("CN", db=db)
        n = db.query(FactorWeight).filter(FactorWeight.market == "CN").count()
        assert n == len(CALIBRATABLE_FACTORS)
    finally:
        db.close()


def test_get_factor_weights_reads_stored_value():
    """已存在的非預設權重應被讀出,不被 seed 覆蓋。"""
    from src.modules.strategy.factor_weights import get_factor_weights
    from src.platform.persistence.models import FactorWeight

    db = _mem_db()
    try:
        db.add(FactorWeight(factor_code="alpha_score", market="CN", weight=1.3))
        db.commit()
        w = get_factor_weights("CN", db=db)
        assert w["alpha_score"] == 1.3
        # 其餘因子仍補齊為預設 1.0
        assert w["catalyst_score"] == 1.0
    finally:
        db.close()


def test_get_all_factor_weights_lists_all_markets():
    """列出所有市場 × 因子,欄位含 weight/is_pinned/auto_calibrate。"""
    from src.modules.strategy.factor_weights import (
        CALIBRATABLE_FACTORS,
        MARKETS,
        get_all_factor_weights,
    )

    db = _mem_db()
    try:
        items = get_all_factor_weights(db=db)
        assert len(items) == len(CALIBRATABLE_FACTORS) * len(MARKETS)
        keys = {(i["factor_code"], i["market"]) for i in items}
        assert ("alpha_score", "CN") in keys
        sample = items[0]
        assert {"weight", "is_pinned", "auto_calibrate"} <= set(sample)
    finally:
        db.close()


def test_set_factor_weight_manual_writes_history():
    """手動改權重寫 manual 審計,並能同時設 is_pinned。"""
    from src.modules.strategy.factor_weights import set_factor_weight
    from src.platform.persistence.models import FactorWeightHistory

    db = _mem_db()
    try:
        res = set_factor_weight("alpha_score", "CN", weight=1.3, is_pinned=True, db=db)
        assert res["weight"] == 1.3
        assert res["is_pinned"] is True
        hist = (db.query(FactorWeightHistory)
                .filter_by(factor_code="alpha_score", market="CN", reason="manual").all())
        assert len(hist) == 1
        assert hist[0].new_weight == 1.3
    finally:
        db.close()


def test_set_factor_weight_rejects_unknown_factor():
    """未知因子拋 ValueError(API 層轉 400)。"""
    import pytest

    from src.modules.strategy.factor_weights import set_factor_weight

    db = _mem_db()
    try:
        with pytest.raises(ValueError):
            set_factor_weight("not_a_factor", "CN", weight=1.1, db=db)
    finally:
        db.close()
