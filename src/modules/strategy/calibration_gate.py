"""標定門（PR-0A）: mode、immutable cohort、fingerprint、atomic application.

Every weight writer (factor calibration, strategy rebalance, scheduler, API) goes
through this service; there is no force flag and no caller-side bypass.

Design notes (docs/plans/ranking-calibration-integrity/contracts/ranking-vocabulary-v1.md):
- Persisted mode per (kind, market): FROZEN keeps weights, SHADOW only produces
  proposals plus a readiness report, ACTIVE may write after every gate passes.
- Cohorts are selected from immutable ``ranking_snapshot_items``, never from the
  mutable UI projections (StrategySignalRun / StrategyFactorSnapshot).
- Application key = (kind, market, target, policy version, cohort fingerprint).
  A new output scoring_config_version does not release that uniqueness.
- The write transaction inserts the application claim first (the SQLite write
  reservation), then rechecks persisted mode, pin state and expected live config.
  Any failure rolls back the claim together with the weights.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import statistics
from datetime import date as _Date

from sqlalchemy.exc import IntegrityError

from src.modules.strategy.factor_weights import CALIBRATABLE_FACTORS, PENALTY_FACTORS, get_factor_weights
from src.modules.strategy.factor_eval import pearson, spearman
from src.modules.strategy.strategy_catalog import get_primary_horizon_sessions
from src.platform.marketdata.models import enabled_market_codes
from src.platform.persistence.database import SessionLocal
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
    StrategyOutcome,
    StrategyWeight,
    StrategyWeightHistory,
)
from src.platform.scheduling.timezone import utc_now

logger = logging.getLogger(__name__)

# Keep in sync with src/platform/persistence/migrations.py.
CALIBRATION_POLICY_VERSION = "calibration-policy-v1"

MODE_FROZEN = "FROZEN"
MODE_SHADOW = "SHADOW"
MODE_ACTIVE = "ACTIVE"
CALIBRATION_MODES = (MODE_FROZEN, MODE_SHADOW, MODE_ACTIVE)
CALIBRATION_KINDS = ("factor", "strategy")
RANKER_VERSION_V1 = "ranker-v1"

POPULATION_LEGACY_UNLABELLED = "legacy-unlabelled"
POPULATION_SIGNAL_FORWARD_V2 = "signal-forward-v2"
EVALUATION_VERSION_V2 = "evaluation-v2"
POINT_IN_TIME_VERIFIED = "VERIFIED"
POINT_IN_TIME_UNVERIFIABLE = "UNVERIFIABLE"

FACTOR_HORIZON_SESSIONS = 5
HOLDOUT_DECISION_DATES = 5
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 20261010
BOOTSTRAP_CONFIDENCE = 0.95
DEFAULT_BASELINE_NAME = "pre-calibration-v1"

# Conservative engineering defaults, not validated statistical significance.
# Floors only count the training cohort; the last HOLDOUT_DECISION_DATES stay holdout.
CALIBRATION_FLOORS: dict[str, dict[str, int]] = {
    "TW": {"units": 60, "dates": 20, "min_period_samples": 5, "ic_periods": 20},
    "CN": {"units": 60, "dates": 20, "min_period_samples": 5, "ic_periods": 20},
    "HK": {"units": 60, "dates": 20, "min_period_samples": 5, "ic_periods": 20},
    "US": {"units": 60, "dates": 20, "min_period_samples": 5, "ic_periods": 20},
    "ALL": {"units": 120, "dates": 30, "min_period_samples": 5, "ic_periods": 30},
}
ALL_MIN_QUALIFYING_MARKETS = 2


def _is_application_key_conflict(error: BaseException) -> bool:
    """True only for the calibration_applications unique-key replay, not other integrity breaks."""
    message = str(error).lower()
    return "calibration_applications" in message and "unique constraint failed" in message


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- mode gate

def ensure_calibration_modes(db, *, markets=None, kinds=CALIBRATION_KINDS) -> None:
    """Seed persisted FROZEN for enabled markets plus ALL (fail-safe default)."""
    wanted = set(markets if markets is not None else [*enabled_market_codes(), "ALL"])
    for kind in kinds:
        for market in sorted(wanted):
            row = (
                db.query(CalibrationModeState)
                .filter(CalibrationModeState.kind == kind, CalibrationModeState.market == market)
                .first()
            )
            if row:
                continue
            db.add(
                CalibrationModeState(
                    kind=kind,
                    market=market,
                    mode=MODE_FROZEN,
                    reason="pr-0a containment default",
                    policy_version=CALIBRATION_POLICY_VERSION,
                )
            )
    db.commit()


def read_calibration_mode(db, *, kind: str, market: str) -> str:
    """Missing row is FROZEN: never assume a writer may run because a row is absent."""
    row = (
        db.query(CalibrationModeState)
        .filter(CalibrationModeState.kind == kind, CalibrationModeState.market == market)
        .first()
    )
    if not row:
        return MODE_FROZEN
    mode = (row.mode or MODE_FROZEN).strip().upper()
    return mode if mode in CALIBRATION_MODES else MODE_FROZEN


def list_calibration_modes(db) -> list[dict]:
    rows = db.query(CalibrationModeState).order_by(
        CalibrationModeState.kind, CalibrationModeState.market
    ).all()
    return [
        {
            "kind": r.kind,
            "market": r.market,
            "mode": (r.mode or MODE_FROZEN).upper(),
            "reason": r.reason or "",
            "policy_version": r.policy_version or "",
        }
        for r in rows
    ]


def set_calibration_mode(db, *, kind: str, market: str, mode: str, reason: str = "") -> dict:
    """Audited rollout/rollback of one market-kind gate (no API surface in PR-0A)."""
    if kind not in CALIBRATION_KINDS:
        raise ValueError(f"unknown calibration kind: {kind}")
    target = (mode or "").strip().upper()
    if target not in CALIBRATION_MODES:
        raise ValueError(f"unknown calibration mode: {mode}")
    ensure_calibration_modes(db, markets=[market], kinds=[kind])
    row = (
        db.query(CalibrationModeState)
        .filter(CalibrationModeState.kind == kind, CalibrationModeState.market == market)
        .first()
    )
    row.mode = target
    row.reason = reason or "mode change"
    row.policy_version = CALIBRATION_POLICY_VERSION
    row.updated_at = utc_now()
    db.commit()
    return {"kind": kind, "market": market, "mode": target, "reason": row.reason}


# ------------------------------------------------------- live config identity

def live_config_payload(db, *, kind: str, market: str,
                        regime: str = "default") -> list:
    """Exact live weight payload a writer would consume (source config identity)."""
    if kind == "factor":
        rows = (
            db.query(FactorWeight)
            .filter(FactorWeight.market == market)
            .order_by(FactorWeight.factor_code)
            .all()
        )
        return [[r.factor_code, float(r.weight), bool(r.is_pinned),
                 bool(r.auto_calibrate)] for r in rows]
    reg = (regime or "default").strip() or "default"
    rows = (
        db.query(StrategyWeight)
        .filter(
            StrategyWeight.regime == reg,
            StrategyWeight.market.in_(("ALL", market)),
        )
        .order_by(StrategyWeight.strategy_code, StrategyWeight.market)
        .all()
    )
    return [[r.strategy_code, r.market, float(r.weight), bool(r.is_pinned),
             bool(r.auto_calibrate)] for r in rows]


def live_config_hash(db, *, kind: str, market: str, regime: str = "default") -> str:
    """Hash of the live weight payload a writer would consume (stale-config guard)."""
    return _sha256(_canonical(live_config_payload(db, kind=kind, market=market,
                                                  regime=regime)))


def current_weight(db, *, kind: str, market: str, target: str, regime: str = "default"):
    """Current persisted weight for one target (None when the row does not exist)."""
    if kind == "factor":
        row = (
            db.query(FactorWeight)
            .filter(FactorWeight.factor_code == target, FactorWeight.market == market)
            .first()
        )
        return float(row.weight) if row else None
    code, _, reg = target.partition("|")
    row = (
        db.query(StrategyWeight)
        .filter(
            StrategyWeight.strategy_code == code,
            StrategyWeight.market == market,
            StrategyWeight.regime == (reg or (regime or "default")),
        )
        .first()
    )
    return float(row.weight) if row else None


def pin_state(db, *, kind: str, market: str, target: str, regime: str = "default") -> tuple[bool, bool]:
    if kind == "factor":
        row = (
            db.query(FactorWeight)
            .filter(FactorWeight.factor_code == target, FactorWeight.market == market)
            .first()
        )
        if not row:
            return False, True
        return bool(row.is_pinned), bool(row.auto_calibrate)
    code, _, reg = target.partition("|")
    row = (
        db.query(StrategyWeight)
        .filter(
            StrategyWeight.strategy_code == code,
            StrategyWeight.market == market,
            StrategyWeight.regime == (reg or (regime or "default")),
        )
        .first()
    )
    if not row:
        return False, True
    return bool(row.is_pinned), bool(row.auto_calibrate)


# --------------------------------------------------------- baseline archive

def export_rollout_baseline(db, *, baseline_name: str, markets=None,
                            kinds=CALIBRATION_KINDS) -> dict:
    """Archive the weights/config that exist before this rollout, marked unvalidated."""
    wanted = list(markets if markets is not None else [*enabled_market_codes(), "ALL"])
    archived = 0
    for kind in kinds:
        for market in wanted:
            existing = (
                db.query(CalibrationBaseline)
                .filter(CalibrationBaseline.baseline_name == baseline_name,
                        CalibrationBaseline.kind == kind,
                        CalibrationBaseline.market == market)
                .first()
            )
            if existing:
                continue
            payload: list[list] = []
            if kind == "factor":
                rows = (
                    db.query(FactorWeight)
                    .filter(FactorWeight.market == market)
                    .order_by(FactorWeight.factor_code)
                    .all()
                )
                payload = [[r.factor_code, float(r.weight), bool(r.is_pinned),
                            bool(r.auto_calibrate)] for r in rows]
                meta = {"policy_version": CALIBRATION_POLICY_VERSION,
                        "pin_flags": {r.factor_code: [bool(r.is_pinned),
                                                      bool(r.auto_calibrate)]
                                      for r in rows}}
            else:
                rows = (
                    db.query(StrategyWeight)
                    .filter(StrategyWeight.market == market)
                    .order_by(StrategyWeight.strategy_code, StrategyWeight.regime)
                    .all()
                )
                payload = [[f"{r.strategy_code}|{r.regime or 'default'}", float(r.weight),
                            bool(r.is_pinned), bool(r.auto_calibrate), r.market]
                            for r in rows]
                meta = {"policy_version": CALIBRATION_POLICY_VERSION,
                        "pin_flags": {f"{r.strategy_code}|{r.regime or 'default'}":
                                      [bool(r.is_pinned), bool(r.auto_calibrate), r.market]
                                      for r in rows}}
            config_hash = _sha256(_canonical(payload))
            if not payload:
                # Empty-config marker so a later rollout can still find this baseline.
                db.add(
                    CalibrationBaseline(
                        baseline_name=baseline_name,
                        kind=kind,
                        market=market,
                        target="*",
                        weight=1.0,
                        config_hash=config_hash,
                        status="unvalidated-baseline",
                        meta={**meta, "empty_payload": True},
                    )
                )
            for row in payload:
                db.add(
                    CalibrationBaseline(
                        baseline_name=baseline_name,
                        kind=kind,
                        market=market,
                        target=str(row[0]),
                        weight=float(row[1]),
                        config_hash=config_hash,
                        status="unvalidated-baseline",
                        meta=meta,
                    )
                )
            archived += 1
    db.commit()
    return {"baseline_name": baseline_name, "markets": wanted, "archived": archived}


def ensure_rollout_baseline_and_modes(db, *, baseline_name: str = DEFAULT_BASELINE_NAME,
                                      market: str, kind: str) -> None:
    """Called before any versioned weight write: baseline archive plus FROZEN gate."""
    if kind == "factor" and market != "ALL":
        # 缺失因子 lazy seed 為 1.0（既有 semantics）;ALL 層沒有獨立_live 設定。
        get_factor_weights(market, db=db)
    existing = (
        db.query(CalibrationBaseline)
        .filter(CalibrationBaseline.baseline_name == baseline_name,
                CalibrationBaseline.kind == kind,
                CalibrationBaseline.market == market)
        .first()
    )
    if not existing:
        export_rollout_baseline(db, baseline_name=baseline_name, markets=[market], kinds=[kind])
    ensure_calibration_modes(db, markets=[market], kinds=[kind])


# ---------------------------------------------------------------- cohort

def _catalog_horizon_map(db) -> dict[str, int]:
    """Fallback primary-horizon map read from the caller's db (never a second session).

    Only legacy captures without a sealed catalog authority need this; a PR-0A capture
    carries its own horizon map so a later catalog edit cannot move a historical cohort.
    """
    out: dict[str, int] = {}
    for row in db.query(StrategyCatalog).all():
        params = row.params or {}
        raw = params.get("horizon_days", params.get("horizon_sessions"))
        try:
            sessions = int(raw)
        except (TypeError, ValueError):
            sessions = 0
        if sessions > 0:
            out[row.code] = sessions
    return out


def _outcome_provenance(item, outcome, *, kind: str) -> tuple[bool, str]:
    """Exact point-in-time join plus producer provenance for one candidate sample.

    Outcome maturity (exit session, gross/net) is judged only on the outcome side: a
    decision item cannot know its own future completion, so the seal never requires it.
    """
    meta = outcome.meta or {}
    if (item.outcome_population_id or "") != POPULATION_SIGNAL_FORWARD_V2:
        return False, "POPULATION_NOT_V2"
    if (item.evaluation_version or "") != EVALUATION_VERSION_V2:
        return False, "EVALUATOR_NOT_V2"
    if (meta.get("evaluation_version") or "") != EVALUATION_VERSION_V2:
        return False, "OUTCOME_EVALUATOR_NOT_V2"
    if (meta.get("outcome_population_id") or "") != POPULATION_SIGNAL_FORWARD_V2:
        return False, "OUTCOME_POPULATION_NOT_V2"
    if (meta.get("decision_snapshot_id") or "") != item.decision_snapshot_id:
        return False, "DECISION_JOIN_MISMATCH"
    if not (meta.get("input_hash") or ""):
        return False, "INPUT_HASH_MISSING"
    if not (meta.get("outcome_revision") or ""):
        return False, "OUTCOME_REVISION_MISSING"
    if not (meta.get("exit_session_date") or "") or not meta.get("exit_session_complete"):
        return False, "EXIT_SESSION_INCOMPLETE"
    if kind == "factor":
        # Factor IC reads raw factors against gross; a missing net stays usable.
        if meta.get("stock_gross_return_pct") is None:
            return False, "GROSS_UNAVAILABLE"
    elif meta.get("stock_net_return_pct") is None:
        return False, "NET_UNAVAILABLE"
    if outcome.outcome_return_pct is None:
        return False, "RETURN_NULL"
    return True, ""


def _item_evidence(item, *, kind: str, expected_horizon: int | None) -> tuple[bool, str]:
    """Item-side evidence for one archived decision row.

    A caller-written VERIFIED label is not enough: the seal needs the timestamps and the
    capture hash that make the archived payload identifiable, so an unknown point-in-time
    input stays ineligible until the RC-D producer fills the real provenance.

    The item never carries outcome maturity: completion is only knowable after the fact,
    so it is judged on the outcome reference instead of on the immutable decision.
    """
    if (item.point_in_time_status or "").upper() != POINT_IN_TIME_VERIFIED:
        return False, "PIT_UNVERIFIED"
    if not (item.capture_hash or ""):
        return False, "CAPTURE_HASH_MISSING"
    if item.decision_at_utc is None or item.available_at_utc is None:
        return False, "PIT_TIMESTAMPS_MISSING"
    if item.available_at_utc > item.decision_at_utc:
        # 輸入 arrived after the decision: that is lookahead, not point-in-time evidence.
        return False, "PIT_AVAILABILITY_AFTER_DECISION"
    if (item.outcome_population_id or "") != POPULATION_SIGNAL_FORWARD_V2:
        return False, "POPULATION_NOT_V2"
    if (item.evaluation_version or "") != EVALUATION_VERSION_V2:
        return False, "EVALUATOR_NOT_V2"
    if item.primary_horizon_sessions is None:
        return False, "PRIMARY_HORIZON_MISSING"
    if kind == "strategy":
        # The strategy horizon comes from the capture's sealed catalog authority, so a
        # later catalog edit cannot move a historical cohort in or out of the sample.
        if expected_horizon is None or int(item.primary_horizon_sessions) != int(
            expected_horizon
        ):
            return False, "NON_PRIMARY_HORIZON"
    return True, ""


def cohort_for_target(cohort: dict, *, kind: str, target: str | None = None) -> dict:
    """Scope a sealed cohort to one strategy target (factor cohorts are already per target).

    Strategy readiness, floors and fingerprint must count only the target strategy's own
    units; a two-unit rebound cohort may not borrow a mature strategy's sample.
    """
    if kind != "strategy" or not target:
        return cohort
    code = target.partition("|")[0]
    rows = [s for s in cohort["samples"] if (s["strategy_code"] or "") == code]
    return {
        **cohort,
        "samples": rows,
        "decision_dates": sorted({s["session_date"] for s in rows}),
        "counts": {
            **cohort["counts"],
            "units": len(rows),
            "dates": len({s["session_date"] for s in rows}),
        },
        "ranker_versions": sorted({s.get("ranker_version") or "" for s in rows}),
    }


def decision_snapshot_id_for(*, capture_id: str, market: str, session_date: str,
                             instrument_id: str, strategy_code: str, ranker_version: str) -> str:
    """Persistent id for one immutable item; retry of the same capture reuses it."""
    return _sha256(_canonical({
        "capture_id": capture_id,
        "market": market,
        "session_date": session_date,
        "instrument_id": instrument_id,
        "strategy_code": strategy_code,
        "ranker_version": ranker_version,
    }))


def ensure_ranking_capture(db, *, capture_id: str, market: str, session_date: str,
                           ranker_version: str, decision_at_utc=None,
                           captured_at_utc=None, source_pool: str = "",
                           rank_source_mode: str = "REPLAY", item_count: int = 0,
                           scoring_config_version: str = "",
                           evaluation_version: str = "",
                           catalog_authority: dict | None = None,
                           calibration_policy_version: str = CALIBRATION_POLICY_VERSION) -> int:
    """Create the capture row when missing; retry of a capture_id reuses the row."""
    existing = (
        db.query(RankingSnapshot)
        .filter(RankingSnapshot.capture_id == capture_id)
        .first()
    )
    if existing:
        return int(existing.id)
    row = RankingSnapshot(
        capture_id=capture_id,
        stock_market=market,
        session_date=session_date,
        decision_at_utc=decision_at_utc,
        captured_at_utc=captured_at_utc,
        source_pool=source_pool,
        item_count=item_count,
        ranker_version=ranker_version,
        scoring_config_version=scoring_config_version,
        evaluation_version=evaluation_version,
        calibration_policy_version=calibration_policy_version,
        catalog_authority=dict(catalog_authority or {}),
        meta={"rank_source_mode": rank_source_mode},
    )
    db.add(row)
    db.commit()
    return int(row.id)


def capture_decision_snapshot(db, *, capture_id: str, market: str, session_date: str,
                              decisions: list[dict], ranker_version: str,
                              scoring_config_version: str = "",
                              evaluation_version: str = "",
                              rank_source_mode: str = "REPLAY",
                              decision_at_utc=None, captured_at_utc=None,
                              catalog_authority: dict | None = None,
                              population: str = POPULATION_LEGACY_UNLABELLED,
                              calibration_policy_version: str = CALIBRATION_POLICY_VERSION) -> dict:
    """Append-only capture: one header plus one immutable item per decision row.

    Retry of the same capture_id reuses the header and the item keyed by
    decision_snapshot_id, so a same-day refresh never revises archived factors and
    never adds a second unit for the same instrument/strategy decision. The writer
    does not update an existing item; a same capture_id carrying different content is
    reported as an identity conflict, never silently ignored.
    """
    decision_at = decision_at_utc
    captured_at = captured_at_utc
    snapshot_id = ensure_ranking_capture(
        db,
        capture_id=capture_id,
        market=market,
        session_date=session_date,
        ranker_version=ranker_version,
        decision_at_utc=decision_at,
        captured_at_utc=captured_at,
        source_pool=rank_source_mode,
        scoring_config_version=scoring_config_version,
        evaluation_version=evaluation_version,
        catalog_authority=catalog_authority,
        calibration_policy_version=calibration_policy_version,
    )
    written = reused = conflicts = 0
    seen_hashes: dict[str, str] = {}
    for row in decisions:
        instrument_id = str(row.get("instrument_id") or "").strip()
        strategy_code = str(row.get("strategy_code") or "").strip()
        if not instrument_id or not strategy_code:
            continue
        decision_id = decision_snapshot_id_for(
            capture_id=capture_id,
            market=market,
            session_date=session_date,
            instrument_id=instrument_id,
            strategy_code=strategy_code,
            ranker_version=ranker_version,
        )
        factors = row.get("raw_factor_values") or {}
        item_hash = _sha256(_canonical({
            "decision_snapshot_id": decision_id,
            "market": market,
            "session_date": session_date,
            "instrument_id": instrument_id,
            "strategy_code": strategy_code,
            "ranker_version": ranker_version,
            "factors": factors,
            "calibration_policy_version": calibration_policy_version,
        }))
        if decision_id in seen_hashes:
            if seen_hashes[decision_id] != item_hash:
                # One batch carries two different payloads for the same decision.
                conflicts += 1
                continue
            reused += 1
            continue
        existing = (
            db.query(RankingSnapshotItem)
            .filter(RankingSnapshotItem.decision_snapshot_id == decision_id)
            .first()
        )
        if existing:
            if (existing.capture_hash or "") != item_hash:
                # Same capture_id, different content: a correction, never a silent ignore.
                conflicts += 1
                continue
            reused += 1
            continue
        seen_hashes[decision_id] = item_hash
        item = RankingSnapshotItem(
            decision_snapshot_id=decision_id,
            ranking_snapshot_id=snapshot_id,
            stock_market=market,
            stock_symbol=str(row.get("symbol") or instrument_id),
            instrument_id=instrument_id,
            strategy_code=strategy_code,
            regime=str(row.get("regime") or "default"),
            session_date=session_date,
            decision_at_utc=decision_at,
            captured_at_utc=captured_at,
            available_at_utc=row.get("available_at_utc") or decision_at,
            receipt_at_utc=row.get("receipt_at_utc") or captured_at,
            quote_source_time=str(row.get("quote_source_time") or ""),
            bar_source_time=str(row.get("bar_source_time") or ""),
            point_in_time_status=str(row.get("point_in_time_status")
                                    or POINT_IN_TIME_UNVERIFIABLE),
            outcome_population_id=str(row.get("population") or population),
            evaluation_version=str(row.get("evaluation_version") or evaluation_version),
            ranker_version=ranker_version,
            scoring_config_version=scoring_config_version,
            calibration_policy_version=calibration_policy_version,
            primary_horizon_sessions=row.get("primary_horizon_sessions"),
            exit_session_date=str(row.get("exit_session_date") or ""),
            exit_session_complete=row.get("exit_session_complete"),
            raw_factor_values=factors,
            factor_versions={code: str(row.get("factor_version") or "")
                             for code in factors},
            eligibility=row.get("eligibility") or {},
            ranking_value=row.get("ranking_value"),
            candidate_id=row.get("candidate_id"),
            signal_run_id=row.get("signal_run_id"),
        )
        for code in CALIBRATABLE_FACTORS:
            if factors.get(code) is not None:
                # A factor the ranker did not emit stays NULL, never a zero sample.
                setattr(item, code, float(factors[code]))
        item.capture_hash = item_hash
        db.add(item)
        written += 1
    db.commit()
    total = (
        db.query(RankingSnapshotItem)
        .filter(RankingSnapshotItem.ranking_snapshot_id == snapshot_id)
        .count()
    )
    capture = (
        db.query(RankingSnapshot)
        .filter(RankingSnapshot.id == snapshot_id)
        .first()
    )
    if capture is not None:
        capture.item_count = int(total)
        # The header hash identifies the sealed payload set, so a replay can be checked.
        capture.capture_hash = _sha256(_canonical(sorted(
            (row.decision_snapshot_id, row.capture_hash or "")
            for row in db.query(RankingSnapshotItem).all()
            if row.ranking_snapshot_id == snapshot_id
        )))
        db.commit()
    return {
        "capture_id": capture_id,
        "snapshot_id": snapshot_id,
        "written": written,
        "reused": reused,
        "conflicts": conflicts,
        "item_count": int(total),
    }


def select_cohort(db, *, kind: str, market: str,
                  primary_horizon_sessions: int | None = None) -> dict:
    """Select the immutable cohort for one market/kind on a sealed read snapshot."""
    if kind not in CALIBRATION_KINDS:
        raise ValueError(f"unknown calibration kind: {kind}")

    # Primary horizon comes from the capture's sealed catalog authority. The live catalog
    # is only a fallback for legacy captures that predate PR-0A, and it is read from the
    # same db so a writer never mixes two connections' views.
    authority = {
        int(snap.id): (snap.catalog_authority or {})
        for snap in db.query(RankingSnapshot).all()
    }
    live_horizons: dict | None = None
    forced_horizon = (int(primary_horizon_sessions)
                      if primary_horizon_sessions is not None else None)

    def _expected_horizon(item) -> int | None:
        nonlocal live_horizons
        if forced_horizon is not None:
            return forced_horizon
        if kind != "strategy":
            return FACTOR_HORIZON_SESSIONS
        sealed = (authority.get(int(item.ranking_snapshot_id))
                  if item.ranking_snapshot_id is not None else None)
        if sealed:
            return sealed.get(item.strategy_code)
        if live_horizons is None:
            # Legacy captures carry no sealed authority: read the catalog from this db,
            # falling back to the module reader only when this db has no catalog rows.
            live_horizons = _catalog_horizon_map(db) or get_primary_horizon_sessions()
        return live_horizons.get(item.strategy_code)

    # Canonical decision selection happens before any outcome is inspected: an
    # incomplete first decision must not let a later same-day refresh become canonical.
    item_query = db.query(RankingSnapshotItem).filter(
        RankingSnapshotItem.primary_horizon_sessions.isnot(None)
    )
    if market != "ALL":
        item_query = item_query.filter(RankingSnapshotItem.stock_market == market)

    rejected: dict[str, int] = {}
    raw_rows = 0
    canonical: dict[tuple, dict] = {}

    def _reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for item in item_query.all():
        raw_rows += 1
        ok, reason = _item_evidence(item, kind=kind,
                                    expected_horizon=_expected_horizon(item))
        if not ok:
            _reject(reason)
            continue
        # The unit key carries the market: the ALL layer must keep market strata apart.
        key = (item.stock_market, item.instrument_id or item.stock_symbol, item.session_date)
        if kind == "strategy":
            key = (*key, item.strategy_code)
        order = (
            int(item.ranking_snapshot_id) if item.ranking_snapshot_id is not None else 0,
            item.decision_snapshot_id or "",
        )
        current = canonical.get(key)
        if not current:
            canonical[key] = {"item": item, "order": order}
            continue
        # Same unit from a later capture is a refresh: the first decision wins.
        _reject("DUPLICATE_UNIT")
        if order < current["order"]:
            canonical[key] = {"item": item, "order": order}

    wanted_signals: set[int] = set()
    horizons: set[int] = set()
    for entry in canonical.values():
        if entry["item"].signal_run_id is not None:
            wanted_signals.add(int(entry["item"].signal_run_id))
        horizons.add(FACTOR_HORIZON_SESSIONS if kind == "factor"
                     else int(entry["item"].primary_horizon_sessions))

    outcome_index: dict[tuple[int, int], StrategyOutcome] = {}
    if wanted_signals:
        for outcome in db.query(StrategyOutcome).filter(
            StrategyOutcome.signal_run_id.in_(wanted_signals),
            StrategyOutcome.horizon_days.in_(horizons),
        ).all():
            outcome_index[(int(outcome.signal_run_id), int(outcome.horizon_days))] = outcome

    candidates: list[dict] = []
    signal_ids: set[int] = set()
    for entry in canonical.values():
        item = entry["item"]
        join_horizon = (FACTOR_HORIZON_SESSIONS if kind == "factor"
                        else int(item.primary_horizon_sessions))
        outcome = (
            outcome_index.get((int(item.signal_run_id), join_horizon))
            if item.signal_run_id is not None else None
        )
        if outcome is None:
            if kind == "factor" and int(item.primary_horizon_sessions) != FACTOR_HORIZON_SESSIONS:
                # A factor decision sealed at another horizon has no fixed-horizon outcome:
                # it cannot enter the factor cohort, and it is not evidence loss.
                _reject("FACTOR_HORIZON_OUTCOME_MISSING")
                continue
            _reject("OUTCOME_MISSING")
            continue
        ok, reason = _outcome_provenance(item, outcome, kind=kind)
        if not ok:
            _reject(reason)
            continue
        meta = outcome.meta or {}
        if item.signal_run_id is not None:
            signal_ids.add(int(item.signal_run_id))
        candidates.append(
            {
                "decision_snapshot_id": item.decision_snapshot_id,
                "capture_order": item.ranking_snapshot_id,
                "ranker_version": item.ranker_version,
                "market": item.stock_market,
                "instrument_id": item.instrument_id or item.stock_symbol,
                "session_date": item.session_date,
                "strategy_code": item.strategy_code,
                "signal_run_id": item.signal_run_id,
                "primary_horizon_sessions": int(item.primary_horizon_sessions),
                "exit_session_date": meta.get("exit_session_date"),
                "input_hash": meta.get("input_hash"),
                "outcome_revision": meta.get("outcome_revision") or outcome.id,
                "gross_return_pct": (
                    float(meta["stock_gross_return_pct"])
                    if meta.get("stock_gross_return_pct") is not None else None),
                "net_return_pct": (
                    float(meta["stock_net_return_pct"])
                    if meta.get("stock_net_return_pct") is not None else None),
                "factors": {
                    code: getattr(item, code, None) for code in CALIBRATABLE_FACTORS
                },
            }
        )

    candidates.sort(key=lambda s: (s["session_date"], s["market"], s["instrument_id"],
                                   s["strategy_code"], s["capture_order"],
                                   s["decision_snapshot_id"]))
    samples = candidates

    dates = sorted({s["session_date"] for s in samples})
    horizon_value: object = FACTOR_HORIZON_SESSIONS
    if kind == "strategy":
        per_strategy = {s["strategy_code"]: s.get("primary_horizon_sessions")
                        for s in samples}
        horizon_value = per_strategy or None
    # An archived decision that lost its outcome reference is evidence loss, not a
    # smaller cohort: fail closed instead of quietly shrinking the sample.
    integrity_suspect = bool(rejected.get("OUTCOME_MISSING") and samples)
    return {
        "kind": kind,
        "market": market,
        "samples": samples,
        "counts": {
            "raw_rows": raw_rows,
            "signal_ids": len(signal_ids),
            "units": len(samples),
            "dates": len(dates),
            "rejected": rejected,
        },
        "decision_dates": dates,
        "primary_horizon_sessions": horizon_value,
        "ranker_versions": sorted({s.get("ranker_version") or "" for s in samples}),
        "integrity_suspect": integrity_suspect,
    }


def cohort_ic_periods(cohort: dict, *, kind: str) -> dict:
    """Per market/decision-date series: factor uses raw factor vs gross; strategy uses net.

    A missing factor is not a zero: it stays out of that factor's series, and the ALL
    layer keeps each market's periods apart before any aggregation.
    """
    by_period: dict[tuple, list[dict]] = {}
    for sample in cohort["samples"]:
        by_period.setdefault((sample.get("market") or "ALL", sample["session_date"]),
                             []).append(sample)

    periods: dict[str, dict] = {}
    for (sub_market, day), rows in by_period.items():
        key = f"{sub_market}|{day}"
        if kind == "factor":
            usable = [r for r in rows if r["gross_return_pct"] is not None]
            returns = [r["gross_return_pct"] for r in usable]
            entry: dict = {"sample_count": len(usable)}
            for code in CALIBRATABLE_FACTORS:
                pairs = [(r["factors"].get(code), r["gross_return_pct"])
                         for r in usable if r["factors"].get(code) is not None]
                entry[code] = (spearman([p[0] for p in pairs], [p[1] for p in pairs])
                               if len(pairs) >= 2 else None)
            periods[key] = entry
        else:
            nets = [r["net_return_pct"] for r in rows if r["net_return_pct"] is not None]
            periods[key] = {"net_return": statistics.fmean(nets) if nets else None,
                            "sample_count": len(nets)}
    return periods


def cohort_fingerprint(cohort: dict, *, kind: str, market: str,
                       target: str | None = None,
                       policy_version: str = CALIBRATION_POLICY_VERSION) -> str:
    """Canonical SHA-256 of the exact immutable inputs that enter the statistics.

    Excludes scheduler tick time, live/output weights, computed IC and sample_size.
    """
    cohort = cohort_for_target(cohort, kind=kind, target=target)
    value_key = "gross_return_pct" if kind == "factor" else "net_return_pct"
    samples = sorted(
        cohort["samples"],
        key=lambda s: (s["decision_snapshot_id"], s["instrument_id"], s["session_date"]),
    )
    payload = {
        "policy_version": policy_version,
        "kind": kind,
        "market": market,
        "population": POPULATION_SIGNAL_FORWARD_V2,
        "evaluation_version": EVALUATION_VERSION_V2,
        "primary_horizon_sessions": cohort["primary_horizon_sessions"],
        "samples": [
            {
                "decision_snapshot_id": s["decision_snapshot_id"],
                "market": s.get("market"),
                "instrument_id": s["instrument_id"],
                "session_date": s["session_date"],
                "strategy_code": s["strategy_code"],
                "ranker_version": s.get("ranker_version"),
                "primary_horizon_sessions": s.get("primary_horizon_sessions"),
                "input_hash": s["input_hash"],
                "outcome_revision": s["outcome_revision"],
                "factors": ({k: (None if v is None else float(v))
                             for k, v in s["factors"].items()}
                            if kind == "factor" else {}),
                "return": s[value_key],
            }
            for s in samples
        ],
    }
    return _sha256(_canonical(payload))


# ------------------------------------------------------------- readiness

def _bootstrap_interval(values: list[float], *, draws: int = BOOTSTRAP_DRAWS,
                        seed: int = BOOTSTRAP_SEED,
                        level: float = BOOTSTRAP_CONFIDENCE) -> tuple[float, float] | None:
    """Deterministic bootstrap of the mean; fixed seed so replays agree."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws)
    )
    lo = max(0, int((1.0 - level) / 2.0 * draws))
    hi = min(draws - 1, int((1.0 - (1.0 - level) / 2.0) * draws))
    return float(means[lo]), float(means[hi])


