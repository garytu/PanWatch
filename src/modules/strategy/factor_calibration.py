"""因子自校準(M2):把孤立的 IC/IR 接進因子權重的輕量標定閉環。

把 `factor_eval.evaluate_factor_ic` 算出的每因子 IC/IR,符號感知地轉成目標權重,
EMA 平滑 + clamp 後寫入 `FactorWeight`(並審計到 `FactorWeightHistory`)。
映象 `strategy_engine.rebalance_strategy_weights` 的機制,但作用於因子級。

設計要點(見 .docs/factor-self-calibration-design-2026-06-20.md):
- IC 必須測在「原始因子」上(快照存 raw,權重只在合成時乘),否則閉環自我強化失真。
- 懲罰因子(risk/crowd)IC 預期為負:用 −IC 驅動,懲罰有效→提權,失效→降權。
- 只吃「持有期已走完」的 outcome(point-in-time),防未來函式。
"""

from __future__ import annotations

import logging

from src.modules.strategy.factor_eval import evaluate_factor_ic
from src.modules.strategy.factor_weights import (
    CALIBRATABLE_FACTORS,
    MARKETS,
    PENALTY_FACTORS,
    get_factor_weights,
)
from src.platform.scheduling.timezone import utc_now
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# 歸一化基準:一個「不錯」的 IR / 一個「有意義」的單期 IC。
IR_REF = 0.5
IC_REF = 0.05


def compute_target(factor_code: str, ic, ir, *, beta: float = 0.4) -> float | None:
    """由 IC/IR 算目標權重;優先 IR(更穩),fallback IC;懲罰因子翻符號。

    返回 None 表示資訊不足(IC、IR 均缺失),應跳過該因子。
    term 歸一化並 clamp 到 [-1, 1];target = 1 + beta·term。
    """
    if ir is not None:
        term = ir / IR_REF
    elif ic is not None:
        term = ic / IC_REF
    else:
        return None
    if factor_code in PENALTY_FACTORS:
        term = -term  # 懲罰因子:IC 越負越該信
    term = max(-1.0, min(1.0, term))
    return 1.0 * (1.0 + beta * term)


def blend(old: float, target: float, *, alpha: float = 0.35,
          lo: float = 0.5, hi: float = 1.5) -> float:
    """EMA 平滑(防跳變)+ clamp 到 [lo, hi]。"""
    new = old * (1.0 - alpha) + target * alpha
    return max(lo, min(hi, new))


def calibrate_factor_weights(
    market: str, *, alpha: float = 0.35, beta: float = 0.4,
    clamp: tuple[float, float] = (0.5, 1.5),
    min_samples: int = 20, horizon: int = 5, days: int = 90, db=None,
) -> dict:
    """對單個市場跑一輪因子權重標定,寫 FactorWeight + FactorWeightHistory。

    門控:is_pinned / auto_calibrate=False / 樣本不足 / IC 缺失 → 跳過(不改權重)。
    每次都把最近觀測(last_ic/ir/sample_size)寫入 FactorWeight.meta 供 API 展示;
    History 只記錄「實際發生的調整」(reason=auto),避免冷啟動期審計噪聲。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        ic_result = evaluate_factor_ic(
            days=days, horizon=horizon, min_samples=min_samples, market=market, db=db
        )
        factors = ic_result.get("factors", {})
        get_factor_weights(market, db=db)  # 確保 5 個因子行存在

        lo, hi = float(clamp[0]), float(clamp[1])
        changed = 0
        rows_changed: list[dict] = []

        for code in CALIBRATABLE_FACTORS:
            row = (
                db.query(FactorWeight)
                .filter(FactorWeight.factor_code == code, FactorWeight.market == market)
                .first()
            )
            old = float(row.weight)
            stats = factors.get(code, {})
            ic = stats.get("ic")
            ir = stats.get("ir")
            n = int(stats.get("sample_size", 0))

            # 記錄最近一次觀測(供 API 展示),無論是否調整。
            row.meta = {
                **(row.meta or {}),
                "last_ic": ic, "last_ir": ir, "last_sample_size": n,
                "last_calibrated_at": utc_now().isoformat(),
            }

            if row.is_pinned or not row.auto_calibrate:
                continue
            if n < min_samples or ic is None:
                continue
            target = compute_target(code, ic, ir, beta=beta)
            if target is None:
                continue
            new = round(blend(old, target, alpha=alpha, lo=lo, hi=hi), 4)
            if abs(new - old) < 0.01:
                continue

            row.weight = new
            row.reason = f"auto(ic={ic}, ir={ir}, n={n})"
            row.effective_from = utc_now()
            row.updated_at = utc_now()
            db.add(FactorWeightHistory(
                factor_code=code, market=market, old_weight=old, new_weight=new,
                ic=ic, ir=ir, sample_size=n, reason="auto",
                meta={"target": round(target, 4), "alpha": alpha},
            ))
            changed += 1
            rows_changed.append({
                "factor_code": code, "old_weight": old, "new_weight": new, "sample_size": n,
            })

        db.commit()
        return {"market": market, "checked": len(CALIBRATABLE_FACTORS),
                "changed": changed, "rows": rows_changed}
    except Exception as e:  # pragma: no cover - 防禦性
        logger.warning(f"[因子標定] market={market} 失敗: {e}")
        db.rollback()
        return {"market": market, "checked": 0, "changed": 0, "rows": [], "error": str(e)}
    finally:
        if own:
            db.close()


def calibrate_all_markets(*, db=None, **kwargs) -> dict[str, dict]:
    """對所有市場(CN/HK/US)各跑一輪因子標定;供排程器每日 outcome 評估後呼叫。

    kwargs 透傳給 calibrate_factor_weights(alpha/beta/clamp/min_samples/horizon/days)。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        return {m: calibrate_factor_weights(m, db=db, **kwargs) for m in MARKETS}
    finally:
        if own:
            db.close()
