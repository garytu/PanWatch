"""A rebuilt candidate snapshot may reuse IDs for different stocks."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.platform.persistence.database import Base
from src.platform.persistence.models import EntryCandidate, StrategySignalRun
from src.modules.strategy import strategy_engine as engine


def test_strategy_refresh_does_not_reuse_another_stocks_signal(monkeypatch, tmp_path):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'signals.db'}")
    Base.metadata.create_all(db_engine)
    sessions = sessionmaker(bind=db_engine)
    monkeypatch.setattr(engine, "SessionLocal", sessions)
    monkeypatch.setattr(engine, "ensure_strategy_catalog", lambda: None)
    monkeypatch.setattr(engine, "get_strategy_profile_map", lambda: {
        "market_scan": {"name": "市場掃描", "risk_level": "medium", "version": "v1", "default_weight": 1.0}})
    monkeypatch.setattr(engine, "get_effective_weight_map", lambda **kwargs: {})
    monkeypatch.setattr(engine, "get_factor_weights", lambda *args, **kwargs: {})
    monkeypatch.setattr(engine, "_upsert_market_regime_snapshots", lambda **kwargs: {})
    monkeypatch.setattr(engine, "_build_cross_section_features", lambda candidates: {})
    monkeypatch.setattr(engine, "_load_news_metrics", lambda **kwargs: {})
    monkeypatch.setattr(engine, "_compute_factor_breakdown", lambda **kwargs: {"weighted_score": 80})
    monkeypatch.setattr(engine, "_apply_portfolio_constraints", lambda **kwargs: {"demoted": 0})
    monkeypatch.setattr(engine, "_sync_factor_and_risk_snapshots", lambda **kwargs: None)

    with sessions() as db:
        db.add(EntryCandidate(
            id=224, snapshot_date="2026-10-01", stock_symbol="TWSE:6213", stock_market="TW",
            stock_name="聯茂", status="active", score=94, action="buy", action_label="建倉",
            candidate_source="market_scan", entry_low=635.58, entry_high=648.42,
        ))
        db.add(EntryCandidate(
            id=225, snapshot_date="2026-10-01", stock_symbol="TWSE:8039", stock_market="TW",
            stock_name="台虹", status="inactive", score=12, action="avoid", action_label="迴避",
            candidate_source="market_scan",
        ))
        db.add(StrategySignalRun(
            id=802, snapshot_date="2026-10-01", stock_symbol="TWSE:8039", stock_market="TW",
            stock_name="台虹", source_candidate_id=224, strategy_code="market_scan",
            status="active", action="buy", entry_low=635.58, entry_high=648.42,
        ))
        db.commit()

    assert engine.list_strategy_signals(snapshot_date="2026-10-01", status="active")["items"] == []
    engine.refresh_strategy_signals(snapshot_date="2026-10-01", rebuild_candidates=False)

    with sessions() as db:
        assert db.get(StrategySignalRun, 802) is None
        rows = db.query(StrategySignalRun).all()
        assert {(row.stock_symbol, row.source_candidate_id, row.status) for row in rows} == {
            ("TWSE:6213", 224, "active"), ("TWSE:8039", 225, "inactive")}
        assert next(row for row in rows if row.stock_symbol == "TWSE:6213").entry_low == 635.58
        assert next(row for row in rows if row.stock_symbol == "TWSE:8039").entry_low is None


def test_strategy_refresh_clears_signals_when_candidate_snapshot_is_empty(monkeypatch, tmp_path):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(db_engine)
    sessions = sessionmaker(bind=db_engine)
    monkeypatch.setattr(engine, "SessionLocal", sessions)
    monkeypatch.setattr(engine, "ensure_strategy_catalog", lambda: None)
    with sessions() as db:
        db.add(StrategySignalRun(
            snapshot_date="2026-10-01", stock_symbol="TWSE:8039", stock_market="TW",
            source_candidate_id=224, strategy_code="market_scan", status="active", action="buy",
        ))
        db.commit()

    assert engine.refresh_strategy_signals(snapshot_date="2026-10-01")["count"] == 0
    with sessions() as db:
        assert db.query(StrategySignalRun).count() == 0