def _readiness_one(cohort: dict, *, market: str, kind: str, target: str | None = None) -> dict:
    cohort = cohort_for_target(cohort, kind=kind, target=target)
    floors = CALIBRATION_FLOORS.get(market, CALIBRATION_FLOORS["ALL"])
    dates = list(cohort["decision_dates"])
    holdout = dates[-HOLDOUT_DECISION_DATES:] if len(dates) > HOLDOUT_DECISION_DATES else []
    holdout_set = set(holdout)
    training_dates = [d for d in dates if d not in holdout_set]
    boundary = training_dates[-1] if training_dates else ""

    training = [s for s in cohort["samples"] if s["session_date"] not in holdout_set]
    # A training sample whose exit crosses the holdout boundary is not a clean training sample.
    purged = [s for s in training if (s["exit_session_date"] or "") > boundary]
    training = [s for s in training if (s["exit_session_date"] or "") <= boundary]

    units_key = (
        (lambda s: (s.get("market") or "ALL", s["instrument_id"], s["session_date"],
                    s["strategy_code"]))
        if kind == "strategy"
        else (lambda s: (s.get("market") or "ALL", s["instrument_id"], s["session_date"]))
    )
    units = len({units_key(s) for s in training})
    training_date_set = sorted({s["session_date"] for s in training})

    periods = cohort_ic_periods({"samples": training}, kind=kind) if training else {}
    qualifying = {
        day for day, values in periods.items()
        if int(values.get("sample_count") or 0) >= floors["min_period_samples"]
    }

    per_factor: dict[str, dict] = {}
    if kind == "factor":
        for code in CALIBRATABLE_FACTORS:
            series = [periods[day][code] for day in sorted(qualifying)
                      if periods[day].get(code) is not None]
            # A missing factor is not a zero: it stays out of this factor's IC series.
            pairs = [(float(s["factors"][code]), s["gross_return_pct"])
                     for s in training
                     if s["factors"].get(code) is not None
                     and s["gross_return_pct"] is not None]
            ic = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 3 else None
            ir = None
            if len(series) >= 3:
                spread = statistics.stdev(series)
                ir = (statistics.fmean(series) / spread) if spread > 0 else None
            per_factor[code] = {
                "ic": ic,
                "ir": ir,
                "series": len(series),
                "interval": _bootstrap_interval(series),
                "sample_units": len(training),
            }
    else:
        series = [periods[day]["net_return"] for day in sorted(qualifying)
                  if periods[day].get("net_return") is not None]
        wins = sum(1 for s in training if (s["net_return_pct"] or 0.0) > 0)
        per_factor["net_return"] = {
            "mean": statistics.fmean(series) if series else None,
            "win_rate": (wins / len(training) * 100.0) if training else None,
            "series": len(series),
            "interval": _bootstrap_interval(series),
            "sample_units": len(training),
        }

    if kind == "factor" and target in per_factor:
        target_report = per_factor[target]
    else:
        target_report = max(per_factor.values(), key=lambda r: r["series"],
                            default={"series": 0, "interval": None})
    valid_periods = int(target_report["series"])
    interval = target_report["interval"]

    reasons: list[str] = []
    if cohort.get("integrity_suspect"):
        # An archived decision lost its outcome reference: evidence loss, not a sample.
        reasons.append("OUTCOME_REFERENCE_LOSS")
    if units < floors["units"]:
        reasons.append(f"UNITS_BELOW_FLOOR({units}/{floors['units']})")
    if len(training_date_set) < floors["dates"]:
        reasons.append(f"TRAINING_DATES_BELOW_FLOOR({len(training_date_set)}/{floors['dates']})")
    if valid_periods < floors["ic_periods"]:
        reasons.append(f"IC_PERIODS_BELOW_FLOOR({valid_periods}/{floors['ic_periods']})")
    if len(holdout) < HOLDOUT_DECISION_DATES:
        reasons.append("HOLDOUT_DATES_INSUFFICIENT")
    if interval is None:
        reasons.append("CONFIDENCE_INTERVAL_UNAVAILABLE")
    elif interval[0] <= 0.0 <= interval[1]:
        reasons.append("CONFIDENCE_INTERVAL_CROSSES_ZERO")

    return {
        "ready": not reasons,
        "reasons": reasons,
        "floors": floors,
        "training": {"units": units, "dates": len(training_date_set),
                     "ic_periods": valid_periods, "purged": len(purged)},
        "holdout": {"dates": len(holdout)},
        "confidence": {"interval": list(interval) if interval else None},
        "per_factor": per_factor,
    }


