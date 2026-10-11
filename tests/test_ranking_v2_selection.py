from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.strategy.calibration_gate import capture_decision_snapshot
from src.modules.strategy import entry_candidates
from src.modules.strategy.ranking import (
    group_instrument_rows,
    rank_map,
    stable_sigmoid_score,
)
from src.modules.strategy.strategy_engine import (
    _apply_portfolio_constraints,
    _tw_instrument_selection,
)
from src.platform.persistence.migrations import MIGRATIONS
from src.platform.persistence.models import Base, EntryCandidate, RankingSnapshot, RankingSnapshotItem, StrategySignalRun
from scripts.replay_rc_c_frozen import replay


REPLAY_PATH = Path(__file__).parent.parent / "docs/plans/ranking-calibration-integrity/evidence/2026-10-11-rc-c-frozen-synthetic-replay.json"


def _signal(instrument: str, strategy: str, raw: float, *, risk: str = "medium", updated: int = 0):
    return StrategySignalRun(
        snapshot_date="2026-10-11",
        stock_market="TW",
        stock_symbol=instrument,
        instrument_id=instrument,
        instrument_group="EQUITY",
        source_candidate_id=int(instrument.split(":", 1)[-1]) * 10 + sum(map(ord, strategy)),
        updated_at=datetime(2026, 10, 11, tzinfo=timezone.utc) + timedelta(seconds=updated),
        strategy_code=strategy,
        status="active",
        action="buy",
        rank_score=stable_sigmoid_score(raw),
        ranking_value=raw,
        score_version="ranker-v2",
        ranker_version="ranker-v2",
        risk_level=risk,
        is_holding_snapshot=False,
    )


def test_sigmoid_mapping_and_sizing_bands_keep_high_raw_scores_distinct():
    from src.modules.paper_trading.paper_trading_engine import _position_weight

    assert stable_sigmoid_score(110) == pytest.approx(62.24593312, abs=1e-8)
    assert stable_sigmoid_score(130) == pytest.approx(81.75744762, abs=1e-8)
    assert stable_sigmoid_score(150) == pytest.approx(92.41418200, abs=1e-8)
    assert [_position_weight(stable_sigmoid_score(value)) for value in (110, 130, 150)] == [
        0.08, 0.18, 0.25,
    ]


def test_average_rank_ties_missing_values_and_direction_are_deterministic():
    values = {"a": 10.0, "b": 10.0, "c": 5.0, "missing": None, "invalid": float("inf")}
    ranked = rank_map(values)
    assert ranked == {"a": 75.0, "b": 75.0, "c": 0.0, "missing": None, "invalid": None}
    ascending = rank_map(values, reverse=False)
    assert ascending == {"a": 25.0, "b": 25.0, "c": 100.0, "missing": None, "invalid": None}
    assert rank_map({"single": 10.0}) == {"single": 50.0}
    assert rank_map(dict(reversed(list(values.items())))) == ranked


def test_tw_twenty_strategy_rows_share_five_instrument_slots_and_keep_max_group_risk():
    rows = []
    for instrument_index in range(5):
        instrument = f"TWSE:{2300 + instrument_index}"
        for strategy_index in range(4):
            rows.append(_signal(
                instrument,
                f"strategy-{instrument_index}",
                150 - instrument_index * 10 - strategy_index,
                risk="high" if instrument_index == 0 and strategy_index == 3 else "medium",
                updated=instrument_index * 10 + strategy_index,
            ))

    stats = _apply_portfolio_constraints(rows=rows)
    assert sum(row.selected_for_portfolio for row in rows) == 5
    assert sum(row.status == "active" for row in rows) == 20
    assert all(row.selection_metadata["after_row_count"] == 20 for row in rows)
    assert all(row.selection_metadata["after_instrument_count"] == 5 for row in rows)
    selected_representatives = [row for row in rows if row.selected_for_portfolio]
    assert len({row.instrument_id for row in selected_representatives}) == 5
    assert any(row.risk_level == "high" for row in rows if row.instrument_id == "TWSE:2300")
    assert all(
        "DUPLICATE_INSTRUMENT" in row.constraint_reasons
        for row in rows if row.instrument_id == "TWSE:2300" and not row.selected_for_portfolio
    )
    tw_equity = next(run for run in stats["selection_runs"] if run["instrument_group"] == "EQUITY")
    assert tw_equity["high_risk_actual_ratio"] == pytest.approx(0.2)
    assert tw_equity["held_management_row_count"] == 0


