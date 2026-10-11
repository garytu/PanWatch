"""Independent RC-D calendar, upgrade, and scheduler acceptance regressions."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from src.platform.persistence.migrations import MIGRATIONS
from src.platform.persistence.models import AppSettings, Base, OutcomeEvaluationAttempt, StrategyOutcomeV2


def test_exchange_calendar_holiday_closure_identity_and_missing_coverage(monkeypatch):
    from src.modules.strategy import outcomes
    from src.platform.scheduling import trading_calendar as calendar
    opens = frozenset(date.fromisoformat(day) for day in (
        "2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09", "2026-01-12",
    ))
    monkeypatch.setattr(calendar, "_TW_TRADING_DATES", opens)
    monkeypatch.setattr(calendar, "_TW_RANGE", (date(2026, 1, 5), date(2026, 1, 12)))
    closure = SimpleNamespace(tw_extra_closed_dates="")
    monkeypatch.setattr("src.platform.runtime.config.Settings", lambda: closure)
    sessions, evidence = outcomes._expected_sessions("TW", "2026-01-05", 3)
    assert sessions == ["2026-01-06", "2026-01-08", "2026-01-09"]
    assert evidence["status"] == "known"
    closure.tw_extra_closed_dates = "2026-01-08"
    changed, changed_evidence = outcomes._expected_sessions("TW", "2026-01-05", 3)
    assert changed == ["2026-01-06", "2026-01-09", "2026-01-12"]
    assert changed_evidence["calendar_version"] != evidence["calendar_version"]
    assert outcomes._expected_sessions("TW", "2026-01-12", 1)[1]["status"] == "unknown"
    assert outcomes._expected_sessions("US", "2026-01-05", 2)[1]["status"] == "unknown"


def test_populated_pre129_upgrade_preserves_legacy_and_matches_orm_schema():
    engine = create_engine("sqlite://")
    runner = next(m.runner for m in MIGRATIONS if m.version == 129)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("CREATE TABLE ranking_snapshot_items (id INTEGER PRIMARY KEY, decision_snapshot_id TEXT UNIQUE)"))
        connection.execute(text("INSERT INTO ranking_snapshot_items VALUES (1, 'old-decision')"))
        connection.execute(text("CREATE TABLE strategy_outcomes (id INTEGER PRIMARY KEY, outcome_return_pct REAL)"))
        connection.execute(text("INSERT INTO strategy_outcomes VALUES (1, 0.0)"))
        runner(connection)
        runner(connection)
        assert connection.execute(text("SELECT * FROM strategy_outcomes")).one() == (1, 0.0)
        assert connection.execute(text("SELECT action, entry_low, score_version FROM ranking_snapshot_items")).one() == ("watch", None, "")
        for table_name in ("strategy_outcomes_v2", "outcome_evaluation_attempts"):
            table = Base.metadata.tables[table_name]
            assert {column["name"] for column in inspect(connection).get_columns(table_name)} == set(table.columns.keys())
            assert {index["name"] for index in inspect(connection).get_indexes(table_name)} == {index.name for index in table.indexes}
        assert inspect(connection).get_unique_constraints("strategy_outcomes_v2")[0]["column_names"] == [
            "decision_snapshot_id", "outcome_population_id", "horizon_sessions", "evaluation_version",
        ]
    with Session(engine) as session:
        outcome = StrategyOutcomeV2(decision_snapshot_id="old-decision", outcome_population_id="candidate-forward-v2", horizon_sessions=1, evaluation_version="upgrade-fixture")
        session.add(outcome)
        session.flush()
        session.add(OutcomeEvaluationAttempt(outcome_id=outcome.id, input_snapshot={"fixture": True}))
        session.commit()
        assert session.query(StrategyOutcomeV2).one().decision_point_in_time_status == "UNVERIFIABLE"
        assert session.query(OutcomeEvaluationAttempt).one().input_snapshot == {"fixture": True}
    engine.dispose()


@pytest.mark.parametrize("entrypoint", ["evaluate_once", "_evaluate_job"])
@pytest.mark.parametrize("result", [{"committed": True}, {}, {"error": "fixture failure"}, {"skipped_running": True}])
def test_scheduler_requires_v2_commit_before_rebalance_and_factor_calibration(tmp_path, monkeypatch, entrypoint, result):
    from src.modules.research import context_scheduler as scheduler_module
    from src.modules.strategy import factor_calibration
    engine = create_engine(f"sqlite:///{tmp_path / 'ordering.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    events = []
    for name in ("evaluate_pending_prediction_outcomes", "evaluate_entry_candidate_outcomes", "evaluate_strategy_outcomes"):
        monkeypatch.setattr(scheduler_module, name, lambda **kwargs: {})

    def evaluate(**kwargs):
        if result.get("committed"):
            with Session(engine) as session:
                session.add(AppSettings(key="rc_d_committed", value="true"))
                session.commit()
            events.append("commit")
        return result

    def rebalance(**kwargs):
        with Session(engine) as session:
            assert session.query(AppSettings).filter_by(key="rc_d_committed").one().value == "true"
        events.append("rebalance")
        return {}

    def calibrate():
        assert events == ["commit", "rebalance"]
        events.append("factor")
        return {}

    monkeypatch.setattr(scheduler_module, "evaluate_point_in_time_outcomes", evaluate)
    monkeypatch.setattr(scheduler_module, "rebalance_strategy_weights", rebalance)
    monkeypatch.setattr(factor_calibration, "calibrate_all_markets", calibrate)
    scheduler = scheduler_module.ContextMaintenanceScheduler()
    response = asyncio.run(getattr(scheduler, entrypoint)())
    assert events == (["commit", "rebalance", "factor"] if result.get("committed") else [])
    assert scheduler._evaluating is False
    if entrypoint == "evaluate_once":
        assert response["post_evaluation_calibration_skipped"] is (not bool(result.get("committed")))
    engine.dispose()


@pytest.mark.parametrize("status", ["AVAILABLE", "PENDING", "UNAVAILABLE"])
def test_benchmark_deadline_rejects_even_valid_late_data_without_stock_rewrite(status):
    from datetime import datetime, timezone
    from src.modules.strategy.outcomes import _append_benchmark_attempt
    deadline = datetime(2026, 2, 15, 11, tzinfo=timezone.utc)
    outcome = StrategyOutcomeV2(
        id=1, decision_snapshot_id="sealed", horizon_sessions=1,
        evaluation_status="COMPLETE", stock_gross_return_pct=0.0,
        stock_input_hash="sealed-input", stock_facts_hash="sealed-facts",
        benchmark_attempt_count=1, benchmark_retry_deadline_at=deadline,
    )
    recorded = []
    _append_benchmark_attempt(
        SimpleNamespace(add=recorded.append), outcome,
        snapshot={"benchmark_id": "TAIEX", "bars": [{"close": "101"}]},
        status=status, reason="", retryable=True, now=deadline,
    )
    assert outcome.benchmark_status == "UNAVAILABLE"
    assert outcome.benchmark_reason_code == "benchmark_retry_deadline_exceeded"
    assert outcome.benchmark_retryable is False
    assert outcome.benchmark_next_retry_at is None
    assert outcome.benchmark_gross_return_pct is None
    assert outcome.relative_net_percentage_points is None
    assert (outcome.stock_input_hash, outcome.stock_facts_hash, outcome.stock_gross_return_pct) == (
        "sealed-input", "sealed-facts", 0.0,
    )
    assert len(recorded) == 1
    assert recorded[0].retryable is False


def test_complete_unfilled_window_missing_session_and_legitimate_zero():
    from datetime import datetime, timezone
    from src.modules.strategy.outcome_kernel import evaluate_execution_stock, evaluate_forward_stock, sha256_json
    sessions = ["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    bars = []
    for day in sessions:
        content = dict(date=day, open=105.0, high=107.0, low=101.0, close=104.0,
                       volume=1000.0, provider="fixture", adjustment_mode="raw",
                       volume_unit="shares", timeframe="day")
        bars.append(dict(content, source_contract="fixture/daily-v1", capture_id=day,
                         content_sha256=sha256_json(content), receipt_at_utc=f"{day}T06:00:00+00:00"))
    clock = datetime(2026, 1, 15, 11, tzinfo=timezone.utc)
    args = dict(expected_session_dates=sessions, horizon_sessions=1, today=clock.date(),
                now=clock, market_timezone="Asia/Taipei", session_close="13:30")
    execution_args = dict(action="buy", is_holding_snapshot=False, entry_low=99,
                          entry_high=100, stop_loss=90, target_price=120)
    expired = evaluate_execution_stock(bars=bars, **args, **execution_args)
    assert (expired["entry_status"], expired["evaluation_status"], expired["reason_code"]) == (
        "NOT_FILLED", "COMPLETE", "entry_window_expired",
    )
    assert expired.get("stock_gross_return_pct") is None
    missing = evaluate_execution_stock(bars=[bars[0], *bars[2:]], **args, **execution_args)
    assert (missing["evaluation_status"], missing["reason_code"]) == (
        "INSUFFICIENT_DATA", "required_session_missing",
    )
    zero = evaluate_forward_stock(bars=bars, **args)
    assert zero["evaluation_status"] == "COMPLETE"
    assert zero["stock_gross_return_pct"] == 0.0
    assert zero["entry_session_date"] != zero["exit_session_date"]


def test_retry_batch_ignores_due_rows_from_other_evaluator_revisions():
    from datetime import datetime, timezone
    from src.modules.strategy import outcomes
    from src.platform.persistence.models import RankingSnapshot, RankingSnapshotItem
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        capture = RankingSnapshot(capture_id="queue-revisions", stock_market="TW", session_date="2026-01-05")
        session.add(capture)
        session.flush()
        for identifier in ("other-revision", "baseline-due"):
            session.add(RankingSnapshotItem(
                decision_snapshot_id=identifier, ranking_snapshot_id=capture.id,
                stock_market="TW", session_date="2026-01-05", instrument_id="TWSE:2330",
                outcome_population_id="candidate-forward-v2",
                evaluation_version="candidate-session-evaluator-v2.1",
            ))
        session.flush()
        for identifier in ("other-revision", "baseline-due"):
            for population, version in (("candidate-forward-v2", "candidate-session-evaluator-v2.1"),
                                        ("strategy-execution-v2", "strategy-execution-evaluator-v2.1")):
                session.add(StrategyOutcomeV2(
                    decision_snapshot_id=identifier, stock_market="TW", session_date="2026-01-05",
                    outcome_population_id=population, horizon_sessions=1, evaluation_version=version,
                    evaluation_status="INSUFFICIENT_DATA" if identifier == "baseline-due" else "COMPLETE",
                    stock_retryable=identifier == "baseline-due", benchmark_retryable=False,
                    stock_next_retry_at=datetime(2026, 1, 14),
                ))
        session.add(StrategyOutcomeV2(
            decision_snapshot_id="other-revision", stock_market="TW", session_date="2026-01-05",
            outcome_population_id="candidate-forward-v2", horizon_sessions=1,
            evaluation_version="candidate-session-evaluator-v2.1@rollout",
            evaluation_status="INSUFFICIENT_DATA", stock_retryable=True, benchmark_retryable=False,
            stock_next_retry_at=datetime(2026, 1, 10),
        ))
        session.commit()
        args = dict(cutoff="2026-01-01", clock=datetime(2026, 1, 15, tzinfo=timezone.utc), limit=1, horizons={1})
        baseline = outcomes._decision_queue(session, evaluation_revision=None, **args)
        revised = outcomes._decision_queue(session, evaluation_revision="rollout", **args)
        assert [item["decision_snapshot_id"] for item in baseline] == ["baseline-due"]
        assert [item["decision_snapshot_id"] for item in revised] == ["other-revision"]
    engine.dispose()


@pytest.mark.parametrize("action,expected_status,expected_reason", [
    ("watch", "COMPLETE", "order_not_applicable"),
    ("hold", "COMPLETE", "order_not_applicable"),
    ("sell", "COMPLETE", "order_not_applicable"),
    ("buy", "INSUFFICIENT_DATA", "entry_window_invalid"),
])
def test_execution_preflight_is_terminal_without_calendar_or_bars(monkeypatch, action, expected_status, expected_reason):
    from datetime import datetime, timezone
    from sqlalchemy.orm import sessionmaker
    from src.modules.strategy import outcomes
    from src.platform.persistence.models import RankingSnapshot, RankingSnapshotItem
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", factory)
    monkeypatch.setattr(outcomes, "_expected_sessions", lambda *args: ([], {"status": "unknown", "reason_code": "calendar_coverage_missing"}))
    monkeypatch.setattr(outcomes, "_bars_for_decision", lambda *args: pytest.fail("preflight must not fetch stock bars"))
    with factory() as session:
        capture = RankingSnapshot(capture_id=f"preflight-{action}", stock_market="TW", session_date="2026-01-05")
        session.add(capture)
        session.flush()
        session.add(RankingSnapshotItem(
            decision_snapshot_id=f"preflight-{action}", ranking_snapshot_id=capture.id,
            stock_market="TW", session_date="2026-01-05", instrument_id="TWSE:2330",
            outcome_population_id="candidate-forward-v2", evaluation_version="candidate-session-evaluator-v2.1",
            action=action, is_holding_snapshot=False,
        ))
        session.commit()
    result = outcomes.evaluate_point_in_time_outcomes(horizons=(1,), now=datetime(2026, 1, 15, tzinfo=timezone.utc))
    assert result["committed"] is True, result
    with factory() as session:
        outcome = session.query(StrategyOutcomeV2).filter_by(outcome_population_id="strategy-execution-v2").one()
        assert (outcome.evaluation_status, outcome.reason_code) == (expected_status, expected_reason)
        assert outcome.stock_retryable is False
        assert outcome.stock_gross_return_pct is None
        assert session.query(OutcomeEvaluationAttempt).filter_by(outcome_id=outcome.id, attempt_kind="stock").count() == 1
    engine.dispose()