def evaluate_readiness(cohort: dict, *, market: str, kind: str,
                       target: str | None = None) -> dict:
    """Floors plus confidence for one market, or market strata for ALL."""
    if market != "ALL":
        report = _readiness_one(cohort, market=market, kind=kind, target=target)
        report["strata"] = {}
        return report

    by_market: dict[str, list[dict]] = {}
    for sample in cohort["samples"]:
        by_market.setdefault(sample.get("market") or "ALL", []).append(sample)
    strata: dict[str, dict] = {}
    for sub_market, rows in sorted(by_market.items()):
        sub_cohort = {
            **cohort,
            "samples": rows,
            "decision_dates": sorted({r["session_date"] for r in rows}),
        }
        strata[sub_market] = _readiness_one(sub_cohort, market=sub_market, kind=kind,
                                           target=target)
    passing = [m for m, r in sorted(strata.items()) if r["ready"]]
    overall = _readiness_one(cohort, market="ALL", kind=kind, target=target)
    reasons = list(overall["reasons"])
    if len(passing) < ALL_MIN_QUALIFYING_MARKETS:
        reasons.append(
            f"MARKET_STRATA_BELOW_FLOOR({len(passing)}/{ALL_MIN_QUALIFYING_MARKETS})"
        )
    return {
        "ready": not reasons,
        "reasons": reasons,
        "floors": overall["floors"],
        "training": overall["training"],
        "holdout": overall["holdout"],
        "confidence": overall["confidence"],
        "per_factor": overall["per_factor"],
        "strata": strata,
        "qualifying_markets": passing,
    }


