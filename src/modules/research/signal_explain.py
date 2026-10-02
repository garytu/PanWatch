"""訊號可解釋化(Phase 3):rank_score → 1-10 AI Score + 正負因子拆解。

對標 Danelfin 的 1-10 AI Score 與 green/red AI Factors:把 strategy_engine 已算出的
score_breakdown(alpha/catalyst/quality/source_bonus 加分項,risk/crowd penalty 扣分項)
拆成「正向(綠,提升)/ 負向(紅,拖累)」兩組,供機會頁展示。

純函式,不依賴 DB;在 API 層對 list_strategy_signals 的結果做後處理注入。
"""

from __future__ import annotations

# 因子中文標籤
FACTOR_LABELS = {
    "alpha_score": "選股α",
    "catalyst_score": "催化",
    "quality_score": "計劃質量",
    "source_bonus": "來源加成",
    "risk_penalty": "風險",
    "crowd_penalty": "擁擠度",
}

# 加分類因子(正值=提升,負值=拖累)
ADDITIVE_FACTORS = ("alpha_score", "catalyst_score", "quality_score", "source_bonus")
# 懲罰類因子(正值=拖累,score_breakdown 中以正數表示懲罰強度)
PENALTY_FACTORS = ("risk_penalty", "crowd_penalty")

_EPS = 0.01


def to_ai_score(rank_score) -> int:
    """rank_score(0-100)→ 1-10 AI Score(clamp 到 [1,10])。"""
    try:
        s = float(rank_score or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    return max(1, min(10, round(s / 10.0)))


def explain_factors(score_breakdown) -> dict:
    """拆成正向(綠)/負向(紅)兩組,各按貢獻絕對值排序取前 5。"""
    sb = score_breakdown if isinstance(score_breakdown, dict) else {}
    positive: list[dict] = []
    negative: list[dict] = []

    def _f(key):
        try:
            return float(sb.get(key))
        except (TypeError, ValueError):
            return None

    for key in ADDITIVE_FACTORS:
        v = _f(key)
        if v is None:
            continue
        if v > _EPS:
            positive.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(v, 2)})
        elif v < -_EPS:
            negative.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(v, 2)})

    for key in PENALTY_FACTORS:
        v = _f(key)
        if v is None:
            continue
        if v > _EPS:  # 懲罰為正 = 拖累,貢獻記為負
            negative.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(-v, 2)})

    positive.sort(key=lambda x: x["contribution"], reverse=True)
    negative.sort(key=lambda x: x["contribution"])  # 最負在前
    return {"positive": positive[:5], "negative": negative[:5]}


def enrich_signal(item: dict) -> dict:
    """給一條訊號 item 注入 ai_score + factor_explain(原地修改並返回)。"""
    if not isinstance(item, dict):
        return item
    item["ai_score"] = to_ai_score(item.get("rank_score"))
    item["factor_explain"] = explain_factors(item.get("score_breakdown"))
    return item
