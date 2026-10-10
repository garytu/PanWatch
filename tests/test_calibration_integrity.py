"""RC-A PR-0A: calibration containment, immutable cohorts and gated restart."""

import shutil
import sys
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import Session

from src.platform.persistence.models import Base
from src.platform.persistence.database import (
    has_pending_migrations,
    run_versioned_migrations,
)
from src.platform.persistence.models import (
    CalibrationApplication,
    CalibrationBaseline,
    CalibrationModeState,
    CalibrationProvenance,
    FactorWeight,
    FactorWeightHistory,
    RankingSnapshot,
    RankingSnapshotItem,
    StrategyCatalog,
    StrategyFactorSnapshot,
    StrategyOutcome,
    StrategySignalRun,
    StrategyWeight,
    StrategyWeightHistory,
)

from src.modules.strategy.calibration_gate import (
    CALIBRATION_FLOORS,
    CALIBRATION_POLICY_VERSION,
    DEFAULT_BASELINE_NAME,
    MODE_ACTIVE,
    MODE_FROZEN,
    MODE_SHADOW,
    POPULATION_SIGNAL_FORWARD_V2,
    EVALUATION_VERSION_V2,
    apply_calibration_plan,
    cohort_fingerprint,
    decision_snapshot_id_for,
    ensure_calibration_modes,
    ensure_ranking_capture,
    evaluate_readiness,
    export_rollout_baseline,
    live_config_hash,
    plan_calibration,
    plan_calibration_batch,
    read_calibration_mode,
    run_calibration,
    select_cohort,
    set_calibration_mode,
    capture_decision_snapshot,
    RANKER_VERSION_V1,
)
from src.modules.strategy.factor_weights import CALIBRATABLE_FACTORS, get_factor_weights
from src.modules.strategy.factor_calibration import blend, compute_target

RANKER_VERSION = "ranker-v1"
SESSIONS = [f"2026-{m:02d}-{d:02d}" for m in (1, 2, 3) for d in range(1, 29)]


def _capture(db, *, market, session_date, capture_id, rows):
    """Write one immutable capture (snapshot + items) plus UI projection/outcome."""
    snapshot_id = ensure_ranking_capture(
        db,
        capture_id=capture_id,
        market=market,
        session_date=session_date,
        ranker_version=RANKER_VERSION,
        decision_at_utc=datetime.strptime(session_date, "%Y-%m-%d"),
        evaluation_version=EVALUATION_VERSION_V2,
    )
    for row in rows:
        decision_id = decision_snapshot_id_for(
            capture_id=capture_id,
            market=market,
            session_date=session_date,
            instrument_id=row["instrument_id"],
            strategy_code=row["strategy_code"],
            ranker_version=RANKER_VERSION,
        )
        db.add(
            RankingSnapshotItem(
                decision_snapshot_id=decision_id,
                ranking_snapshot_id=snapshot_id,
                stock_market=market,
                stock_symbol=row["symbol"],
                instrument_id=row["instrument_id"],
                strategy_code=row["strategy_code"],
                regime=row["regime"],
                session_date=session_date,
                primary_horizon_sessions=row["primary_horizon_sessions"],
                exit_session_date=row["exit_session_date"],
                exit_session_complete=row.get("exit_session_complete", True),
                decision_at_utc=datetime.strptime(session_date, "%Y-%m-%d"),
                available_at_utc=row["available_at_utc"],
                receipt_at_utc=row["available_at_utc"],
                capture_hash=row["capture_hash"],
                point_in_time_status=row["point_in_time_status"],
                outcome_population_id=row["population"],
                ranker_version=RANKER_VERSION,
                evaluation_version=row["evaluation_version"],
                calibration_policy_version=CALIBRATION_POLICY_VERSION,
                signal_run_id=row["signal_run_id"],
                raw_factor_values=row["factors"],
                factor_versions={code: "factor-v1" for code in row["factors"]},
                **row["factors"],
            )
        )
        db.add(
            StrategySignalRun(
                id=row["signal_run_id"],
                snapshot_date=session_date,
                stock_symbol=row["symbol"],
                stock_market=market,
                strategy_code=row["strategy_code"],
                score=1.0,
                rank_score=1.0,
                holding_days=row["primary_horizon_sessions"],
            )
        )
        db.add(
            StrategyFactorSnapshot(
                signal_run_id=row["signal_run_id"],
                snapshot_date=session_date,
                stock_symbol=row["symbol"],
                stock_market=market,
                strategy_code=row["strategy_code"],
                **row["factors"],
            )
        )
        db.add(
            StrategyOutcome(
                id=row["outcome_id"],
                signal_run_id=row["signal_run_id"],
                strategy_code=row["strategy_code"],
                snapshot_date=session_date,
                stock_symbol=row["symbol"],
                stock_market=market,
                horizon_days=row["primary_horizon_sessions"],
                target_date=row["exit_session_date"],
                outcome_return_pct=row["gross_return_pct"],
                outcome_status="evaluated",
                meta={
                    "stock_gross_return_pct": row["gross_return_pct"],
                    "stock_net_return_pct": row["net_return_pct"],
                    "exit_session_date": row["exit_session_date"],
                    "exit_session_complete": row.get("exit_session_complete", True),
                    "outcome_population_id": row["population"],
                    "evaluation_version": row["evaluation_version"],
                    "decision_snapshot_id": decision_id,
                    "input_hash": row["input_hash"],
                    "outcome_revision": row["outcome_revision"],
                },
            )
        )
    db.commit()