# ------------------------------------------------------------- application

def plan_calibration(db, *, kind: str, market: str, target: str,
                     regime: str = "default",
                     baseline_name: str = DEFAULT_BASELINE_NAME) -> dict:
    """Read-only sealed snapshot: mode, pin, cohort, fingerprint, readiness, config."""
    ensure_rollout_baseline_and_modes(db, baseline_name=baseline_name,
                                      market=market, kind=kind)
    cohort = select_cohort(db, kind=kind, market=market)
    target_cohort = cohort_for_target(cohort, kind=kind, target=target)
    fingerprint = cohort_fingerprint(cohort, kind=kind, market=market, target=target)
    readiness = evaluate_readiness(target_cohort, market=market, kind=kind, target=target)
    mode = read_calibration_mode(db, kind=kind, market=market)
    pinned, auto = pin_state(db, kind=kind, market=market, target=target, regime=regime)
    old_weight = current_weight(db, kind=kind, market=market, target=target, regime=regime)
    expected_hash = live_config_hash(db, kind=kind, market=market, regime=regime)
    return {
        "kind": kind,
        "market": market,
        "target": target,
        "regime": regime,
        "mode": mode,
        "pinned": pinned,
        "auto_calibrate": auto,
        "old_weight": old_weight,
        "cohort": target_cohort,
        "cohort_fingerprint": fingerprint,
        "readiness": readiness,
        "expected_live_config_hash": expected_hash,
        "calibration_policy_version": CALIBRATION_POLICY_VERSION,
    }


