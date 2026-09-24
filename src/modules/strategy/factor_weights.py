"""因子權重存取層(M1)。

把訊號合成時各因子的權重從「隱式 = 1 的硬編碼」變成「外接 + 可標定」:
- `get_factor_weights(market)`:讀取某市場各因子權重,缺失則 lazy seed 為 1.0;
  供 `strategy_engine._compute_factor_breakdown` 合成 raw_score 時按因子相乘。

標定邏輯見 `factor_calibration.py`;只讀/手動覆蓋的 API 在 `web/api/factors.py`。
"""

from __future__ import annotations

import logging

from src.platform.scheduling.timezone import utc_now
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# 可標定因子(與 StrategyFactorSnapshot 列、factor_eval.FACTOR_FIELDS 對齊)。
# source_bonus 暫不進標定集(v1 維持權重 1.0);final_score 是合成結果,非輸入因子。
CALIBRATABLE_FACTORS = (
    "alpha_score",
    "catalyst_score",
    "quality_score",
    "risk_penalty",
    "crowd_penalty",
)

# 懲罰類因子:在 raw_score 中被減,IC 預期為負。
PENALTY_FACTORS = frozenset({"risk_penalty", "crowd_penalty"})

MARKETS = ("CN", "HK", "US")


def get_factor_weights(market: str, *, db=None) -> dict[str, float]:
    """讀取某市場各可標定因子的權重;缺失因子 lazy seed 為 1.0。

    返回 {factor_code: weight},鍵恆為 CALIBRATABLE_FACTORS 全集。
    消費方對未登記因子應用 `.get(code, 1.0)` 兜底。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(FactorWeight).filter(FactorWeight.market == market).all()
        existing = {r.factor_code: float(r.weight) for r in rows}
        missing = [f for f in CALIBRATABLE_FACTORS if f not in existing]
        if missing:
            for f in missing:
                db.add(FactorWeight(factor_code=f, market=market, weight=1.0))
            db.commit()
            for f in missing:
                existing[f] = 1.0
        return {f: existing.get(f, 1.0) for f in CALIBRATABLE_FACTORS}
    finally:
        if own:
            db.close()


def _serialize(row: FactorWeight) -> dict:
    meta = row.meta or {}
    return {
        "factor_code": row.factor_code,
        "market": row.market,
        "weight": round(float(row.weight), 4),
        "is_pinned": bool(row.is_pinned),
        "auto_calibrate": bool(row.auto_calibrate),
        "last_ic": meta.get("last_ic"),
        "last_ir": meta.get("last_ir"),
        "last_sample_size": meta.get("last_sample_size"),
        "last_calibrated_at": meta.get("last_calibrated_at"),
        "reason": row.reason or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_all_factor_weights(*, db=None) -> list[dict]:
    """列出所有市場 × 因子的權重(含最近 IC/IR 觀測),供只讀 API/UI 展示。"""
    own = db is None
    db = db or SessionLocal()
    try:
        for m in MARKETS:
            get_factor_weights(m, db=db)  # 確保各市場已 seed
        rows = (
            db.query(FactorWeight)
            .order_by(FactorWeight.market, FactorWeight.factor_code)
            .all()
        )
        return [_serialize(r) for r in rows]
    finally:
        if own:
            db.close()


def set_factor_weight(
    factor_code: str, market: str, *,
    weight: float | None = None, is_pinned: bool | None = None,
    auto_calibrate: bool | None = None, db=None,
) -> dict:
    """手動覆蓋某因子權重 / pin / 開關自動標定;權重變化寫 manual 審計。"""
    if factor_code not in CALIBRATABLE_FACTORS:
        raise ValueError(f"未知因子: {factor_code}")
    if market not in MARKETS:
        raise ValueError(f"未知市場: {market}")
    own = db is None
    db = db or SessionLocal()
    try:
        get_factor_weights(market, db=db)  # 確保行存在
        row = (
            db.query(FactorWeight)
            .filter(FactorWeight.factor_code == factor_code, FactorWeight.market == market)
            .first()
        )
        if weight is not None:
            old = float(row.weight)
            new = round(max(0.1, min(3.0, float(weight))), 4)  # 手動也有界,防誤填
            if abs(new - old) >= 1e-9:
                row.weight = new
                row.reason = "manual"
                row.effective_from = utc_now()
                row.updated_at = utc_now()
                db.add(FactorWeightHistory(
                    factor_code=factor_code, market=market,
                    old_weight=old, new_weight=new, sample_size=0, reason="manual",
                ))
        if is_pinned is not None:
            row.is_pinned = bool(is_pinned)
            row.updated_at = utc_now()
        if auto_calibrate is not None:
            row.auto_calibrate = bool(auto_calibrate)
            row.updated_at = utc_now()
        db.commit()
        return _serialize(row)
    finally:
        if own:
            db.close()