def _row(**kwargs):
    gross = float(kwargs["gross_return_pct"])
    net = kwargs.get("net_return_pct", False)
    if net is False:
        net = gross - 0.1
    elif net is None:
        net = None
    else:
        net = float(net)
    return {
        "symbol": kwargs.get("symbol") or f"{kwargs['market']}{kwargs['unit']}",
        "instrument_id": kwargs.get("instrument_id")
        or f"inst-{kwargs['market']}-{kwargs['unit']}",
        "strategy_code": kwargs.get("strategy_code", "trend_follow"),
        "regime": kwargs.get("regime", "default"),
        "primary_horizon_sessions": kwargs.get("primary_horizon_sessions", 5),
        "exit_session_date": kwargs["exit_session_date"],
        "point_in_time_status": kwargs.get("point_in_time_status", "VERIFIED"),
        "population": kwargs.get("population", POPULATION_SIGNAL_FORWARD_V2),
        "evaluation_version": kwargs.get("evaluation_version", EVALUATION_VERSION_V2),
        "gross_return_pct": gross,
        "net_return_pct": net,
        "signal_run_id": kwargs["signal_run_id"],
        "exit_session_complete": kwargs.get("exit_session_complete", True),
        "available_at_utc": kwargs.get("available_at_utc",
                                       datetime.strptime(kwargs["session_date"], "%Y-%m-%d")),
        "capture_hash": kwargs["capture_hash"]
        if "capture_hash" in kwargs
        else f"cap-{kwargs['market']}-{kwargs['unit']}-{kwargs['session_date']}",
        "outcome_id": kwargs["outcome_id"],
        "input_hash": kwargs.get("input_hash")
        or f"input-{kwargs['market']}-{kwargs['unit']}-{kwargs['session_date']}",
        "outcome_revision": kwargs.get("outcome_revision")
        or f"rev-{kwargs['market']}-{kwargs['unit']}-{kwargs['session_date']}",
        "factors": kwargs.get("factors")
        or {
            "alpha_score": gross,
            "catalyst_score": 0.5,
            "quality_score": 0.5,
            "risk_penalty": 0.5,
            "crowd_penalty": 0.5,
        },
    }


def _seed_cohort(db, *, market="TW", dates=26, units=5, start_index=0,
                 strategy_code="trend_follow", primary_horizon_sessions=5,
                 population=POPULATION_SIGNAL_FORWARD_V2,
                 evaluation_version=EVALUATION_VERSION_V2,
                 point_in_time_status="VERIFIED", capture_prefix=None,
                 id_base=1, net_offset=-0.1, correlated=("alpha_score",)):
    """Write a synthetic cohort whose alpha IC is strongly positive."""
    prefix = capture_prefix or f"c-{market}"
    counter = id_base
    for index, session_date in enumerate(SESSIONS[start_index:start_index + dates]):
        rows = []
        for unit in range(units):
            gross = ((index * units + unit) % 13) - 6
            factors = {code: gross for code in correlated}
            factors.update({code: 0.5 for code in CALIBRATABLE_FACTORS if code not in correlated})
            rows.append(
                _row(
                    market=market,
                    unit=unit,
                    session_date=session_date,
                    gross_return_pct=gross,
                    net_return_pct=gross + net_offset,
                    exit_session_date=SESSIONS[
                        min(index + primary_horizon_sessions, len(SESSIONS) - 1)
                    ],
                    signal_run_id=counter,
                    outcome_id=counter,
                    strategy_code=strategy_code,
                    primary_horizon_sessions=primary_horizon_sessions,
                    population=population,
                    evaluation_version=evaluation_version,
                    point_in_time_status=point_in_time_status,
                    factors=factors,
                )
            )
            counter += 1
        _capture(
            db,
            market=market,
            session_date=session_date,
            capture_id=f"{prefix}-{session_date}",
            rows=rows,
        )
    return counter


@pytest.fixture
def db_session():
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/t.db", echo=False)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    for market in ("TW", "HK"):
        get_factor_weights(market, db=session)
    try:
        yield session
    finally:
        session.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _live_factor_weights(db, market="TW"):
    return {
        row.factor_code: float(row.weight)
        for row in db.query(FactorWeight).filter(FactorWeight.market == market).all()
    }


def _alpha_weight(db, market="TW"):
    row = db.query(FactorWeight).filter(
        FactorWeight.factor_code == "alpha_score", FactorWeight.market == market
    ).first()
    return float(row.weight)


def _proposal():
    """Reuse the existing proposal math on the sealed cohort's own statistics."""

    def _compute(plan):
        stats = plan["readiness"]["per_factor"].get("alpha_score") or {}
        if stats.get("ic") is None and stats.get("ir") is None:
            return None
        target = compute_target("alpha_score", stats.get("ic"), stats.get("ir"))
        if target is None:
            return None
        return round(blend(float(plan["old_weight"]), target), 4)

    return _compute