def plan_calibration_batch(db, *, kind: str, market: str, targets: list[str],
                           regime: str = "default",
                           baseline_name: str = DEFAULT_BASELINE_NAME) -> dict:
    """One sealed read snapshot shared by every proposal in the batch."""
    ensure_rollout_baseline_and_modes(db, baseline_name=baseline_name,
                                      market=market, kind=kind)
    cohort = select_cohort(db, kind=kind, market=market)
    fingerprint = cohort_fingerprint(cohort, kind=kind, market=market)
    expected_hash = live_config_hash(db, kind=kind, market=market, regime=regime)
    mode = read_calibration_mode(db, kind=kind, market=market)
    plans: dict[str, dict] = {}
    for target in targets:
        target_cohort = cohort_for_target(cohort, kind=kind, target=target)
        plans[target] = {
            "kind": kind,
            "market": market,
            "target": target,
            "regime": regime,
            "mode": mode,
            "cohort": target_cohort,
            "cohort_fingerprint": cohort_fingerprint(cohort, kind=kind, market=market,
                                                     target=target),
            "readiness": evaluate_readiness(target_cohort, market=market, kind=kind,
                                            target=target),
            "expected_live_config_hash": expected_hash,
            "calibration_policy_version": CALIBRATION_POLICY_VERSION,
            "old_weight": current_weight(db, kind=kind, market=market,
                                         target=target, regime=regime),
        }
    return {"kind": kind, "market": market, "targets": list(targets), "plans": plans}


