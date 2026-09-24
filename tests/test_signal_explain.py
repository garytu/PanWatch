"""訊號可解釋化(Phase 3)單元測試 —— 純函式。"""

from src.modules.research.signal_explain import enrich_signal, explain_factors, to_ai_score


def test_ai_score_mapping():
    """rank_score 0-100 對映到 1-10 並 clamp。"""
    assert to_ai_score(0) == 1
    assert to_ai_score(50) == 5
    assert to_ai_score(100) == 10
    assert to_ai_score(30) == 3
    assert to_ai_score(None) == 1  # 兜底


def test_explain_factors_splits_positive_negative():
    """加分因子正值入綠、負值入紅;懲罰因子入紅。"""
    sb = {"alpha_score": 5.0, "catalyst_score": -2.0, "quality_score": 3.0,
          "risk_penalty": 3.0, "crowd_penalty": 0.0}
    r = explain_factors(sb)
    pos = {x["factor"] for x in r["positive"]}
    neg = {x["factor"] for x in r["negative"]}
    assert "alpha_score" in pos and "quality_score" in pos
    assert "catalyst_score" in neg   # 加分因子取負值 → 拖累
    assert "risk_penalty" in neg     # 懲罰為正 → 拖累
    assert "crowd_penalty" not in neg  # 為 0 不計


def test_explain_factors_penalty_contribution_negative():
    """懲罰因子的 contribution 記為負數(表示拖累)。"""
    r = explain_factors({"risk_penalty": 4.0})
    item = next(x for x in r["negative"] if x["factor"] == "risk_penalty")
    assert item["contribution"] == -4.0


def test_enrich_signal_adds_fields():
    """enrich_signal 注入 ai_score 與 factor_explain。"""
    out = enrich_signal({"rank_score": 80, "score_breakdown": {"alpha_score": 5.0}})
    assert out["ai_score"] == 8
    assert "positive" in out["factor_explain"]


def test_explain_factors_empty():
    """空 breakdown 返回空兩組,不報錯。"""
    r = explain_factors(None)
    assert r == {"positive": [], "negative": []}
