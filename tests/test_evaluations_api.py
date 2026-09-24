"""驗證中心 Agent 建議覆盤 API。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.platform.persistence.models  # noqa: F401  註冊 ORM 模型
from src.platform.persistence.database import Base
from src.platform.persistence.models import AgentPredictionOutcome


def _mem_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _row(horizon: int) -> AgentPredictionOutcome:
    return AgentPredictionOutcome(
        agent_name="daily_report",
        stock_symbol="600000",
        stock_market="CN",
        prediction_date="2026-08-28",
        prediction_group_id="group-api-1",
        horizon_days=horizon,
        horizon_unit="trading_days",
        action="buy",
        action_label="買入",
        trigger_price=10.0,
        outcome_price=11.0,
        outcome_return_pct=10.0,
        outcome_status="evaluated",
        meta={"reason": "測試理由", "signal": "測試訊號"},
    )


def test_evaluations_router_is_mounted():
    """驗證中心僅暴露 Agent 建議覆盤介面。"""
    from src.bootstrap.application import app

    paths = app.openapi()["paths"]

    assert "/api/evaluations/agent-predictions" in paths
    assert "/api/evaluations/agent-predictions/summary" in paths
    assert "/api/evaluations/backtests" not in paths
    assert "/api/evaluations/backtests/options" not in paths


def test_list_agent_predictions_returns_one_group_with_policy():
    """兩條 horizon 原始記錄被 API 組裝為一條覆盤建議。"""
    from src.modules.research.api import evaluations

    db = _mem_db()
    try:
        db.add_all([_row(1), _row(5)])
        db.commit()

        result = evaluations.list_agent_predictions(
            db=db,
            days=90,
            limit=100,
            offset=0,
        )

        assert result["total"] == 1
        assert result["items"][0]["prediction_group_id"] == "group-api-1"
        assert result["items"][0]["outcomes"]["5"]["hit"] is True
        assert result["policy"]["horizon_unit"] == "trading_days"
    finally:
        db.close()


def test_summary_only_counts_trading_day_records():
    """舊自然日記錄可瀏覽，但不進入預設命中率彙總。"""
    from src.modules.research.api import evaluations

    db = _mem_db()
    try:
        db.add(_row(5))
        db.add(
            AgentPredictionOutcome(
                agent_name="daily_report",
                stock_symbol="000001",
                stock_market="CN",
                prediction_date="2026-08-28",
                horizon_days=5,
                horizon_unit="calendar_days_legacy",
                action="buy",
                action_label="買入",
                outcome_return_pct=-8.0,
                outcome_status="evaluated",
            )
        )
        db.commit()

        result = evaluations.get_agent_prediction_summary(db=db, days=90)

        assert result["horizons"]["5"]["completed_count"] == 1
        assert result["horizons"]["5"]["hit_rate"] == 1.0
    finally:
        db.close()


def test_status_filter_keeps_all_horizons_for_matched_suggestion():
    """按狀態篩選命中一條 horizon 時，返回的建議仍保留完整 1/5 日結果。"""
    from src.modules.research.api import evaluations

    db = _mem_db()
    try:
        evaluated = _row(1)
        pending = _row(5)
        pending.outcome_status = "pending"
        pending.outcome_price = None
        pending.outcome_return_pct = None
        db.add_all([evaluated, pending])
        db.commit()

        result = evaluations.list_agent_predictions(
            db=db,
            status="evaluated",
            days=90,
            limit=100,
            offset=0,
        )

        assert result["total"] == 1
        assert set(result["items"][0]["outcomes"]) == {"1", "5"}
        assert result["items"][0]["outcomes"]["5"]["status"] == "pending"
    finally:
        db.close()