def _weight_write(db, *, plan: dict, new_weight: float, reason: str) -> str:
    """Write one weight row and its history entry inside the caller's transaction."""
    kind, market, target = plan["kind"], plan["market"], plan["target"]
    regime = plan["regime"]
    if kind == "factor":
        row = (
            db.query(FactorWeight)
            .filter(FactorWeight.factor_code == target, FactorWeight.market == market)
            .first()
        )
        if not row:
            return "TARGET_MISSING"
        old = float(row.weight)
        row.weight = float(new_weight)
        row.reason = reason or "auto"
        row.effective_from = utc_now()
        row.updated_at = utc_now()
        db.add(
            FactorWeightHistory(
                factor_code=target, market=market, old_weight=old, new_weight=float(new_weight),
                reason="auto", sample_size=plan["cohort"]["counts"]["units"],
                ic=(plan["readiness"]["per_factor"].get(target) or {}).get("ic"),
                ir=(plan["readiness"]["per_factor"].get(target) or {}).get("ir"),
                meta={"cohort_fingerprint": plan["cohort_fingerprint"],
                      "readiness": plan["readiness"]["training"]},
            )
        )
        return "APPLIED"
    code, _, reg = target.partition("|")
    reg = reg or (regime or "default")
    row = (
        db.query(StrategyWeight)
        .filter(
            StrategyWeight.strategy_code == code,
            StrategyWeight.market == market,
            StrategyWeight.regime == reg,
        )
        .first()
    )
    if not row:
        return "TARGET_MISSING"
    old = float(row.weight)
    row.weight = float(new_weight)
    row.reason = reason or "auto"
    row.effective_from = utc_now()
    row.updated_at = utc_now()
    db.add(
        StrategyWeightHistory(
            strategy_code=code, market=market, regime=reg,
            old_weight=old, new_weight=float(new_weight),
            reason=reason or "auto",
            sample_size=plan["cohort"]["counts"]["units"],
            meta={"cohort_fingerprint": plan["cohort_fingerprint"],
                  "readiness": plan["readiness"]["training"]},
        )
    )
    return "APPLIED"


