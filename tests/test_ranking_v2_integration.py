"""Ranker refresh, consumer boundaries and immutable selection on disposable SQLite."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.modules.strategy import strategy_engine as engine
from src.modules.strategy.ranking import group_instrument_rows, stable_sigmoid_score
from src.platform.persistence.models import (Base, EntryCandidate, StrategySignalRun,
    RankingSnapshot, RankingSnapshotItem, PortfolioSelectionRun, PortfolioConstraintLedger)
from src.platform.persistence.migrations import MIGRATIONS
from tests.test_ranking_v2_selection import _signal


@pytest.fixture
def sessions(monkeypatch, tmp_path):
    connection = create_engine(f"sqlite:///{tmp_path / 'ranking.db'}")
    Base.metadata.create_all(connection)
    with connection.begin() as conn:
        migration = next(m for m in MIGRATIONS if m.version == 130)
        migration.runner(conn)
        migration.runner(conn)
    factory = sessionmaker(bind=connection)
    monkeypatch.setattr(engine, "SessionLocal", factory)
    monkeypatch.setattr(engine, "ensure_strategy_catalog", lambda: None)
    monkeypatch.setattr(engine, "get_strategy_profile_map", lambda: {
        f"s{i}": {"name": f"Strategy {i}", "risk_level": "low", "default_weight": 1}
        for i in range(24)})
    monkeypatch.setattr(engine, "get_primary_horizon_sessions", lambda: {})
    monkeypatch.setattr(engine, "get_effective_weight_map", lambda **kwargs: {})
    monkeypatch.setattr(engine, "get_factor_weights", lambda *args, **kwargs: {})
    monkeypatch.setattr(engine, "_upsert_market_regime_snapshots", lambda **kwargs: {})
    monkeypatch.setattr(engine, "_load_news_metrics", lambda **kwargs: {})
    monkeypatch.setattr(engine, "utc_now", lambda: datetime(2026, 10, 11, 8, tzinfo=timezone.utc))
    with factory() as db:
        for i in range(6):
            db.add(EntryCandidate(snapshot_date="2026-10-11", stock_market="TW",
                stock_symbol=f"TWSE:{2300+i}", instrument_id=f"TWSE:{2300+i}",
                instrument_group="EQUITY", status="active", action="buy",
                score=100-i, plan_quality=95, entry_low=100, entry_high=101,
                candidate_source="watchlist", strategy_tags=[f"s{i*4+j}" for j in range(4)]))
        db.commit()
    yield factory
    connection.dispose()


def test_refresh_archives_reference_and_selection_before_mutable_projection(sessions):
    engine.refresh_strategy_signals(snapshot_date="2026-10-11", rebuild_candidates=False)
    with sessions() as db:
        rows = db.query(StrategySignalRun).all()
        assert len(rows) == 24
        assert sum(row.status == "active" for row in rows) == 20
        assert sum(row.selected_for_portfolio for row in rows) == 5
        assert all(row.confidence is None for row in rows)
        capture = db.query(RankingSnapshot).one()
        assert len(capture.ranking_reference["items"]) == 6
        archived = db.query(RankingSnapshotItem).all()
        assert len(archived) == 24
        assert all(item.eligibility["eligible"] for item in archived)
        assert all(item.ranking_inputs["score_breakdown"]["raw_score"] is not None for item in archived)
        run = db.query(PortfolioSelectionRun).filter_by(instrument_group="EQUITY").one()
        assert (run.before_row_count, run.after_row_count, run.after_instrument_count) == (24, 20, 5)
        assert run.quota_metadata["cap_binding"] is True
        ledger = db.query(PortfolioConstraintLedger).all()
        assert len(ledger) == 24
        assert sum(item.selected_for_portfolio for item in ledger) == 5
        assert all(item.decision_snapshot_id for item in ledger)
        assert all(item.quota_metadata["group_risk_level"] == "low" for item in ledger)
        assert all(item.ranking_inputs["risk_level"] == "low" for item in archived)
        before = [(item.decision_snapshot_id, item.ranking_value) for item in archived]
    with sessions() as db:
        for row in db.query(StrategySignalRun).all():
            row.updated_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        db.commit()
    # Changing projection timestamps changes neither immutable inputs nor selection identity.
    engine.refresh_strategy_signals(snapshot_date="2026-10-11", rebuild_candidates=False)
    with sessions() as db:
        assert db.query(RankingSnapshot).count() == 1
        assert db.query(PortfolioConstraintLedger).count() == 24
        assert [(item.decision_snapshot_id, item.ranking_value) for item in db.query(RankingSnapshotItem).all()] == before
        db.query(StrategySignalRun).delete()
        db.commit()
    with sessions() as db:
        assert db.query(RankingSnapshotItem).count() == 24
        assert db.query(PortfolioConstraintLedger).count() == 24
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text("UPDATE portfolio_selection_runs SET cap=99"))
        db.rollback()


def test_failure_after_ledger_write_rolls_back_capture_and_selection(sessions, monkeypatch):
    original = engine._persist_selection_ledger
    def crash(**kwargs):
        original(**kwargs)
        raise RuntimeError("injected selection crash")
    monkeypatch.setattr(engine, "_persist_selection_ledger", crash)
    with pytest.raises(RuntimeError, match="injected"):
        engine.refresh_strategy_signals(snapshot_date="2026-10-11", rebuild_candidates=False)
    with sessions() as db:
        for model in (StrategySignalRun, RankingSnapshot, RankingSnapshotItem,
                      PortfolioSelectionRun, PortfolioConstraintLedger):
            assert db.query(model).count() == 0


def test_filter_uses_unrounded_score_and_group_risk(sessions):
    with sessions() as db:
        candidate = db.query(EntryCandidate).first()
        for code, risk in (("low-primary", "low"), ("high-secondary", "high")):
            row = _signal(candidate.instrument_id, code, 120, risk=risk)
            row.source_candidate_id = candidate.id
            row.rank_score = 64.996
            row.selection_metadata = {"group_risk_level": "high"}
            db.add(row)
        db.commit()
    assert engine.list_strategy_signals(min_score=65)["items"] == []
    result = engine.list_strategy_signals(min_score=64, risk_level="high")
    assert result["count"] == 1
    assert result["items"][0]["risk_level"] == "high"
    assert result["items"][0]["strategy_count"] == 2
    assert engine.list_strategy_signals(min_score=64, risk_level="low")["count"] == 0


def test_held_management_preserves_explicit_legacy_versions(sessions):
    with sessions() as db:
        for index, version in enumerate(("ranker-v1", "ranker-v2")):
            db.add(StrategySignalRun(snapshot_date="2026-10-11", stock_market="TW",
                stock_symbol=f"TWSE:{2400+index}", instrument_id=f"TWSE:{2400+index}",
                strategy_code="held", status="active", action="hold", is_holding_snapshot=True,
                ranker_version=version, rank_score=80, ranking_value=130 if index else None))
        db.commit()
    result = engine.list_strategy_signals(holding="held", min_score=0)
    assert result["count"] == 2
    assert result["ranker_version"] == "mixed"
    assert result["legacy_omitted_count"] == 0
    assert engine.list_strategy_signals(holding="held", min_score=70)["legacy_omitted_count"] == 1


def test_etf_keeps_legacy_twenty_row_cap():
    rows = [_signal(f"TWSE:{2500+i}", f"s{i*4+j}", 200-i-j/10)
            for i in range(6) for j in range(4)]
    for row in rows:
        row.instrument_group = "ETF"
    stats = engine._apply_portfolio_constraints(rows=rows)
    assert sum(row.status == "active" for row in rows) == 20
    assert sum(row.selected_for_portfolio for row in rows) == 5
    assert stats["by_reason"]["UNHELD_CAP"] == 4
    assert all(row.selection_metadata["constraint_unit"] == "ROW" for row in rows)


def test_invalid_v2_does_not_sort_by_candidate_score_and_ties_use_decision_identity():
    invalid = SimpleNamespace(stock_market="TW", stock_symbol="TWSE:2300", status="active",
        action="buy", ranker_version="ranker-v2", ranking_value=None, rank_score=None,
        score=1000, strategy_code="same", decision_snapshot_id="a")
    valid = SimpleNamespace(**{**vars(invalid), "ranking_value": 110, "rank_score": stable_sigmoid_score(110),
        "score": 0, "decision_snapshot_id": "b"})
    assert group_instrument_rows([invalid, valid])[0][0] is valid
    tied = SimpleNamespace(**{**vars(valid), "decision_snapshot_id": "a", "source_candidate_id": 99})
    valid.source_candidate_id = 1
    assert group_instrument_rows([valid, tied])[0][0] is tied


def test_missing_selection_archive_demotes_duplicate_members(monkeypatch):
    rows = [_signal("TWSE:2330", f"s{i}", 130-i) for i in range(4)]
    for i, row in enumerate(rows):
        row.id = i+1
        row.decision_snapshot_id = f"decision-{i}"
    monkeypatch.setattr(engine, "_persist_selection_ledger", lambda **kwargs: "")
    engine._apply_portfolio_constraints(rows=rows, db=object(), capture_refs={"TW": {
        "capture_id": "capture", "snapshot_id": 1, "scoring_config_version": "config",
        "ranking_reference_hash": "reference", "ranker_version": "ranker-v2",
        "decision_ids_by_signal": {row.id: row.decision_snapshot_id for row in rows}}})
    assert all(row.status != "active" and not row.selected_for_portfolio for row in rows)
    assert all("DECISION_REFERENCE_UNAVAILABLE" in row.constraint_reasons for row in rows)
    assert all(row.selection_metadata["after_row_count"] == 0 for row in rows)


@pytest.mark.parametrize("score,ratio", [(64.999, .08), (65, .12), (74.999, .12),
                                          (75, .18), (84.999, .18), (85, .25)])
def test_sizing_boundaries_use_unrounded_display_score(score, ratio):
    from src.modules.paper_trading.paper_trading_engine import _position_weight
    assert _position_weight(score) == ratio


def test_paper_entries_use_one_selected_representative_per_instrument(sessions, monkeypatch):
    from src.modules.paper_trading import paper_trading_engine as paper
    from src.modules.strategy.backtest.cost_model import CostModel
    from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition

    engine.refresh_strategy_signals(snapshot_date="2026-10-11", rebuild_candidates=False)
    trader = paper.PaperTradingEngine()
    requested = []
    def quotes(symbols):
        requested.extend(symbols)
        return {(market, symbol): {"current_price": 100.5} for symbol, market in symbols}
    monkeypatch.setattr(trader, "_fetch_quotes_map", quotes)
    monkeypatch.setattr(paper, "quote_usable_for_trading", lambda *args: True)
    # Cost availability is independently tested by RC-D; here use a fixed offline cost model.
    monkeypatch.setattr(paper, "_cost_model", lambda *args: CostModel())
    with sessions() as db:
        account = PaperTradingAccount(initial_capital=5_000_000, current_capital=5_000_000,
                                      market_allocations={"TW": 1})
        db.add(account)
        db.commit()
        opened, keys, events = trader._check_entries(db, account)
        assert opened == len(keys) == len(events) == 5
        assert len(requested) == len(set(requested)) == 5
        positions = db.query(PaperTradingPosition).all()
        assert len({row.stock_symbol for row in positions}) == 5
        assert all(db.get(StrategySignalRun, row.signal_run_id).selected_for_portfolio for row in positions)
        assert account.current_capital >= 0
        # A second scan does not generate further quote or budget inputs for open instruments.
        requested.clear()
        assert trader._check_entries(db, account)[0] == 0
        assert requested == []