def test_tw_representatives_and_selected_instruments_ignore_input_order_and_updated_at():
    def run(reverse: bool):
        rows = []
        for instrument_index in range(6):
            instrument = f"TWSE:{2400 + instrument_index}"
            rows.append(_signal(instrument, f"strategy-{instrument_index}", 200 - instrument_index * 10,
                                updated=(100 if reverse else 0) + instrument_index))
        if reverse:
            rows.reverse()
        _apply_portfolio_constraints(rows=rows)
        selected = sorted(row.instrument_id for row in rows if row.selected_for_portfolio)
        excluded = sorted(
            (row.instrument_id, tuple(row.constraint_reasons))
            for row in rows if "UNHELD_CAP" in row.constraint_reasons
        )
        return selected, excluded

    assert run(False) == run(True)
    assert run(False)[1] == [("TWSE:2405", ("UNHELD_CAP",))]

    def representative(reverse: bool):
        rows = [
            _signal("TWSE:2499", "z_strategy", 120, updated=80 if reverse else 2),
            _signal("TWSE:2499", "a_strategy", 120, updated=2 if reverse else 80),
        ]
        if reverse:
            rows.reverse()
        return group_instrument_rows(rows)[0][0].strategy_code

    assert representative(False) == representative(True) == "a_strategy"


def test_active_count_at_equity_cap_does_not_report_cap_binding():
    rows = [_signal(f"TWSE:{2500 + i}", f"strategy-{i}", 150 - i) for i in range(5)]
    stats = _apply_portfolio_constraints(rows=rows)
    assert sum(row.selected_for_portfolio for row in rows) == 5
    assert stats["by_reason"].get("UNHELD_CAP", 0) == 0


def test_invalid_tw_identity_does_not_block_canonical_capture_selection(monkeypatch):
    import src.modules.strategy.strategy_engine as engine

    valid_rows = [
        _signal(f"TWSE:{2700 + index}", f"strategy-{index}", 150 - index)
        for index in range(5)
    ]
    unknown = _signal("2705", "unknown", 100)
    unknown.instrument_id = ""
    unknown.instrument_group = "UNKNOWN"
    all_rows = [*valid_rows, unknown]
    decision_map = {}
    for index, row in enumerate(all_rows, start=1):
        row.id = index
        row.decision_snapshot_id = f"decision-{index}"
        if row is not unknown:
            decision_map[index] = row.decision_snapshot_id
    monkeypatch.setattr(engine, "_persist_selection_ledger", lambda **_kwargs: "selection-run")

    result = _apply_portfolio_constraints(
        rows=all_rows,
        db=object(),
        capture_refs={"TW": {
            "capture_id": "capture-1",
            "snapshot_id": 1,
            "scoring_config_version": "config-1",
            "ranking_reference_hash": "reference-1",
            "ranker_version": "ranker-v2",
            "decision_ids_by_signal": decision_map,
        }},
        snapshot_date="2026-10-11",
    )
    assert sum(row.selected_for_portfolio for row in valid_rows) == 5
    assert "INSTRUMENT_GROUP_UNKNOWN" in unknown.constraint_reasons
    assert result["selection_run_ids"]


def test_missing_verified_selection_ledger_fails_closed(monkeypatch):
    import src.modules.strategy.strategy_engine as engine

    rows = [_signal(f"TWSE:{2800 + index}", f"strategy-{index}", 150 - index) for index in range(5)]
    decision_map = {}
    for index, row in enumerate(rows, start=1):
        row.id = index
        row.decision_snapshot_id = f"decision-{index}"
        decision_map[index] = row.decision_snapshot_id
    monkeypatch.setattr(engine, "_persist_selection_ledger", lambda **_kwargs: "")

    _apply_portfolio_constraints(
        rows=rows,
        db=object(),
        capture_refs={"TW": {
            "capture_id": "capture-2",
            "snapshot_id": 2,
            "scoring_config_version": "config-2",
            "ranking_reference_hash": "reference-2",
            "ranker_version": "ranker-v2",
            "decision_ids_by_signal": decision_map,
        }},
        snapshot_date="2026-10-11",
    )
    assert all(not row.selected_for_portfolio for row in rows)
    assert all("DECISION_REFERENCE_UNAVAILABLE" in row.constraint_reasons for row in rows)