# ---------------------------------------------------------------- containment

def test_missing_mode_row_is_frozen(db_session):
    assert read_calibration_mode(db_session, kind="factor", market="TW") == MODE_FROZEN
    assert read_calibration_mode(db_session, kind="strategy", market="ZZ") == MODE_FROZEN


def test_modes_seeded_frozen(db_session):
    ensure_calibration_modes(db_session)
    modes = db_session.query(CalibrationModeState).all()
    assert modes
    assert {(m.kind, m.market) for m in modes} >= {("factor", "ALL"), ("strategy", "ALL")}
    assert {m.mode for m in modes} == {MODE_FROZEN}


def test_baseline_archive_is_marked_unvalidated(db_session):
    get_factor_weights("TW", db=db_session)
    export_rollout_baseline(db_session, baseline_name=DEFAULT_BASELINE_NAME,
                            markets=["TW"], kinds=["factor"])
    rows = db_session.query(CalibrationBaseline).all()
    assert rows
    assert {r.status for r in rows} == {"unvalidated-baseline"}
    assert {r.baseline_name for r in rows} == {DEFAULT_BASELINE_NAME}
    export_rollout_baseline(db_session, baseline_name=DEFAULT_BASELINE_NAME,
                            markets=["TW"], kinds=["factor"])
    assert len(db_session.query(CalibrationBaseline).all()) == len(rows)


def test_frozen_gate_keeps_live_weights(db_session):
    _seed_cohort(db_session)
    before = _live_factor_weights(db_session)
    result = run_calibration(
        db_session,
        kind="factor",
        market="TW",
        target="alpha_score",
        compute_new_weight=_proposal(),
    )
    assert result["status"] == "MODE_FROZEN"
    assert _live_factor_weights(db_session) == before
    assert db_session.query(CalibrationApplication).count() == 0
    assert db_session.query(FactorWeightHistory).count() == 0


def test_shadow_gate_reports_without_mutating(db_session):
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_SHADOW,
                         reason="shadow evaluation")
    _seed_cohort(db_session, dates=32)
    before = _live_factor_weights(db_session)
    plan = plan_calibration(db_session, kind="factor", market="TW", target="alpha_score")
    assert plan["mode"] == MODE_SHADOW
    assert plan["readiness"]["ready"] is True
    assert plan["readiness"]["per_factor"]["alpha_score"]["ic"] > 0
    result = apply_calibration_plan(db_session, plan=plan, new_weight=1.25)
    assert result["status"] == "MODE_SHADOW"
    assert _live_factor_weights(db_session) == before
    assert db_session.query(CalibrationApplication).count() == 0
    assert db_session.query(CalibrationProvenance).count() == 0


# ---------------------------------------------------------- cohort immutability

def test_projection_delete_keeps_cohort(db_session):
    _seed_cohort(db_session, dates=8, units=3)
    cohort = select_cohort(db_session, kind="factor", market="TW")
    fingerprint = cohort_fingerprint(cohort, kind="factor", market="TW")
    db_session.query(StrategyFactorSnapshot).delete()
    db_session.query(StrategySignalRun).delete()
    again = select_cohort(db_session, kind="factor", market="TW")
    assert cohort_fingerprint(again, kind="factor", market="TW") == fingerprint
    assert again["counts"]["units"] == cohort["counts"]["units"]


def test_same_day_refresh_adds_no_units(db_session):
    _seed_cohort(db_session, dates=6, units=2)
    first = select_cohort(db_session, kind="factor", market="TW")
    fingerprint = cohort_fingerprint(first, kind="factor", market="TW")
    # A refresh is a new capture over the same units/dates; the first decision wins.
    _seed_cohort(db_session, dates=6, units=2, capture_prefix="r-TW", id_base=500)
    again = select_cohort(db_session, kind="factor", market="TW")
    assert again["counts"]["units"] == first["counts"]["units"]
    assert cohort_fingerprint(again, kind="factor", market="TW") == fingerprint
    assert again["counts"]["rejected"].get("DUPLICATE_UNIT") == 12


def test_input_change_changes_fingerprint(db_session):
    _seed_cohort(db_session, dates=6, units=2)
    before = cohort_fingerprint(select_cohort(db_session, kind="factor", market="TW"),
                                kind="factor", market="TW")
    row = (
        db_session.query(RankingSnapshotItem)
        .filter(RankingSnapshotItem.stock_market == "TW")
        .first()
    )
    row.alpha_score = float(row.alpha_score) + 0.25
    db_session.commit()
    after = cohort_fingerprint(select_cohort(db_session, kind="factor", market="TW"),
                               kind="factor", market="TW")
    assert after != before


def test_duplicate_capture_counts_once(db_session):
    _seed_cohort(db_session, dates=6, units=2)
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 12
    assert cohort["counts"]["dates"] == 6