def apply_calibration_batch(db, *, plans: dict, proposals: dict,
                            reasons: dict | None = None,
                            output_config_hash: str = "") -> dict:
    """All-or-nothing write transaction for one market/kind batch.

    Every claim is inserted before any weight write, and every gate is rechecked before
    the first weight write, so a mid-batch STALE_CONFIG, a pinned target or a crash
    rolls back the whole batch: no earlier target can stay applied on a partial batch.
    """
    targets = [t for t in plans if t in proposals and proposals[t] is not None]
    if not targets:
        return {"status": "NO_PROPOSAL", "results": {}, "applied": []}
    reasons = reasons or {}

    def refuse(status: str, **extra) -> dict:
        db.rollback()
        return {"status": status, "results": {t: status for t in targets},
                "applied": [], **extra}

    claims = {}
    for target in targets:
        plan = plans[target]
        claim = CalibrationApplication(
            kind=plan["kind"],
            market=plan["market"],
            target=target,
            regime=plan["regime"],
            calibration_policy_version=plan["calibration_policy_version"],
            cohort_fingerprint=plan["cohort_fingerprint"],
            population=POPULATION_SIGNAL_FORWARD_V2,
            evaluation_version=EVALUATION_VERSION_V2,
            expected_live_config_hash=plan["expected_live_config_hash"],
            output_config_hash=output_config_hash,
            sample_units=plan["cohort"]["counts"]["units"],
            decision_dates=plan["cohort"]["counts"]["dates"],
            valid_ic_periods=plan["readiness"]["training"]["ic_periods"],
            raw_rows=plan["cohort"]["counts"]["raw_rows"],
            signal_ids=plan["cohort"]["counts"]["signal_ids"],
            old_weight=plan["old_weight"],
            new_weight=float(proposals[target]),
            mode=plan["mode"],
            reason=reasons.get(target, ""),
        )
        db.add(claim)
        claims[target] = claim
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        if not _is_application_key_conflict(error):
            # A foreign-key or NOT NULL break is a real fault, not a replay of the cohort key.
            raise
        return refuse("DUPLICATE_COHORT",
                      cohort_fingerprints={t: plans[t]["cohort_fingerprint"]
                                           for t in targets})

    for target in targets:
        plan = plans[target]
        kind, market = plan["kind"], plan["market"]
        mode = read_calibration_mode(db, kind=kind, market=market)
        if mode == MODE_FROZEN:
            return refuse("MODE_FROZEN")
        if mode == MODE_SHADOW:
            return refuse("MODE_SHADOW")
        if not plan["readiness"]["ready"]:
            return refuse("READINESS_NOT_MET",
                          reasons=plan["readiness"]["reasons"])
        pinned, auto = pin_state(db, kind=kind, market=market, target=target,
                                 regime=plan["regime"])
        if pinned or not auto:
            return refuse("PINNED_OR_OFF")

    checked: set[tuple] = set()
    for target in targets:
        plan = plans[target]
        key = (plan["kind"], plan["market"], plan["regime"])
        if key in checked:
            continue
        checked.add(key)
        if live_config_hash(db, kind=plan["kind"], market=plan["market"],
                            regime=plan["regime"]) != plan["expected_live_config_hash"]:
            # One config snapshot for the whole batch: a sibling write inside the batch
            # is expected, an outside change invalidates every proposal at once.
            return refuse("STALE_CONFIG")

    results: dict[str, str] = {}
    for target in targets:
        plan = plans[target]
        claim = claims[target]
        horizon = plan["cohort"].get("primary_horizon_sessions")
        if isinstance(horizon, dict):
            horizon = horizon.get(str(target or "").partition("|")[0])
        claim.ranker_version = ";".join(plan["cohort"].get("ranker_versions") or ())
        claim.primary_horizon_sessions = horizon
        claim.target_weight = float(proposals[target])
        claim.source_config_versions = {
            "live_config_payload": live_config_payload(db, kind=plan["kind"],
                                                       market=plan["market"],
                                                       regime=plan["regime"]),
        }
        status = _weight_write(db, plan=plan, new_weight=float(proposals[target]),
                               reason=reasons.get(target, ""))
        if status == "TARGET_MISSING":
            db.rollback()
            return {"status": "TARGET_MISSING",
                    "results": {t: ("TARGET_MISSING" if t == target else "ROLLED_BACK")
                                for t in targets},
                    "applied": []}
        db.add(
            CalibrationProvenance(
                kind=plan["kind"], market=plan["market"], target=target,
                calibration_policy_version=plan["calibration_policy_version"],
                cohort_fingerprint=plan["cohort_fingerprint"],
                population=POPULATION_SIGNAL_FORWARD_V2,
                evaluation_version=EVALUATION_VERSION_V2,
                decision_snapshot_ids=[s["decision_snapshot_id"]
                                       for s in plan["cohort"]["samples"]],
                outcome_revisions=[{"outcome_revision": s["outcome_revision"],
                                     "input_hash": s["input_hash"]}
                                   for s in plan["cohort"]["samples"]],
                policy_payload={"floors": plan["readiness"]["floors"],
                                "holdout_dates": HOLDOUT_DECISION_DATES},
            )
        )
        results[target] = "APPLIED"

    db.commit()
    return {"status": "APPLIED", "results": results, "applied": list(results)}


