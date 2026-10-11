from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from marketdata.types import TaiwanDiscoveryPool
from marketdata.types import HotStock
from src.modules.strategy import entry_candidates
from src.modules.market.api.discovery import _discovery_run_detail, get_latest_discovery_run
from src.modules.portfolio.api import dashboard as dashboard_api
from src.modules.strategy.backtest.cost_model import resolve_dated_cost_model
from src.modules.strategy.calibration_gate import RANKER_VERSION_V1, capture_decision_snapshot
from src.modules.strategy.discovery_coverage import (
    create_discovery_run,
    finalize_discovery_run,
    prune_discovery_runs,
)
from src.modules.strategy.strategy_engine import _apply_portfolio_constraints
from src.platform.persistence.migrations import MIGRATIONS
from src.platform.persistence.models import (
    Base,
    DiscoveryExclusion,
    DiscoveryRun,
    EntryCandidate,
    RankingSnapshot,
    RankingSnapshotItem,
    StrategySignalRun,
)


def _memory_session() -> tuple[object, Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(bind=engine, expire_on_commit=False)


def _pool() -> TaiwanDiscoveryPool:
    return TaiwanDiscoveryPool(
        items=[],
        status="available",
        catalog_count=2,
        eligible_catalog_count=1,
        scanned_instrument_count=1,
        price_snapshot_count=1,
        catalog_request_count=1,
        price_snapshot_request_count=1,
        catalog_response_row_count=2,
        catalog_unique_count=2,
        catalog_source="twmd:/api/v1/instruments",
        catalog_source_as_of=None,
        catalog_captured_at_utc="2026-10-10T02:00:00+00:00",
        catalog_hash="a" * 64,
        catalog_authority_status="LATEST_ONLY",
        scan_coverage_state="COMPLETE",
        coverage_scope="FULL_ELIGIBLE_CATALOG",
        run_started_at_utc="2026-10-10T01:59:00+00:00",
        run_completed_at_utc="2026-10-10T02:00:00+00:00",
        requested_market="TW",
        requested_venues=["TWSE", "TPEX"],
        scan_limits={"top_n": 20, "max_universe_size": None, "deadline_budget_sec": 12.0},
        stage_id_sets={
            "catalog": {
                "unique": ["TWSE:2330", "TWSE:9999"],
                "eligible": ["TWSE:2330"],
                "excluded": ["TWSE:9999"],
            },
            "universe_bound": {
                "requested": ["TWSE:2330"],
                "not_requested_due_to_limit": [],
            },
            "dispatch": {"attempted": ["TWSE:2330"], "unattempted": []},
            "price_validation": {
                "valid": ["TWSE:2330"],
                "invalid": [],
                "missing_or_failed": [],
                "pending": [],
            },
            "ranking": {"eligible": ["TWSE:2330"], "excluded": []},
            "pool_selection": {"selected": ["TWSE:2330"], "ranked_not_selected": []},
            "candidate_selection": {"selected": [], "excluded": [], "not_selected": ["TWSE:2330"]},
        },
        stage_reason_id_sets={
            "catalog": {"inactive": ["TWSE:9999"]},
            "price_validation": {"valid_price": ["TWSE:2330"]},
        },
        stage_reason_counts={
            "catalog": {"inactive": 1},
            "price_validation": {"valid_price": 1},
        },
        authority_by_instrument_id={
            "TWSE:2330": {
                "instrument_id": "TWSE:2330",
                "venue": "TWSE",
                "security_type": "EQUITY",
                "is_active": True,
                "taxonomy_subtype": "UNKNOWN",
                "tax_category": "UNKNOWN",
                "catalog_source": "twmd:/api/v1/instruments",
                "catalog_source_as_of": None,
                "catalog_hash": "a" * 64,
                "authority_status": "LATEST_ONLY",
                "point_in_time_status": "UNVERIFIABLE",
            }
        },
    )


def _candidate(*, scan_run_ids: list[int] | None = None) -> EntryCandidate:
    return EntryCandidate(
        stock_symbol="TWSE:2330",
        stock_market="TW",
        stock_name="台積電",
        snapshot_date="2026-10-10",
        status="active",
        score=80,
        action="buy",
        action_label="建倉",
        candidate_source="market_scan",
        instrument_id="TWSE:2330",
        instrument_group="EQUITY",
        instrument_authority={
            "instrument_id": "TWSE:2330",
            "venue": "TWSE",
            "security_type": "EQUITY",
            "is_active": True,
            "authority_status": "LATEST_ONLY",
            "catalog_hash": "a" * 64,
        },
        scan_run_ids=scan_run_ids or [],
        scan_run_refs=[],
    )


def test_run_manifest_final_hash_links_candidate_and_freezes_candidate_projection():
    engine, db = _memory_session()
    try:
        pool = _pool()
        row = _candidate()
        db.add(row)
        db.flush()
        run = create_discovery_run(db, pool, mode="turnover", scan_run_key="run-link")
        row.scan_run_ids = [run.id]
        finalize_discovery_run(
            db,
            run,
            pool,
            candidate_rows=[row],
            candidate_by_instrument_id={"TWSE:2330": row},
        )
        db.commit()

        canonical_manifest = json.dumps(
            run.manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        assert run.manifest_hash == hashlib.sha256(canonical_manifest).hexdigest()
        assert run.manifest["scan_limits"]["top_n"] == 20
        assert run.manifest["selection_config"]["mode"] == "turnover"
        assert run.manifest["selection_config"]["top_n"] == 20
        assert run.manifest["selection_config_hash"] == hashlib.sha256(
            json.dumps(
                run.manifest["selection_config"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert run.manifest["candidate_items"][0]["stock_name"] == "台積電"
        assert row.scan_run_id == run.id
        assert row.scan_run_refs == [{
            "scan_run_id": run.id,
            "revision": 1,
            "manifest_hash": run.manifest_hash,
            "source_mode": "turnover",
        }]

        # A later daily-projection edit must not rewrite the run-bound UI facts.
        row.stock_name = "後來改名"
        row.instrument_authority = {"authority_status": "CONFLICTING_CATALOG_ROWS"}
        db.commit()
        response = _discovery_run_detail(db, run)
        assert response["manifest_hash"] == run.manifest_hash
        assert response["items"][0]["stock_name"] == "台積電"
        assert response["items"][0]["instrument_authority"]["catalog_hash"] == "a" * 64
        assert response["items"][0]["scan_run_manifest_hash"] == run.manifest_hash
    finally:
        db.close()
        engine.dispose()


def test_latest_run_api_uses_newest_same_day_sealed_manifest_after_projection_refresh():
    engine, db = _memory_session()
    try:
        pool = _pool()
        candidate = _candidate()
        db.add(candidate)
        db.flush()
        completed_at = datetime(2026, 10, 10, 2, tzinfo=timezone.utc)
        pool = TaiwanDiscoveryPool(**{
            **pool.__dict__,
            "run_completed_at_utc": completed_at.isoformat(),
        })
        first = create_discovery_run(db, pool, mode="turnover", scan_run_key="same-day-first")
        candidate.scan_run_ids = [first.id]
        finalize_discovery_run(
            db, first, pool, candidate_rows=[candidate],
            candidate_by_instrument_id={"TWSE:2330": candidate},
        )
        db.commit()
        first_name = first.manifest["candidate_items"][0]["stock_name"]

        candidate.stock_name = "同日刷新候選"
        candidate.scan_run_ids = [first.id]
        refreshed_authority = {
            **pool.authority_by_instrument_id,
            "TWSE:2330": {
                **pool.authority_by_instrument_id["TWSE:2330"],
                "catalog_hash": "b" * 64,
            },
        }
        refreshed_pool = TaiwanDiscoveryPool(**{
            **pool.__dict__,
            "authority_by_instrument_id": refreshed_authority,
        })
        second = create_discovery_run(db, refreshed_pool, mode="gainers", scan_run_key="same-day-second")
        candidate.scan_run_ids = [first.id, second.id]
        finalize_discovery_run(
            db, second, refreshed_pool, candidate_rows=[candidate],
            candidate_by_instrument_id={"TWSE:2330": candidate},
        )
        db.commit()

        # A later mutable projection edit must not alter either saved run's facts.
        candidate.stock_name = "更晚的投影名稱"
        db.commit()
        result = get_latest_discovery_run(db=db)
        assert result["scan_run_id"] == second.id
        assert result["manifest_hash"] == second.manifest_hash
        assert result["items"][0]["stock_name"] == "同日刷新候選"
        assert result["items"][0]["instrument_authority"]["catalog_hash"] == "b" * 64
        assert result["items"][0]["scan_run_id"] == second.id
        assert first.manifest["candidate_items"][0]["stock_name"] == first_name
        assert first.manifest["candidate_items"][0]["instrument_authority"]["catalog_hash"] == "a" * 64
    finally:
        db.close()
        engine.dispose()


def test_finalize_database_failure_rolls_back_header_candidate_and_ledger():
    engine, db = _memory_session()
    try:
        row = _candidate()
        db.add(row)
        db.flush()
        run = create_discovery_run(db, _pool(), mode="gainers", scan_run_key="run-rollback")
        row.scan_run_ids = [run.id]

        def fail_after_manifest_flush(session, _flush_context, _instances):
            if any(isinstance(value, DiscoveryExclusion) for value in session.new):
                raise RuntimeError("injected ledger write failure")

        event.listen(db, "before_flush", fail_after_manifest_flush)
        with pytest.raises(RuntimeError, match="injected ledger write failure"):
            finalize_discovery_run(
                db,
                run,
                _pool(),
                candidate_rows=[row],
                candidate_by_instrument_id={"TWSE:2330": row},
            )
        event.remove(db, "before_flush", fail_after_manifest_flush)
        db.rollback()

        with Session(bind=engine) as verify:
            assert verify.query(DiscoveryRun).filter_by(scan_run_key="run-rollback").count() == 0
            assert verify.query(EntryCandidate).count() == 0
            assert verify.query(DiscoveryExclusion).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_retention_archives_referenced_runs_and_deletes_unreferenced_runs():
    engine, db = _memory_session()
    try:
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        pool = _pool()
        linked = _candidate()
        db.add(linked)
        db.flush()
        archived = create_discovery_run(db, pool, mode="turnover", scan_run_key="retained")
        linked.scan_run_ids = [archived.id]
        finalize_discovery_run(
            db, archived, pool, candidate_rows=[linked],
            candidate_by_instrument_id={"TWSE:2330": linked},
        )
        archived.retention_expires_at_utc = now.replace(tzinfo=None) - timedelta(seconds=1)

        expired = create_discovery_run(db, pool, mode="gainers", scan_run_key="expired")
        finalize_discovery_run(db, expired, pool, candidate_rows=[], candidate_by_instrument_id={})
        expired.retention_expires_at_utc = now.replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()

        result = prune_discovery_runs(db, now=now)
        db.commit()
        assert result == {"archived": 1, "deleted": 1}
        assert db.query(DiscoveryRun).filter_by(scan_run_key="retained").one().archived is True
        assert db.query(DiscoveryRun).filter_by(scan_run_key="expired").first() is None
        assert linked.scan_run_refs[0]["manifest_hash"] == archived.manifest_hash
    finally:
        db.close()
        engine.dispose()


def test_empty_and_failed_run_can_be_sealed_with_a_measured_missing_state():
    engine, db = _memory_session()
    try:
        pool = _pool()
        pool = TaiwanDiscoveryPool(
            **{
                **pool.__dict__,
                "items": [],
                "status": "catalog_error",
                "catalog_count": 0,
                "eligible_catalog_count": 0,
                "scanned_instrument_count": 0,
                "catalog_response_row_count": 0,
                "catalog_unique_count": 0,
                "catalog_authority_status": "UNKNOWN",
                "scan_coverage_state": "MISSING",
                "coverage_scope": "UNKNOWN",
                "stage_id_sets": {
                    "catalog": {"unique": [], "eligible": [], "excluded": []},
                    "universe_bound": {"requested": [], "not_requested_due_to_limit": []},
                    "dispatch": {"attempted": [], "unattempted": []},
                    "price_validation": {"valid": [], "invalid": [], "missing_or_failed": [], "pending": []},
                    "ranking": {"eligible": [], "excluded": []},
                    "pool_selection": {"selected": [], "ranked_not_selected": []},
                    "candidate_selection": {"selected": [], "excluded": [], "not_selected": []},
                },
                "stage_reason_id_sets": {"catalog": {"catalog_error": []}, "price_validation": {}},
                "stage_reason_counts": {"catalog": {"catalog_error": 0}, "price_validation": {}},
                "authority_by_instrument_id": {},
            }
        )
        run = create_discovery_run(db, pool, mode="turnover", scan_run_key="empty-failed-run")
        finalize_discovery_run(db, run, pool, candidate_rows=[], candidate_by_instrument_id={})
        db.commit()
        assert run.scan_coverage_state == "MISSING"
        assert run.candidate_ids == []
        assert run.manifest["candidate_items"] == []
        assert run.manifest_hash
    finally:
        db.close()
        engine.dispose()


def test_taiwan_signal_constraints_cap_equity_instruments_keep_etf_separate_and_holdings():
    rows = [
        StrategySignalRun(
            stock_market="TW", stock_symbol=f"TWSE:{2300 + index}",
            strategy_code=f"strategy-{index}", status="active", action="buy",
            rank_score=90 - index, is_holding_snapshot=False,
            instrument_group="EQUITY",
        )
        for index in range(21)
    ]
    etf = StrategySignalRun(
        stock_market="TW", stock_symbol="TWSE:00631L", strategy_code="etf-strategy",
        status="active", action="buy", rank_score=88, is_holding_snapshot=False,
        instrument_group="ETF",
    )
    unknown_holding = StrategySignalRun(
        stock_market="TW", stock_symbol="TPEX:00001", strategy_code="holding-strategy",
        status="active", action="hold", rank_score=40, is_holding_snapshot=True,
        instrument_group="UNKNOWN",
    )
    unknown_opportunity = StrategySignalRun(
        stock_market="TW", stock_symbol="TW:UNKNOWN:9999", strategy_code="unknown-strategy",
        status="active", action="buy", rank_score=99, is_holding_snapshot=False,
        instrument_group="UNKNOWN",
    )
    rows.extend([etf, unknown_holding, unknown_opportunity])

    stats = _apply_portfolio_constraints(rows=rows)
    assert sum(row.status == "active" for row in rows[:21]) == 5
    assert sum(row.selected_for_portfolio for row in rows[:21]) == 5
    assert sum("UNHELD_CAP" in row.constraint_reasons for row in rows[:21]) == 16
    assert etf.status == "active"
    assert etf.selected_for_portfolio is True
    assert unknown_opportunity.status == "inactive"
    assert unknown_holding.status == "active"
    assert stats["by_reason"]["INSTRUMENT_GROUP_UNKNOWN"] == 1


def test_twse_and_tpex_same_code_remain_distinct_in_candidate_scan_projection(monkeypatch):
    from src.platform.marketdata.collectors.discovery_collector import EastMoneyDiscoveryCollector

    authorities = {}
    stocks = []
    for index, venue in enumerate(("TWSE", "TPEX", *("TWSE" for _ in range(10)))):
        code = "2330" if index < 2 else f"{2400 + index}"
        instrument_id = f"{venue}:{code}"
        authorities[instrument_id] = {
            "instrument_id": instrument_id,
            "venue": venue,
            "security_type": "EQUITY",
            "is_active": True,
            "authority_status": "LATEST_ONLY",
            "catalog_hash": "a" * 64,
        }
        stocks.append(HotStock(
            symbol=instrument_id,
            market="TW",
            name=f"公司{index}",
            price=100,
            change_pct=float(index),
            turnover=1000 + index,
            volume=10,
            instrument_id=instrument_id,
            venue=venue,
            security_type="EQUITY",
            is_active=True,
        ))

    class FakeCollector:
        async def fetch_taiwan_discovery(self, *, mode, limit):
            assert limit == 20
            return TaiwanDiscoveryPool(
                items=list(stocks), status="available", catalog_count=12,
                eligible_catalog_count=12, scanned_instrument_count=12,
                price_snapshot_count=12, catalog_request_count=1,
                price_snapshot_request_count=1, authority_by_instrument_id=authorities,
            )

        async def fetch_hot_stocks(self, **_kwargs):
            raise AssertionError("TW structured scan should not call the legacy fetch")

    monkeypatch.setattr(entry_candidates, "enabled_market_codes", lambda: ["TW"])
    monkeypatch.setattr(entry_candidates, "EastMoneyDiscoveryCollector", lambda **_kwargs: FakeCollector())
    scanned, runs = entry_candidates._load_market_scan_inputs_with_runs(20)

    assert "TW:TWSE:2330" in scanned
    assert "TW:TPEX:2330" in scanned
    assert scanned["TW:TWSE:2330"]["symbol"] == "TWSE:2330"
    assert scanned["TW:TPEX:2330"]["symbol"] == "TPEX:2330"
    assert scanned["TW:TWSE:2330"]["instrument_id"] == "TWSE:2330"
    assert scanned["TW:TPEX:2330"]["instrument_id"] == "TPEX:2330"
    assert len(runs) == 2


def test_dashboard_queries_equity_before_an_etf_saturated_limit(monkeypatch):
    engine, db = _memory_session()
    calls: list[dict] = []
    equity = {
        "id": 1000, "stock_market": "TW", "stock_symbol": "TWSE:2330",
        "stock_name": "後段普通股", "instrument_group": "EQUITY", "status": "active",
        "action": "buy", "action_label": "建倉", "rank_score": 71,
        "entry_low": 100, "entry_high": 102,
    }
    etfs = [
        {
            **equity,
            "id": index + 1,
            "stock_symbol": f"TWSE:ETF{index:03d}",
            "stock_name": f"ETF {index}",
            "instrument_group": "ETF",
        }
        for index in range(100)
    ]

    def fake_signals(**kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("holding") == "held":
            return {"items": []}
        if kwargs.get("instrument_group") == "EQUITY":
            return {"items": [equity]}
        if kwargs.get("instrument_group") == "ETF":
            return {"items": etfs[: kwargs["limit"]]}
        return {"items": etfs[: kwargs["limit"]] + [equity]}

    monkeypatch.setattr(dashboard_api, "get_strategy_stats", lambda **_kwargs: {"coverage": {"snapshot_date": "2026-10-10"}})
    monkeypatch.setattr(dashboard_api, "list_strategy_signals", fake_signals)
    try:
        result = dashboard_api.get_dashboard_overview(
            market="TW", action_limit=6, risk_limit=6, days=45, db=db
        )
        assert any(item["stock_symbol"] == "TWSE:2330" for item in result["action_center"]["opportunities"])
        assert all(item["instrument_group"] == "EQUITY" for item in result["action_center"]["opportunities"])
        assert result["action_center"]["etf_opportunities"]
        assert all(item["instrument_group"] == "ETF" for item in result["action_center"]["etf_opportunities"])
        assert any(call.get("instrument_group") == "EQUITY" for call in calls)
        assert any(call.get("instrument_group") == "ETF" for call in calls)
    finally:
        db.close()
        engine.dispose()


def test_capture_identity_includes_instrument_authority_and_exact_run_revisions():
    engine, db = _memory_session()
    try:
        now = datetime(2026, 10, 10, 2, tzinfo=timezone.utc)
        authority_v1 = {
            "instrument_id": "TWSE:2330", "venue": "TWSE", "security_type": "EQUITY",
            "is_active": True, "catalog_hash": "a" * 64, "authority_status": "LATEST_ONLY",
        }

        def decision(authority: dict, refs: list[dict], run_ids: list[int]) -> dict:
            return {
                "instrument_id": "TWSE:2330", "symbol": "TWSE:2330",
                "strategy_code": "market_scan", "raw_factor_values": {"alpha_score": 2.0},
                "primary_horizon_sessions": 3, "eligibility": {"eligible": True},
                "instrument_group": "EQUITY", "instrument_authority": authority,
                "scan_run_ids": run_ids, "scan_run_refs": refs,
            }

        ref_v1 = {"scan_run_id": 41, "revision": 1, "manifest_hash": "1" * 64,
                  "source_mode": "turnover"}
        first = capture_decision_snapshot(
            db,
            capture_id="tw-authority-capture",
            market="TW",
            session_date="2026-10-10",
            decisions=[decision(authority_v1, [ref_v1], [41])],
            ranker_version=RANKER_VERSION_V1,
            catalog_authority={"market_scan": 3},
            decision_at_utc=now,
            captured_at_utc=now,
        )
        authority_v2 = {**authority_v1, "catalog_hash": "b" * 64}
        ref_v2 = {**ref_v1, "revision": 2, "manifest_hash": "2" * 64}
        second = capture_decision_snapshot(
            db,
            capture_id="tw-authority-capture",
            market="TW",
            session_date="2026-10-10",
            decisions=[decision(authority_v2, [ref_v2, {**ref_v1, "scan_run_id": 42, "source_mode": "gainers"}], [41, 42])],
            ranker_version=RANKER_VERSION_V1,
            catalog_authority={"market_scan": 3},
            decision_at_utc=now,
            captured_at_utc=now,
        )

        assert second["capture_id"] == "tw-authority-capture-r1"
        assert db.query(RankingSnapshot).count() == 2
        snapshots = db.query(RankingSnapshot).order_by(RankingSnapshot.id).all()
        assert snapshots[0].scan_run_id == 41
        assert snapshots[1].scan_run_id is None
        assert snapshots[1].scan_run_ids == [41, 42]
        old_item = db.query(RankingSnapshotItem).filter_by(ranking_snapshot_id=first["snapshot_id"]).one()
        new_item = db.query(RankingSnapshotItem).filter_by(ranking_snapshot_id=second["snapshot_id"]).one()
        assert old_item.instrument_authority["catalog_hash"] == "a" * 64
        assert old_item.scan_run_refs == [ref_v1]
        assert new_item.instrument_authority["catalog_hash"] == "b" * 64
        assert new_item.scan_run_refs == [ref_v2, {**ref_v1, "scan_run_id": 42, "source_mode": "gainers"}]
        assert snapshots[1].meta["scan_run_refs_by_item"]["TWSE:2330|market_scan"] == new_item.scan_run_refs
    finally:
        db.close()
        engine.dispose()


def test_dated_cost_authority_is_explicit_and_never_infers_tax_from_etf():
    missing = resolve_dated_cost_model(
        instrument_id="TWSE:00631L", session_date="2026-10-10",
        authority={"instrument_id": "TWSE:00631L", "security_type": "ETF", "tax_category": "UNKNOWN"},
    )
    assert missing.reason_code == "cost_authority_missing"
    assert missing.net_available is False

    valid = resolve_dated_cost_model(
        instrument_id="TWSE:2330",
        session_date="2026-10-10",
        authority={
            "instrument_id": "TWSE:2330", "tax_category": "STOCK_TAXABLE",
            "classification_authority": "versioned-local-policy",
            "classification_source": "approved-classification-snapshot",
            "policy_version": "tw-cost-test-v1", "effective_from": "2026-01-01",
            "effective_to": "2026-12-31", "stamp_duty_rate": 0.001,
            "commission_rate": 0.0001, "minimum_commission": 1,
            "transfer_fee_rate": 0, "slippage_bps": 3,
        },
    )
    assert valid.status == "AVAILABLE"
    assert valid.net_available is True
    assert valid.cost_model.cfg.stamp_duty_rate == 0.001

    out_of_date = resolve_dated_cost_model(
        instrument_id="TWSE:2330", session_date="2027-01-01", authority=valid.authority,
    )
    assert out_of_date.reason_code == "cost_authority_date_mismatch"


def test_migration_128_upgrades_populated_pre_128_rows_and_supports_new_writes():
    runner = next(item.runner for item in MIGRATIONS if item.version == 128)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE entry_candidates (id INTEGER PRIMARY KEY, stock_symbol TEXT)"))
        conn.execute(text("CREATE TABLE market_scan_snapshots (id INTEGER PRIMARY KEY, stock_symbol TEXT)"))
        conn.execute(text("CREATE TABLE strategy_signal_runs (id INTEGER PRIMARY KEY, stock_symbol TEXT)"))
        conn.execute(text("CREATE TABLE ranking_snapshots (id INTEGER PRIMARY KEY, capture_id TEXT)"))
        conn.execute(text("CREATE TABLE ranking_snapshot_items (id INTEGER PRIMARY KEY, decision_snapshot_id TEXT)"))
        conn.execute(text("INSERT INTO entry_candidates VALUES (1, 'TWSE:2330')"))
        conn.execute(text("INSERT INTO market_scan_snapshots VALUES (1, 'TWSE:2330')"))
        conn.execute(text("INSERT INTO strategy_signal_runs VALUES (1, 'TWSE:2330')"))
        conn.execute(text("INSERT INTO ranking_snapshots VALUES (1, 'old-capture')"))
        conn.execute(text("INSERT INTO ranking_snapshot_items VALUES (1, 'old-decision')"))
        runner(conn)

        assert "UNKNOWN" == conn.execute(text(
            "SELECT instrument_group FROM entry_candidates WHERE id=1"
        )).scalar_one()
        assert "UNKNOWN" == conn.execute(text(
            "SELECT instrument_group FROM strategy_signal_runs WHERE id=1"
        )).scalar_one()
        assert "UNKNOWN" == conn.execute(text(
            "SELECT instrument_group FROM ranking_snapshot_items WHERE id=1"
        )).scalar_one()
        assert "scan_run_ids" in {column["name"] for column in inspect(conn).get_columns("ranking_snapshots")}
        run_columns = {column["name"] for column in inspect(conn).get_columns("discovery_runs")}
        assert {"selection_config", "selection_config_hash"}.issubset(run_columns)
        conn.execute(text(
            "UPDATE entry_candidates SET instrument_id='TWSE:2330', instrument_group='EQUITY', "
            "instrument_authority=:authority, scan_run_ids='[7]' WHERE id=1"
        ), {"authority": json.dumps({"authority_status": "LATEST_ONLY"})})
        assert conn.execute(text(
            "SELECT instrument_id, instrument_group, scan_run_ids FROM entry_candidates WHERE id=1"
        )).one() == ("TWSE:2330", "EQUITY", "[7]")

    with Session(bind=engine) as db:
        created = DiscoveryRun(scan_run_key="migration-write", source_mode="turnover")
        db.add(created)
        db.commit()
        persisted = db.query(DiscoveryRun).filter_by(scan_run_key="migration-write").one()
        assert persisted.scan_coverage_state == "UNKNOWN"
        assert persisted.requested_venues == ["TWSE", "TPEX"]
        assert persisted.selection_config == {}
    engine.dispose()


def test_refresh_persists_exact_runs_across_database_reopen_and_empty_failure(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'discovery.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(entry_candidates, "SessionLocal", lambda: Session(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(entry_candidates, "_load_latest_suggestions", lambda **_kw: [])
    monkeypatch.setattr(entry_candidates, "_load_holding_keys", lambda: set())
    monkeypatch.setattr(entry_candidates, "_load_quote_fallbacks", lambda *_a: {})
    monkeypatch.setattr(entry_candidates, "_load_kline_fallbacks", lambda keys, **_kw: {key: {} for key in keys})
    quote_calls = []
    monkeypatch.setattr(entry_candidates, "md_stock_data", lambda symbols, market: quote_calls.append((symbols, market)) or [])
    pool = _pool()
    seed = {
        "symbol": "TWSE:2330", "market": "TW", "stock_name": "台積電",
        "candidate_source": "market_scan", "instrument_id": "TWSE:2330", "instrument_group": "EQUITY",
        "instrument_authority": pool.authority_by_instrument_id["TWSE:2330"],
        "scan_run_keys": ["turnover-first", "gainers-first"],
        "quote_seed": {"current_price": 100, "change_pct": 1, "turnover": 1000},
    }
    entries = [{"run_key": f"{mode}-first", "mode": mode, "pool": pool} for mode in ("turnover", "gainers")]
    monkeypatch.setattr(entry_candidates, "_load_market_scan_inputs_with_runs", lambda **_kw: ({"TW:TWSE:2330": seed}, entries))
    result = entry_candidates.refresh_entry_candidates(snapshot_date="2026-10-10")
    assert result["count"] == 1
    assert len(quote_calls) == 1  # Existing candidate batch; no extra per-symbol lookup.
    refs = result["items"][0]["scan_run_refs"]
    assert len(refs) == 2
    engine.dispose()
    with Session(bind=engine) as reopened:
        frozen = [_discovery_run_detail(reopened, reopened.get(DiscoveryRun, ref["scan_run_id"])) for ref in refs]
        sealed = reopened.query(RankingSnapshotItem).filter_by(
            outcome_population_id="candidate-forward-v2",
        ).one()
        sealed_id = sealed.decision_snapshot_id
        sealed_facts = (sealed.capture_hash, sealed.ranking_value, sealed.entry_low,
                        sealed.entry_high, sealed.stop_loss, sealed.target_price,
                        sealed.action, sealed.candidate_id, sealed.scan_run_refs)
        assert sealed.point_in_time_status == "UNVERIFIABLE"
        projection = reopened.get(EntryCandidate, sealed.candidate_id)
        projection.score = -999
        projection.entry_high = 9999
        reopened.commit()
        for detail, ref in zip(frozen, refs):
            assert detail["manifest_hash"] == ref["manifest_hash"]
            assert detail["items"][0]["instrument_id"] == "TWSE:2330"
            assert detail["items"][0]["scan_run_refs"] == [ref]
    failed = entry_candidates._empty_taiwan_discovery_pool(reason="catalog_error")
    monkeypatch.setattr(entry_candidates, "_load_market_scan_inputs_with_runs", lambda **_kw: ({}, [{"run_key": "empty-failure", "mode": "turnover", "pool": failed}]))
    empty = entry_candidates.refresh_entry_candidates(snapshot_date="2026-10-10")
    assert empty["count"] == 0
    assert len(quote_calls) == 1
    engine.dispose()
    with Session(bind=engine) as reopened:
        assert reopened.query(EntryCandidate).filter_by(snapshot_date="2026-10-10").count() == 0
        archived = reopened.query(RankingSnapshotItem).filter_by(decision_snapshot_id=sealed_id).one()
        assert (archived.capture_hash, archived.ranking_value, archived.entry_low,
                archived.entry_high, archived.stop_loss, archived.target_price,
                archived.action, archived.candidate_id, archived.scan_run_refs) == sealed_facts
        latest = reopened.query(DiscoveryRun).filter_by(scan_run_key="empty-failure").one()
        detail = _discovery_run_detail(reopened, latest)
        assert detail["manifest"]["error_reason"] == "catalog_error"
        assert detail["manifest"]["scan_coverage_state"] == "MISSING"
        assert detail["items"] == []
        for old in frozen:
            assert _discovery_run_detail(reopened, reopened.get(DiscoveryRun, old["scan_run_id"]))["manifest_hash"] == old["manifest_hash"]
    # The outcome producer joins the archive after the daily candidate is gone.
    from src.modules.strategy import outcomes
    from src.modules.strategy.outcome_kernel import sha256_json
    from src.platform.persistence.models import StrategyOutcomeV2
    monkeypatch.setattr(outcomes, "SessionLocal", lambda: Session(bind=engine, expire_on_commit=False))
    sessions = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
    monkeypatch.setattr(outcomes, "_expected_sessions", lambda market, day, count: (
        sessions[:count], {"status": "known", "calendar_version": "fixture-calendar",
                           "source": "fixture", "timezone": "Asia/Taipei", "confidence": "OFFICIAL"},
    ))
    bars = []
    for day in sessions:
        content = dict(date=day, open=100.0, high=101.0, low=99.0, close=100.0,
                       volume=1000.0, provider="fixture", adjustment_mode="raw",
                       volume_unit="shares", timeframe="day")
        bars.append(dict(content, content_sha256=sha256_json(content), capture_id=day,
                         source_contract="fixture/daily-v1", receipt_at_utc=f"{day}T06:00:00+00:00"))
    monkeypatch.setattr(outcomes, "_bars_for_decision", lambda *args: bars)
    monkeypatch.setattr(outcomes, "_benchmark_attempt_payload", lambda *args, **kwargs: (
        {"reason_code": "fixture unavailable"}, "UNAVAILABLE", "fixture unavailable", False,
    ))
    evaluation = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), now=datetime(2026, 10, 20, 11, tzinfo=timezone.utc),
    )
    assert evaluation["committed"] is True, evaluation
    with Session(bind=engine) as reopened:
        forward = reopened.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id=sealed_id, outcome_population_id="candidate-forward-v2",
            horizon_sessions=3,
        ).one()
        assert forward.evaluation_status == "COMPLETE"
        assert forward.stock_gross_return_pct == 0.0
        assert forward.decision_score == sealed_facts[1]
        assert forward.decision_capture_hash == sealed_facts[0]
        assert forward.decision_point_in_time_status == "UNVERIFIABLE"
    engine.dispose()


def test_ai_uses_exact_saved_revision_and_reports_unknown_for_stale_reference():
    from src.modules.assistant.tools import _attach_candidate_scan_coverage, _compact_research_candidate

    engine, db = _memory_session()
    try:
        pool = _pool()
        run = create_discovery_run(db, pool, mode="turnover", scan_run_key="ai-exact")
        finalize_discovery_run(db, run, pool, candidate_rows=[], candidate_by_instrument_id={})
        db.commit()
        assert db.query(DiscoveryExclusion).filter_by(discovery_run_id=run.id, reason_code="valid_price").one().outcome == "valid"
        ref = {"scan_run_id": run.id, "revision": run.revision, "manifest_hash": run.manifest_hash, "source_mode": "turnover"}
        newer = create_discovery_run(db, entry_candidates._empty_taiwan_discovery_pool(reason="timeout"), mode="gainers", scan_run_key="ai-newer")
        finalize_discovery_run(db, newer, entry_candidates._empty_taiwan_discovery_pool(reason="timeout"), candidate_rows=[], candidate_by_instrument_id={})
        db.commit()
        items = [{"stock_market": "TW", "scan_run_refs": [ref]}, {"stock_market": "TW", "scan_run_refs": [{**ref, "manifest_hash": "stale"}]}]
        _attach_candidate_scan_coverage(db, items)
        compact = _compact_research_candidate(items[0])
        assert compact["scan_coverage"][0]["scan_run_id"] == run.id
        assert compact["scan_coverage"][0]["valid_price_count"] == 1
        assert compact["scan_coverage"][0]["scan_coverage_state"] == "COMPLETE"
        assert _compact_research_candidate(items[1])["scan_coverage_state"] == "UNKNOWN"
        assert items[1]["scan_coverage"] == []
    finally:
        db.close()
        engine.dispose()


def test_ordinary_query_excludes_etfs_before_limits_for_signal_and_fallback(monkeypatch):
    from src.modules.strategy import strategy_engine

    engine, db = _memory_session()
    try:
        for index in range(25):
            db.add(StrategySignalRun(snapshot_date="2026-10-10", stock_market="TW", stock_symbol=f"TWSE:ETF{index}", strategy_code="test", status="active", action="buy", rank_score=99, instrument_group="ETF"))
            db.add(EntryCandidate(snapshot_date="2026-10-10", stock_market="TW", stock_symbol=f"TWSE:ETF{index}", status="active", score=99, instrument_group="ETF"))
        for market, symbol, group in (("TW", "TWSE:2330", "EQUITY"), ("CN", "600519", "UNKNOWN")):
            db.add(StrategySignalRun(snapshot_date="2026-10-10", stock_market=market, stock_symbol=symbol, strategy_code="test", status="active", action="buy", rank_score=70, instrument_group=group))
            db.add(EntryCandidate(snapshot_date="2026-10-10", stock_market=market, stock_symbol=symbol, status="active", score=70, instrument_group=group))
        db.commit()
        monkeypatch.setattr(strategy_engine, "SessionLocal", lambda: Session(bind=engine))
        monkeypatch.setattr(entry_candidates, "SessionLocal", lambda: Session(bind=engine))
        for query in (strategy_engine.list_strategy_signals, entry_candidates.list_entry_candidates):
            result = query(instrument_group="ORDINARY", limit=2)
            assert {item["stock_symbol"] for item in result["items"]} == {"TWSE:2330", "600519"}
            assert all(item["instrument_group"] == "ETF" for item in query(instrument_group="ETF", limit=2)["items"])
    finally:
        db.close()
        engine.dispose()