def test_legacy_population_is_ineligible(db_session):
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-05",
        capture_id="legacy-1",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                exit_session_date="2026-01-12",
                signal_run_id=1,
                outcome_id=1,
                population="legacy-unlabelled",
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("POPULATION_NOT_V2") == 1


def test_old_evaluator_version_fails_readiness(db_session):
    _seed_cohort(db_session, dates=32, units=5, evaluation_version="evaluation-v1-calendar")
    cohort = select_cohort(db_session, kind="factor", market="TW")
    readiness = evaluate_readiness(cohort, market="TW", kind="factor", target="alpha_score")
    assert cohort["counts"]["units"] == 0
    assert readiness["ready"] is False
    assert cohort["counts"]["rejected"].get("EVALUATOR_NOT_V2") == 160


def test_unverified_point_in_time_is_ineligible(db_session):
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-05",
        capture_id="pit-1",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                exit_session_date="2026-01-12",
                signal_run_id=1,
                outcome_id=1,
                point_in_time_status="UNVERIFIED",
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("PIT_UNVERIFIED") == 1


def test_gross_net_pair_required(db_session):
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-05",
        capture_id="net-1",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                net_return_pct=None,
                exit_session_date="2026-01-12",
                signal_run_id=1,
                outcome_id=1,
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("GROSS_NET_UNAVAILABLE") == 1


def test_floors_are_encoded(db_session):
    assert CALIBRATION_FLOORS["TW"] == {
        "units": 60,
        "dates": 20,
        "min_period_samples": 5,
        "ic_periods": 20,
    }
    for code in ("CN", "HK", "US"):
        assert CALIBRATION_FLOORS[code] == CALIBRATION_FLOORS["TW"]
    assert CALIBRATION_FLOORS["ALL"]["units"] == 120
    assert CALIBRATION_FLOORS["ALL"]["dates"] == 30


def test_small_rebound_cohort_fails_floors(db_session):
    _seed_cohort(db_session, dates=6, units=2)
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 12
    readiness = evaluate_readiness(cohort, market="TW", kind="factor", target="alpha_score")
    assert readiness["ready"] is False
    assert any("UNITS_BELOW_FLOOR" in reason for reason in readiness["reasons"])


def test_all_market_requires_two_strata(db_session):
    _seed_cohort(db_session, market="TW", dates=32, units=5)
    cohort = select_cohort(db_session, kind="factor", market="ALL")
    readiness = evaluate_readiness(cohort, market="ALL", kind="factor", target="alpha_score")
    assert readiness["ready"] is False
    assert readiness["qualifying_markets"] == ["TW"]
    assert any("MARKET_STRATA_BELOW_FLOOR" in reason for reason in readiness["reasons"])


def test_all_market_passes_with_two_strata(db_session):
    _seed_cohort(db_session, market="TW", dates=42, units=5)
    _seed_cohort(db_session, market="HK", dates=42, units=5, id_base=10000)
    cohort = select_cohort(db_session, kind="factor", market="ALL")
    readiness = evaluate_readiness(cohort, market="ALL", kind="factor", target="alpha_score")
    assert readiness["ready"] is True
    assert sorted(readiness["qualifying_markets"]) == ["HK", "TW"]


# ---------------------------------------------------------------- restart gate

def test_active_gate_writes_atomically(db_session):
    _seed_cohort(db_session, dates=32)
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    before = _alpha_weight(db_session)
    result = run_calibration(
        db_session, kind="factor", market="TW", target="alpha_score",
        compute_new_weight=_proposal(),
    )
    assert result["status"] == "APPLIED"
    assert _alpha_weight(db_session) != before
    assert db_session.query(CalibrationApplication).count() == 1
    assert db_session.query(CalibrationProvenance).count() == 1
    assert db_session.query(FactorWeightHistory).count() == 1


def test_duplicate_cohort_is_rejected(db_session):
    _seed_cohort(db_session, dates=32)
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    first = run_calibration(
        db_session, kind="factor", market="TW", target="alpha_score",
        compute_new_weight=lambda plan: 1.2,
    )
    assert first["status"] == "APPLIED"
    second = run_calibration(
        db_session, kind="factor", market="TW", target="alpha_score",
        compute_new_weight=lambda plan: 1.4,
    )
    assert second["status"] == "DUPLICATE_COHORT"
    assert _alpha_weight(db_session) == 1.2


def test_stale_config_rolls_back_claim_and_weight(db_session):
    _seed_cohort(db_session, dates=32)
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    plan = plan_calibration(db_session, kind="factor", market="TW", target="alpha_score")
    row = db_session.query(FactorWeight).filter(
        FactorWeight.factor_code == "quality_score", FactorWeight.market == "TW"
    ).first()
    row.weight = 0.123456
    db_session.commit()
    result = apply_calibration_plan(db_session, plan=plan, new_weight=1.9)
    assert result["status"] == "STALE_CONFIG"
    assert db_session.query(CalibrationApplication).count() == 0
    assert db_session.query(FactorWeightHistory).count() == 0
    assert _alpha_weight(db_session) == 1.0