def test_immutable_reference_and_config_changes_create_repeatable_capture_revisions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            decision = {
                "instrument_id": "TWSE:2330",
                "symbol": "TWSE:2330",
                "strategy_code": "trend_follow",
                "raw_factor_values": {"alpha_score": 1.23456789},
                "ranking_inputs": {"ranking_value": 130.125, "cross_section_features": {"volume": None}},
                "primary_horizon_sessions": 5,
            }
            args = {
                "capture_id": "ranker-reference-v2",
                "market": "TW",
                "session_date": "2026-10-11",
                "decisions": [decision],
                "ranker_version": "ranker-v2",
                "scoring_config_payload": {"mapping": {"center": 100, "scale": 20}},
                "decision_at_utc": datetime(2026, 10, 11, tzinfo=timezone.utc),
                "captured_at_utc": datetime(2026, 10, 11, tzinfo=timezone.utc),
            }
            first = capture_decision_snapshot(
                db, ranking_reference={"items": [{"candidate_id": 7, "feature_values": {"turnover": None}}]},
                **args,
            )
            revised = capture_decision_snapshot(
                db, ranking_reference={"items": [{"candidate_id": 7, "feature_values": {"turnover": 12.5}}]},
                **args,
            )
            replayed = capture_decision_snapshot(
                db, ranking_reference={"items": [{"candidate_id": 7, "feature_values": {"turnover": 12.5}}]},
                **args,
            )
            new_config_args = {
                **args,
                "scoring_config_payload": {"mapping": {"center": 100, "scale": 21}},
            }
            config_revision = capture_decision_snapshot(
                db, ranking_reference={"items": [{"candidate_id": 7, "feature_values": {"turnover": 12.5}}]},
                **new_config_args,
            )
            config_replay = capture_decision_snapshot(
                db, ranking_reference={"items": [{"candidate_id": 7, "feature_values": {"turnover": 12.5}}]},
                **new_config_args,
            )
            assert first["capture_id"] == "ranker-reference-v2"
            assert revised["capture_id"] == "ranker-reference-v2-r1"
            assert replayed["snapshot_id"] == revised["snapshot_id"]
            assert config_revision["capture_id"] == "ranker-reference-v2-r2"
            assert config_replay["snapshot_id"] == config_revision["snapshot_id"]
            snapshots = db.query(RankingSnapshot).order_by(RankingSnapshot.id).all()
            assert len(snapshots) == 3
            assert snapshots[0].ranking_reference["items"][0]["feature_values"]["turnover"] is None
            assert snapshots[1].ranking_reference["items"][0]["feature_values"]["turnover"] == 12.5
            assert snapshots[0].ranking_reference_hash != snapshots[1].ranking_reference_hash
            assert snapshots[1].scoring_config_version != snapshots[2].scoring_config_version
            item = db.query(RankingSnapshotItem).filter_by(ranking_snapshot_id=snapshots[1].id).one()
            assert item.ranking_inputs["ranking_value"] == 130.125
            assert item.alpha_score == 1.23456789
    finally:
        engine.dispose()