def apply_calibration_plan(db, *, plan: dict, new_weight: float,
                           reason: str = "", output_config_hash: str = "",
                           batch: dict | None = None) -> dict:
    """Single-target convenience path; it runs through the same batch transaction."""
    result = apply_calibration_batch(
        db,
        plans={plan["target"]: plan},
        proposals={plan["target"]: new_weight},
        reasons={plan["target"]: reason},
        output_config_hash=output_config_hash,
    )
    target = plan["target"]
    status = result["status"]
    out = {"status": status, "market": plan["market"], "kind": plan["kind"],
           "target": target, "cohort_fingerprint": plan["cohort_fingerprint"]}
    if status == "READINESS_NOT_MET" and "reasons" in result:
        out["reasons"] = result["reasons"]
    if status == "APPLIED":
        out["old_weight"] = plan["old_weight"]
        out["new_weight"] = float(new_weight)
    return out


def run_calibration(db, *, kind: str, market: str, target: str, regime: str = "default",
                    compute_new_weight, baseline_name: str = DEFAULT_BASELINE_NAME,
                    reason: str = "", output_config_hash: str = "") -> dict:
    """Plan on a sealed snapshot, then apply the proposal through the same gate."""
    plan = plan_calibration(db, kind=kind, market=market, target=target, regime=regime,
                            baseline_name=baseline_name)
    proposal = compute_new_weight(plan)
    if proposal is None:
        return {"status": "NO_PROPOSAL", "market": market, "kind": kind, "target": target,
                "mode": plan["mode"], "readiness": plan["readiness"]}
    result = apply_calibration_plan(db, plan=plan, new_weight=float(proposal),
                                    reason=reason, output_config_hash=output_config_hash)
    result["readiness"] = plan["readiness"]
    result["mode"] = plan["mode"]
    return result