def test_pinned_target_rolls_back_claim(db_session):
    _seed_cohort(db_session, dates=32)
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    row = db_session.query(FactorWeight).filter(
        FactorWeight.factor_code == "alpha_score", FactorWeight.market == "TW"
    ).first()
    row.is_pinned = True
    db_session.commit()
    plan = plan_calibration(db_session, kind="factor", market="TW", target="alpha_score")
    result = apply_calibration_plan(db_session, plan=plan, new_weight=1.9)
    assert result["status"] == "PINNED_OR_OFF"
    assert db_session.query(CalibrationApplication).count() == 0
    assert _alpha_weight(db_session) == 1.0


def test_readiness_not_met_rolls_back_claim(db_session):
    _seed_cohort(db_session, dates=6, units=2)
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="premature restart")
    plan = plan_calibration(db_session, kind="factor", market="TW", target="alpha_score")
    assert plan["readiness"]["ready"] is False
    result = apply_calibration_plan(db_session, plan=plan, new_weight=1.9)
    assert result["status"] == "READINESS_NOT_MET"
    assert db_session.query(CalibrationApplication).count() == 0
    assert _alpha_weight(db_session) == 1.0


def test_strategy_gate_uses_primary_horizon(db_session):
    db_session.query(StrategyCatalog).delete()
    db_session.commit()
    db_session.add(
        StrategyCatalog(
            code="trend_follow",
            name="趨勢延續",
            params={"horizon_days": 5},
        )
    )
    db_session.commit()
    db_session.add(
        StrategyWeight(strategy_code="trend_follow", market="TW", regime="default", weight=1.0)
    )
    db_session.commit()
    _seed_cohort(db_session, dates=32, units=5, strategy_code="trend_follow", net_offset=2.0)
    # A non-primary horizon capture must not enter the cohort.
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-02",
        capture_id="wrong-horizon",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-02",
                gross_return_pct=1.0,
                exit_session_date="2026-01-09",
                signal_run_id=9000,
                outcome_id=9000,
                strategy_code="trend_follow",
                primary_horizon_sessions=2,
            )
        ],
    )
    cohort = select_cohort(db_session, kind="strategy", market="TW")
    assert cohort["counts"]["rejected"].get("NON_PRIMARY_HORIZON") == 1
    readiness = evaluate_readiness(cohort, market="TW", kind="strategy",
                                   target="trend_follow|default")
    assert readiness["ready"] is True

    row = db_session.query(StrategyWeight).filter(
        StrategyWeight.strategy_code == "trend_follow", StrategyWeight.market == "TW"
    ).first()
    assert row is not None
    before = float(row.weight)
    set_calibration_mode(db_session, kind="strategy", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    result = run_calibration(
        db_session,
        kind="strategy",
        market="TW",
        target="trend_follow|default",
        compute_new_weight=lambda plan: 1.31,
    )
    assert result["status"] == "APPLIED"
    assert float(
        db_session.query(StrategyWeight).filter(
            StrategyWeight.strategy_code == "trend_follow", StrategyWeight.market == "TW"
        ).first().weight
    ) == 1.31
    assert db_session.query(StrategyWeightHistory).count() == 1


# ------------------------------------------------------------------- migration

def test_equal_price_units_remain_counted(db_session):
    """A flat price across complete distinct sessions still counts as units."""
    for index, session_date in enumerate(SESSIONS[:3]):
        _capture(
            db_session,
            market="TW",
            session_date=session_date,
            capture_id=f"flat-{session_date}",
            rows=[
                _row(
                    market="TW",
                    unit=unit,
                    session_date=session_date,
                    gross_return_pct=0.0,
                    exit_session_date=SESSIONS[index + 5],
                    signal_run_id=700 + index * 2 + unit,
                    outcome_id=700 + index * 2 + unit,
                )
                for unit in range(2)
            ],
        )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 6
    assert cohort["counts"]["rejected"].get("DUPLICATE_UNIT") is None


def test_missing_session_bar_is_ineligible(db_session):
    """An outcome whose exit session bar is missing cannot join a cohort."""
    _capture(
        db_session,
        market="TW",
        session_date=SESSIONS[0],
        capture_id="incomplete-1",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date=SESSIONS[0],
                gross_return_pct=1.0,
                exit_session_date=SESSIONS[5],
                signal_run_id=800,
                outcome_id=800,
                exit_session_complete=False,
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("EXIT_SESSION_INCOMPLETE") == 1


def test_scheduler_and_api_rebalance_are_frozen(db_session):
    """Scheduled and API entry points share the gate; neither has a force flag."""
    import src.modules.strategy.strategy_engine as strategy_engine
    from src.modules.strategy.strategy_engine import rebalance_strategy_weights
    from src.modules.research.api.recommendations import (
        rebalance_strategy_weights_api,
    )

    original_factory = strategy_engine.SessionLocal
    strategy_engine.SessionLocal = lambda: db_session
    try:
        scheduled = rebalance_strategy_weights(
            window_days=45, min_samples=8, alpha=0.35, regime="default"
        )
        assert scheduled["changed"] == 0
        assert scheduled["mode"] == MODE_FROZEN

        api_result = rebalance_strategy_weights_api(45, 8, 0.35)
        assert api_result["changed"] == 0
        assert api_result["mode"] == MODE_FROZEN
    finally:
        strategy_engine.SessionLocal = original_factory


def test_v2_selector_ignores_mutable_projections(db_session):
    """Legacy projections alone give the v2 selector nothing to read."""
    from src.modules.strategy.factor_eval import evaluate_factor_ic, evaluate_factor_ic_v2

    recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    db_session.add(
        StrategyFactorSnapshot(
            signal_run_id=9100,
            snapshot_date=recent,
            stock_symbol="TW0",
            stock_market="TW",
            strategy_code="trend_follow",
            alpha_score=3.0,
        )
    )
    db_session.add(
        StrategyOutcome(
            id=9100,
            signal_run_id=9100,
            strategy_code="trend_follow",
            snapshot_date=recent,
            stock_symbol="TW0",
            stock_market="TW",
            horizon_days=5,
            target_date=recent,
            outcome_return_pct=3.0,
            outcome_status="evaluated",
        )
    )
    db_session.commit()

    legacy = evaluate_factor_ic(days=90, horizon=5, min_samples=1, market="TW", db=db_session)
    assert legacy["factors"]["alpha_score"]["sample_size"] == 1

    v2 = evaluate_factor_ic_v2(market="TW", db=db_session)
    assert v2["factors"]["alpha_score"]["sample_size"] == 0
    assert v2["ready"] is False
    assert v2["counts"]["units"] == 0


def test_batch_second_target_sees_own_write(db_session):
    """A batch keeps its own writes; only an outside change is stale."""
    _seed_cohort(db_session, dates=32, correlated=("alpha_score", "catalyst_score"))
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    batch = plan_calibration_batch(db_session, kind="factor", market="TW",
                                   targets=["alpha_score", "catalyst_score"])
    first = apply_calibration_plan(db_session, plan=batch["plans"]["alpha_score"],
                                   new_weight=1.3, batch=batch)
    assert first["status"] == "APPLIED"

    second = apply_calibration_plan(db_session, plan=batch["plans"]["catalyst_score"],
                                    new_weight=1.2, batch=batch)
    assert second["status"] == "APPLIED"


def test_outside_config_change_stales_the_batch(db_session):
    """Another writer changing config mid-batch rejects the rest of the batch."""
    _seed_cohort(db_session, dates=32, correlated=("alpha_score", "catalyst_score"))
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    batch = plan_calibration_batch(db_session, kind="factor", market="TW",
                                   targets=["alpha_score", "catalyst_score"])
    first = apply_calibration_plan(db_session, plan=batch["plans"]["alpha_score"],
                                   new_weight=1.3, batch=batch)
    assert first["status"] == "APPLIED"

    outside = db_session.query(FactorWeight).filter(
        FactorWeight.factor_code == "quality_score", FactorWeight.market == "TW"
    ).first()
    outside.weight = 1.7
    db_session.commit()

    second = apply_calibration_plan(db_session, plan=batch["plans"]["catalyst_score"],
                                    new_weight=1.2, batch=batch)
    assert second["status"] == "STALE_CONFIG"
    history = db_session.query(FactorWeightHistory).filter(
        FactorWeightHistory.factor_code == "catalyst_score"
    ).all()
    assert history == []


def test_migration_127_is_idempotent_on_current_schema():
    tmp = tempfile.mkdtemp()
    try:
        engine = create_engine(f"sqlite:///{tmp}/m.db")
        Base.metadata.create_all(engine)
        run_versioned_migrations(engine)
        assert has_pending_migrations(engine) is False
        tables = set(sa_inspect(engine).get_table_names())
        assert {
            "calibration_modes",
            "calibration_baselines",
            "calibration_provenance",
            "calibration_applications",
            "ranking_snapshots",
            "ranking_snapshot_items",
        } <= tables
        columns = {c["name"] for c in sa_inspect(engine).get_columns("strategy_weights")}
        assert {"is_pinned", "auto_calibrate"} <= columns
        with Session(bind=engine) as session:
            modes = session.query(CalibrationModeState).all()
            assert modes
            assert {m.mode for m in modes} == {MODE_FROZEN}
            assert {(m.kind, m.market) for m in modes} >= {("factor", "ALL"),
                                                           ("strategy", "ALL")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_migration_127_upgrades_legacy_shape():
    """Legacy DB: strategy_weights without the new columns and no new tables."""
    from src.platform.persistence.migrations import MIGRATIONS

    runner = next(m.runner for m in MIGRATIONS if m.version == 127)
    tmp = tempfile.mkdtemp()
    try:
        engine = create_engine(f"sqlite:///{tmp}/legacy.db")
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
CREATE TABLE strategy_weights (
    id INTEGER PRIMARY KEY,
    strategy_code TEXT,
    market TEXT,
    regime TEXT,
    weight REAL,
    reason TEXT,
    sample_size INTEGER,
    effective_from TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
"""
                )
            )
            conn.execute(
                text(
                    "INSERT INTO strategy_weights VALUES(1, 'trend_follow', 'HK', 'default', 1.0, '', 0, NULL, NULL, NULL)"
                )
            )
            runner(conn)
        columns = {c["name"] for c in sa_inspect(engine).get_columns("strategy_weights")}
        assert {"is_pinned", "auto_calibrate"} <= columns
        tables = set(sa_inspect(engine).get_table_names())
        assert "calibration_modes" in tables
        with Session(bind=engine) as session:
            modes = session.query(CalibrationModeState).all()
            assert {(m.kind, m.market) for m in modes} == {
                ("factor", "ALL"),
                ("strategy", "ALL"),
                ("factor", "HK"),
                ("strategy", "HK"),
            }
            assert {m.mode for m in modes} == {MODE_FROZEN}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- provenance and cohort scope

def test_verified_label_without_evidence_is_ineligible(db_session):
    """自行標為 VERIFIED 但無時間戳或無 capture hash 的資料仍拒絕。"""
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-05",
        capture_id="pit-evidence",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                exit_session_date="2026-01-12",
                signal_run_id=1,
                outcome_id=1,
                available_at_utc=None,
            ),
            _row(
                market="TW",
                unit=1,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                exit_session_date="2026-01-12",
                signal_run_id=2,
                outcome_id=2,
                capture_hash="",
            ),
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("PIT_TIMESTAMPS_MISSING") == 1
    assert cohort["counts"]["rejected"].get("CAPTURE_HASH_MISSING") == 1


def test_factor_cohort_pins_the_fixed_horizon(db_session):
    """因子樣組只接受固定 5 sessions primary horizon。"""
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-05",
        capture_id="short-horizon",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                exit_session_date="2026-01-08",
                signal_run_id=1,
                outcome_id=1,
                primary_horizon_sessions=3,
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("NON_PRIMARY_HORIZON") == 1


def test_pending_first_decision_stays_canonical(db_session):
    """首未完成決策仍 canonical：後面的 refresh 不會變成樣組成員。"""
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-01",
        capture_id="c-pending",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-01",
                gross_return_pct=1.0,
                exit_session_date="2026-01-08",
                signal_run_id=1,
                outcome_id=1,
            )
        ],
    )
    db_session.query(StrategyOutcome).delete()
    db_session.commit()
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-01",
        capture_id="c-refresh",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-01",
                gross_return_pct=9.0,
                exit_session_date="2026-01-08",
                signal_run_id=2,
                outcome_id=2,
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("OUTCOME_MISSING") == 1
    assert cohort["counts"]["rejected"].get("DUPLICATE_UNIT") == 1


def test_all_layer_keeps_cross_market_units_apart(db_session):
    """ALL 層必須 keep 不同市場相同 instrument_id 的獨立 units。"""
    for market, capture_id, signal_run_id, outcome_id in (
        ("TW", "shared-TW", 1, 1),
        ("HK", "shared-HK", 2, 2),
    ):
        _capture(
            db_session,
            market=market,
            session_date="2026-01-05",
            capture_id=capture_id,
            rows=[
                _row(
                    market=market,
                    unit=0,
                    session_date="2026-01-05",
                    gross_return_pct=1.0,
                    exit_session_date="2026-01-12",
                    signal_run_id=signal_run_id,
                    outcome_id=outcome_id,
                    instrument_id="inst-shared",
                )
            ],
        )
    cohort = select_cohort(db_session, kind="factor", market="ALL")
    assert cohort["counts"]["units"] == 2
    assert cohort["counts"]["rejected"].get("DUPLICATE_UNIT") is None


def test_strategy_target_readiness_is_isolated(db_session):
    """成熟策略的樣組數不計為 rebound 的 readiness。"""
    _seed_cohort(db_session, dates=32, units=5, strategy_code="trend_follow",
                 net_offset=2.0)
    _seed_cohort(db_session, dates=26, units=2, strategy_code="rebound",
                 net_offset=2.0, primary_horizon_sessions=3, id_base=5000)
    batch = plan_calibration_batch(
        db_session,
        kind="strategy",
        market="TW",
        targets=["trend_follow|default", "rebound|default"],
    )
    mature = batch["plans"]["trend_follow|default"]["readiness"]
    rebound = batch["plans"]["rebound|default"]["readiness"]
    assert mature["ready"] is True
    assert rebound["ready"] is False
    assert rebound["training"]["units"] == 36
    assert any("UNITS_BELOW_FLOOR" in reason for reason in rebound["reasons"])
    assert mature["training"]["units"] == 110
    # 不同 target 的 fingerprint 必須 differ（樣組實際進入統計istics）。
    assert (batch["plans"]["trend_follow|default"]["cohort_fingerprint"]
            != batch["plans"]["rebound|default"]["cohort_fingerprint"])


def test_integrity_fault_is_not_duplicate_cohort(db_session):
    """非唯一鍵的 integrity 錯誤向上拋，不會變成 DUPLICATE_COHORT。"""
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    from src.modules.strategy.calibration_gate import _is_application_key_conflict

    def _integrity(message: str) -> IntegrityError:
        return IntegrityError("INSERT calibration_applications", None,
                              sqlite3.OperationalError(message))

    replay = _integrity("UNIQUE constraint failed:"
                        " calibration_applications.kind,"
                        " calibration_applications.target")
    fault = _integrity("NOT NULL constraint failed:"
                       " calibration_applications.target")
    assert _is_application_key_conflict(replay) is True
    assert _is_application_key_conflict(fault) is False

    _seed_cohort(db_session, dates=32)
    set_calibration_mode(db_session, kind="factor", market="TW", mode=MODE_ACTIVE,
                         reason="floors met")
    plan = plan_calibration(db_session, kind="factor", market="TW", target="alpha_score")

    def _boom() -> None:
        raise fault

    # 替替 flush 的 write reservation 時非-key integrity 錯誤 must 向上拋.
    db_session.flush = _boom
    with pytest.raises(IntegrityError):
        apply_calibration_plan(db_session, plan=plan, new_weight=1.2)


def test_migration_127_fk_targets_capture_id():
    """Migration 的 item FK 指向 capture 的 PK，並開啟 foreign_keys 後仍可寫。"""
    import sqlite3

    from src.platform.persistence.migrations import MIGRATIONS

    runner = next(m.runner for m in MIGRATIONS if m.version == 127)
    tmp = tempfile.mkdtemp()
    try:
        db_path = f"{tmp}/fk.db"
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            runner(conn)
            sql = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master WHERE type='table'"
                    " AND name='ranking_snapshot_items'"
                )
            ).first()
        assert sql is not None
        assert "REFERENCES ranking_snapshots (id)" in sql[0]
        assert "ON DELETE CASCADE" not in sql[0]

        # SQLite 只驗證外鍵時 pragma 開啟:錯誤的 target column 在此會 raise。
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("INSERT INTO ranking_snapshots (capture_id) VALUES ('probe-1')")
        con.execute(
            "INSERT INTO ranking_snapshot_items (ranking_snapshot_id, decision_snapshot_id)"
            " VALUES (1, 'probe-decision')"
        )
        con.commit()
        inserted = con.execute(
            "SELECT ranking_snapshot_id FROM ranking_snapshot_items"
        ).fetchall()
        assert [row[0] for row in inserted] == [1]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- capture producer (PR-0A item 3)

def test_capture_producer_writes_items_once(db_session):
    """Producer 的 append-only capture: refresh 時 reuse,不會 add second unit."""
    decisions = [
        {
            "instrument_id": f"TW:123{i}",
            "symbol": f"123{i}",
            "strategy_code": "trend_follow",
            "raw_factor_values": {"alpha_score": 1.0 + i, "catalyst_score": 0.5},
            "primary_horizon_sessions": 5,
        }
        for i in range(3)
    ]
    first = capture_decision_snapshot(
        db_session,
        capture_id="decision-TW-2026-01-05",
        market="TW",
        session_date="2026-01-05",
        decisions=decisions,
        ranker_version=RANKER_VERSION_V1,
    )
    assert first["written"] == 3
    assert first["item_count"] == 3

    # Same-day refresh: same capture_id and same decisions must reuse the sealed items.
    second = capture_decision_snapshot(
        db_session,
        capture_id="decision-TW-2026-01-05",
        market="TW",
        session_date="2026-01-05",
        decisions=[*decisions, decisions[0]],
        ranker_version=RANKER_VERSION_V1,
    )
    assert second["written"] == 0
    assert second["reused"] == 4
    assert second["item_count"] == 3
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    # Three distinct decisions, not six: the duplicate row never became a second unit.
    assert cohort["counts"]["rejected"].get("PIT_UNVERIFIED") == 3


def test_capture_producer_keeps_new_items_ineligible(db_session):
    """New capture 的 provenance 是 UNVERIFIABLE: ineligible until RC-D labels population."""
    capture_decision_snapshot(
        db_session,
        capture_id="decision-TW-2026-01-05",
        market="TW",
        session_date="2026-01-05",
        decisions=[{
            "instrument_id": "TW:1234",
            "symbol": "1234",
            "strategy_code": "trend_follow",
            "raw_factor_values": {"alpha_score": 1.0},
            "primary_horizon_sessions": 5,
        }],
        ranker_version=RANKER_VERSION_V1,
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("PIT_UNVERIFIED") == 1


def test_late_arriving_input_is_not_point_in_time(db_session):
    """Available 的 time after decision is lookahead: reject, not evidence."""
    _capture(
        db_session,
        market="TW",
        session_date="2026-01-05",
        capture_id="late-input",
        rows=[
            _row(
                market="TW",
                unit=0,
                session_date="2026-01-05",
                gross_return_pct=1.0,
                exit_session_date="2026-01-12",
                signal_run_id=1,
                outcome_id=1,
                available_at_utc=datetime.strptime("2026-01-05", "%Y-%m-%d")
                + timedelta(days=1),
            )
        ],
    )
    cohort = select_cohort(db_session, kind="factor", market="TW")
    assert cohort["counts"]["units"] == 0
    assert cohort["counts"]["rejected"].get("PIT_AVAILABILITY_AFTER_DECISION") == 1