def test_candidate_fallback_deduplicates_with_common_representative_before_limit(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(entry_candidates, "SessionLocal", sessions)
    try:
        with sessions() as db:
            db.add_all([
                EntryCandidate(
                    snapshot_date="2026-10-11", stock_market="CN", stock_symbol="600519.A",
                    instrument_id="CN:600519", instrument_group="EQUITY", stock_name="茅臺",
                    status="active", score=99, action="hold", action_label="持有",
                ),
                EntryCandidate(
                    snapshot_date="2026-10-11", stock_market="CN", stock_symbol="600519.B",
                    instrument_id="CN:600519", instrument_group="EQUITY", stock_name="茅臺",
                    status="active", score=90, action="buy", action_label="建倉",
                ),
                EntryCandidate(
                    snapshot_date="2026-10-11", stock_market="CN", stock_symbol="300750",
                    instrument_id="CN:300750", instrument_group="EQUITY", stock_name="寧德時代",
                    status="active", score=80, action="buy", action_label="建倉",
                ),
            ])
            db.commit()

        result = entry_candidates.list_entry_candidates(market="CN", limit=2)
        assert result["count"] == 2
        assert [item["instrument_id"] for item in result["items"]] == ["CN:600519", "CN:300750"]
        assert result["items"][0]["stock_symbol"] == "600519.B"
        assert result["items"][0]["rank_score"] is None
        assert result["items"][0]["score_version"] == "candidate-score-v1"
    finally:
        engine.dispose()


def test_tw_quota_pruning_uses_union_and_records_all_reasons_deterministically():
    rows = [
        _signal("TWSE:2601", "strategy-a", 200, risk="high"),
        _signal("TWSE:2602", "strategy-a", 190, risk="high"),
        _signal("TWSE:2603", "strategy-a", 180, risk="high"),
        _signal("TWSE:2604", "strategy-b", 170),
        _signal("TWSE:2605", "strategy-c", 160),
    ]
    result = _tw_instrument_selection(
        [(row, [row]) for row in rows], cap=5, risk_share=0.32, strategy_share=0.42,
    )
    assert result["excluded"]["TWSE:2603"] == ["HIGH_RISK_RATIO", "STRATEGY_SHARE"]
    assert result["excluded"]["TWSE:2602"] == ["HIGH_RISK_RATIO", "STRATEGY_SHARE"]
    assert {row.instrument_id for row, _ in result["selected"]} == {"TWSE:2601", "TWSE:2604", "TWSE:2605"}


def test_populated_migration_preserves_legacy_versions_and_selection_ledger_is_append_only():
    migration = next(item.runner for item in MIGRATIONS if item.version == 130)
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE ranking_snapshots (id INTEGER PRIMARY KEY, capture_id TEXT)"))
            conn.execute(text("CREATE TABLE ranking_snapshot_items (id INTEGER PRIMARY KEY, decision_snapshot_id TEXT)"))
            conn.execute(text("CREATE TABLE strategy_signal_runs (id INTEGER PRIMARY KEY, snapshot_date TEXT, rank_score REAL)"))
            conn.execute(text("INSERT INTO ranking_snapshots VALUES (1, 'legacy-capture')"))
            conn.execute(text("INSERT INTO ranking_snapshot_items VALUES (1, 'legacy-decision')"))
            conn.execute(text("INSERT INTO strategy_signal_runs VALUES (1, '2026-10-11', 88.5)"))
            migration(conn)
            assert conn.execute(text(
                "SELECT score_version, ranker_version FROM strategy_signal_runs WHERE id=1"
            )).one() == ("legacy-score-v1", "ranker-v1")
            assert conn.execute(text(
                "SELECT ranking_reference, ranking_reference_hash FROM ranking_snapshots WHERE id=1"
            )).one() == ("{}", "")
            assert conn.execute(text(
                "SELECT ranking_inputs FROM ranking_snapshot_items WHERE id=1"
            )).scalar_one() == "{}"

        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO portfolio_selection_runs
                (selection_run_id, stock_market, snapshot_date, scoring_config_version)
                VALUES ('run-1', 'TW', '2026-10-11', 'config-hash')"""))
            conn.execute(text("""INSERT INTO portfolio_constraint_ledger
                (selection_run_id, stock_market, snapshot_date, signal_run_id, decision_snapshot_id)
                VALUES ('run-1', 'TW', '2026-10-11', 1, 'decision-1')"""))
        for statement in (
            "UPDATE portfolio_selection_runs SET policy_version='changed' WHERE selection_run_id='run-1'",
            "DELETE FROM portfolio_selection_runs WHERE selection_run_id='run-1'",
            "UPDATE portfolio_constraint_ledger SET constrained=1 WHERE selection_run_id='run-1'",
            "DELETE FROM portfolio_constraint_ledger WHERE selection_run_id='run-1'",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(statement))
    finally:
        engine.dispose()


def test_frozen_synthetic_replay_is_reproducible_and_explicitly_reports_scope():
    result = replay()
    assert result == json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
    assert result["calibration_mode"] == "FROZEN"
    assert result["calibration_writes"] is False
    assert result["selection"]["constraint_unit"] == "INSTRUMENT"
    assert result["selection"]["after_instrument_count"] == 5
    assert result["exposure"]["selected_position_budget_ratio_v2"] < result["exposure"]["selected_position_budget_ratio_legacy_capped"]
