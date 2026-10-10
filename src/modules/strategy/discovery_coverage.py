"""Immutable persistence helpers for bounded discovery runs.

Run headers, per-stage ID partitions, candidate linkage, and the final manifest
hash are written through the caller's database transaction. The pool is captured
before that transaction begins, so no provider work is held under a DB lock.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from marketdata.types import TaiwanDiscoveryPool
from src.platform.persistence.models import (
    DiscoveryExclusion,
    DiscoveryRun,
    EntryCandidate,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _datetime_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _ids(pool: TaiwanDiscoveryPool, stage: str, outcome: str) -> list[str]:
    values = (pool.stage_id_sets.get(stage) or {}).get(outcome) or []
    return sorted({str(value) for value in values if str(value)})


def _assert_partition(parent: set[str], children: list[set[str]], label: str) -> None:
    union: set[str] = set()
    for child in children:
        if union & child:
            raise ValueError(f"discovery {label} contains overlapping stage outcomes")
        union.update(child)
    if union != parent:
        raise ValueError(f"discovery {label} does not reconcile to its parent stage")


def _assert_run_reconciles(run: DiscoveryRun) -> None:
    stages = run.stage_id_sets or {}
    reasons = run.stage_reason_id_sets or {}

    catalog = stages.get("catalog") or {}
    unique = set(catalog.get("unique") or [])
    eligible = set(catalog.get("eligible") or [])
    excluded = set(catalog.get("excluded") or [])
    _assert_partition(unique, [eligible, excluded], "catalog")

    universe = stages.get("universe_bound") or {}
    _assert_partition(
        eligible,
        [set(universe.get("requested") or []), set(universe.get("not_requested_due_to_limit") or [])],
        "universe bound",
    )
    dispatch = stages.get("dispatch") or {}
    _assert_partition(
        set(universe.get("requested") or []),
        [set(dispatch.get("attempted") or []), set(dispatch.get("unattempted") or [])],
        "dispatch",
    )
    validation = stages.get("price_validation") or {}
    _assert_partition(
        set(dispatch.get("attempted") or []),
        [
            set(validation.get("valid") or []),
            set(validation.get("invalid") or []),
            set(validation.get("missing_or_failed") or []),
            set(validation.get("pending") or []),
        ],
        "price validation",
    )
    ranking = stages.get("ranking") or {}
    _assert_partition(
        set(validation.get("valid") or []),
        [set(ranking.get("eligible") or []), set(ranking.get("excluded") or [])],
        "ranking",
    )
    selection = stages.get("pool_selection") or {}
    _assert_partition(
        set(ranking.get("eligible") or []),
        [set(selection.get("selected") or []), set(selection.get("ranked_not_selected") or [])],
        "pool selection",
    )
    candidate = stages.get("candidate_selection") or {}
    _assert_partition(
        set(selection.get("selected") or []),
        [set(candidate.get("selected") or []), set(candidate.get("excluded") or []),
         set(candidate.get("not_selected") or [])],
        "candidate selection",
    )

    counts = {
        "catalog_unique_count": unique,
        "eligible_catalog_count": eligible,
        "requested_universe_count": set(universe.get("requested") or []),
        "not_requested_due_to_limit_count": set(universe.get("not_requested_due_to_limit") or []),
        "attempted_instrument_count": set(dispatch.get("attempted") or []),
        "unattempted_instrument_count": set(dispatch.get("unattempted") or []),
        "valid_price_count": set(validation.get("valid") or []),
        "invalid_price_count": set(validation.get("invalid") or []),
        "missing_or_failed_price_count": set(validation.get("missing_or_failed") or []),
        "pending_price_count": set(validation.get("pending") or []),
        "ranking_eligible_count": set(ranking.get("eligible") or []),
        "ranking_excluded_count": set(ranking.get("excluded") or []),
        "pool_selected_count": set(selection.get("selected") or []),
        "ranked_not_selected_count": set(selection.get("ranked_not_selected") or []),
        "candidate_selected_count": set(candidate.get("selected") or []),
        "candidate_excluded_count": set(candidate.get("excluded") or []),
        "candidate_not_selected_count": set(candidate.get("not_selected") or []),
    }
    for field, values in counts.items():
        if getattr(run, field) != len(values):
            raise ValueError(f"discovery {field} does not match its canonical ID set")
    if run.catalog_response_row_count != (
        len(unique) + run.duplicate_catalog_record_count + run.invalid_catalog_record_count
    ):
        raise ValueError("discovery raw catalog rows do not reconcile")

    for stage, expected in (
        ("catalog", excluded),
        ("candidate_selection", set(candidate.get("excluded") or []) | set(candidate.get("not_selected") or [])),
    ):
        reason_ids = reasons.get(stage) or {}
        _assert_partition(expected, [set(values or []) for values in reason_ids.values()], f"{stage} reasons")
    price_reason_ids = reasons.get("price_validation") or {}
    # catalog_diagnostics are intentionally overlapping secondary evidence;
    # price-validation reasons are primary and must cover the attempted set.
    _assert_partition(
        set(dispatch.get("attempted") or []),
        [set(values or []) for values in price_reason_ids.values()],
        "price validation reasons",
    )
    for stage, reason_map in reasons.items():
        for reason, values in reason_map.items():
            recorded = (run.stage_reason_counts.get(stage) or {}).get(reason)
            if recorded is not None and recorded != len(set(values or [])):
                raise ValueError(f"discovery {stage}/{reason} count does not match IDs")


def create_discovery_run(
    db: Session,
    pool: TaiwanDiscoveryPool,
    *,
    mode: str,
    scan_run_key: str,
) -> DiscoveryRun:
    """Create an uncommitted run header and reserve its numeric scan_run_id."""
    run = DiscoveryRun(
        scan_run_key=scan_run_key,
        revision=1,
        stock_market="TW",
        requested_venues=list(pool.requested_venues or []),
        source_mode=mode,
        provider_scope=pool.provider_scope,
        provider_raw_status=pool.status,
        scan_coverage_state=pool.scan_coverage_state,
        coverage_scope=pool.coverage_scope,
        run_started_at_utc=_datetime_utc(pool.run_started_at_utc),
        run_completed_at_utc=_datetime_utc(pool.run_completed_at_utc),
        catalog_source=pool.catalog_source,
        catalog_source_as_of=pool.catalog_source_as_of,
        catalog_captured_at_utc=_datetime_utc(pool.catalog_captured_at_utc),
        catalog_authority_status=pool.catalog_authority_status,
        catalog_hash=pool.catalog_hash,
        catalog_response_row_count=pool.catalog_response_row_count,
        catalog_unique_count=pool.catalog_unique_count,
        duplicate_catalog_record_count=pool.duplicate_catalog_record_count,
        invalid_catalog_record_count=pool.invalid_catalog_record_count,
        eligible_catalog_count=pool.eligible_catalog_count,
        requested_universe_count=len(_ids(pool, "universe_bound", "requested")),
        not_requested_due_to_limit_count=len(_ids(pool, "universe_bound", "not_requested_due_to_limit")),
        attempted_instrument_count=len(_ids(pool, "dispatch", "attempted")),
        unattempted_instrument_count=len(_ids(pool, "dispatch", "unattempted")),
        valid_price_count=len(_ids(pool, "price_validation", "valid")),
        invalid_price_count=len(_ids(pool, "price_validation", "invalid")),
        missing_or_failed_price_count=len(_ids(pool, "price_validation", "missing_or_failed")),
        pending_price_count=len(_ids(pool, "price_validation", "pending")),
        ranking_eligible_count=len(_ids(pool, "ranking", "eligible")),
        ranking_excluded_count=len(_ids(pool, "ranking", "excluded")),
        pool_selected_count=len(_ids(pool, "pool_selection", "selected")),
        ranked_not_selected_count=len(_ids(pool, "pool_selection", "ranked_not_selected")),
        catalog_request_count=pool.catalog_request_count,
        price_snapshot_request_count=pool.price_snapshot_request_count,
        price_snapshot_batches_planned=pool.price_snapshot_batches_planned,
        unattempted_price_snapshot_batches=pool.unattempted_price_snapshot_batches,
        pending_price_snapshot_batches=pool.price_snapshot_batches_pending_count,
        failed_request_count=pool.failed_request_count,
        price_snapshot_count=pool.price_data_snapshot_count,
        price_data_dates=list(pool.price_data_dates),
        stage_id_sets={stage: {outcome: _ids(pool, stage, outcome) for outcome in outcomes}
                       for stage, values in pool.stage_id_sets.items()
                       for outcomes in [values.keys()]},
        stage_reason_id_sets=pool.stage_reason_id_sets or {},
        stage_reason_counts={
            stage: {reason: len(set(values or [])) for reason, values in reason_map.items()}
            for stage, reason_map in (pool.stage_reason_id_sets or {}).items()
        },
        scan_limits=pool.scan_limits or {},
        authority_by_instrument_id=pool.authority_by_instrument_id or {},
        selection_config_version=pool.selection_config_version,
        selection_config=(
            pool.selection_config or {
                "version": pool.selection_config_version,
                "mode": mode,
                "requested_market": pool.requested_market or "TW",
                "requested_venues": list(pool.requested_venues or []),
                **(pool.scan_limits or {}),
            }
        ),
        selection_config_hash=(
            pool.selection_config_hash
            or _hash(pool.selection_config or {
                "version": pool.selection_config_version,
                "mode": mode,
                "requested_market": pool.requested_market or "TW",
                "requested_venues": list(pool.requested_venues or []),
                **(pool.scan_limits or {}),
            })
        ),
        retention_expires_at_utc=(
            _datetime_utc(pool.run_completed_at_utc) or datetime.now(timezone.utc).replace(tzinfo=None)
        ) + timedelta(days=180),
    )
    db.add(run)
    db.flush()
    return run


def run_reference(run: DiscoveryRun) -> dict[str, Any]:
    return {
        "scan_run_id": run.id,
        "revision": run.revision,
        "manifest_hash": run.manifest_hash,
        "source_mode": run.source_mode,
    }


def finalize_discovery_run(
    db: Session,
    run: DiscoveryRun,
    pool: TaiwanDiscoveryPool,
    *,
    candidate_rows: list[EntryCandidate],
    candidate_by_instrument_id: dict[str, EntryCandidate],
) -> None:
    """Seal candidate partition, ledger, and manifest in the active transaction."""
    if run.manifest_hash:
        raise ValueError("discovery manifest is immutable after finalization")
    candidate_pool_ids = set(_ids(pool, "pool_selection", "selected"))
    selected: set[str] = set()
    excluded: set[str] = set()
    not_selected: set[str] = set()
    reasons: dict[str, set[str]] = {}
    linked_candidates: list[int] = []

    for instrument_id in sorted(candidate_pool_ids):
        row = candidate_by_instrument_id.get(instrument_id)
        if row is None:
            not_selected.add(instrument_id)
            reasons.setdefault("candidate_not_materialized", set()).add(instrument_id)
            continue
        if row.id is not None:
            linked_candidates.append(int(row.id))
        captured_authority = (pool.authority_by_instrument_id or {}).get(instrument_id)
        authority = (
            captured_authority if isinstance(captured_authority, dict)
            else row.instrument_authority if isinstance(row.instrument_authority, dict)
            else {}
        )
        group = str(authority.get("security_type") or row.instrument_group or "UNKNOWN").upper()
        authority_status = str(authority.get("authority_status") or "UNKNOWN").upper()
        if row.status == "active" and group in {"EQUITY", "ETF"} and authority_status == "LATEST_ONLY":
            selected.add(instrument_id)
        else:
            excluded.add(instrument_id)
            if authority_status != "LATEST_ONLY":
                reason = "instrument_authority_unverified"
            elif group not in {"EQUITY", "ETF"}:
                reason = "instrument_group_unknown"
            else:
                reason = "candidate_gate_or_plan"
            reasons.setdefault(reason, set()).add(instrument_id)

    run.stage_id_sets = dict(run.stage_id_sets or {})
    run.stage_id_sets["candidate_selection"] = {
        "selected": sorted(selected),
        "excluded": sorted(excluded),
        "not_selected": sorted(not_selected),
    }
    run.stage_reason_id_sets = dict(run.stage_reason_id_sets or {})
    run.stage_reason_id_sets["candidate_selection"] = {
        reason: sorted(values) for reason, values in sorted(reasons.items())
    }
    run.stage_reason_counts = dict(run.stage_reason_counts or {})
    run.stage_reason_counts["candidate_selection"] = {
        reason: len(values) for reason, values in sorted(reasons.items())
    }
    run.candidate_selected_count = len(selected)
    run.candidate_excluded_count = len(excluded)
    run.candidate_not_selected_count = len(not_selected)
    run.candidate_ids = sorted(set(linked_candidates))

    candidate_items: list[dict[str, Any]] = []
    for instrument_id in sorted(candidate_pool_ids):
        row = candidate_by_instrument_id.get(instrument_id)
        if row is None:
            continue
        captured_authority = (pool.authority_by_instrument_id or {}).get(instrument_id)
        authority = (
            captured_authority if isinstance(captured_authority, dict)
            else row.instrument_authority if isinstance(row.instrument_authority, dict)
            else {}
        )
        group = str(authority.get("security_type") or row.instrument_group or "UNKNOWN").upper()
        candidate_items.append({
            "candidate_id": int(row.id) if row.id is not None else None,
            "instrument_id": instrument_id,
            "stock_symbol": row.stock_symbol,
            "stock_name": row.stock_name or row.stock_symbol,
            "instrument_group": group,
            "instrument_authority": authority,
            "status": row.status or "inactive",
            "score": float(row.score or 0.0),
            "action": row.action or "watch",
            "action_label": row.action_label or "觀望",
            "reason": row.reason or "",
            "entry_low": row.entry_low,
            "entry_high": row.entry_high,
            "stop_loss": row.stop_loss,
            "target_price": row.target_price,
            "candidate_stage": (
                "selected" if instrument_id in selected else "excluded"
            ),
        })

    _assert_run_reconciles(run)
    db.flush()

    def _manifest_time(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    manifest = {
        "scan_run_id": run.id,
        "scan_run_key": run.scan_run_key,
        "revision": run.revision,
        "stock_market": run.stock_market,
        "requested_market": run.stock_market,
        "requested_venues": run.requested_venues or [],
        "requested_universe": (run.stage_id_sets or {}).get("universe_bound", {}).get("requested", []),
        "scan_limits": run.scan_limits or {},
        "selection_config": run.selection_config or {},
        "selection_config_hash": run.selection_config_hash or "",
        "source_mode": run.source_mode,
        "provider_scope": run.provider_scope,
        "provider_raw_status": run.provider_raw_status,
        "error_reason": pool.error_reason,
        "scan_coverage_state": run.scan_coverage_state,
        "coverage_scope": run.coverage_scope,
        "run_started_at_utc": _manifest_time(run.run_started_at_utc),
        "run_completed_at_utc": _manifest_time(run.run_completed_at_utc),
        "catalog_source": run.catalog_source,
        "catalog_source_as_of": run.catalog_source_as_of,
        "catalog_captured_at_utc": _manifest_time(run.catalog_captured_at_utc),
        "catalog_authority_status": run.catalog_authority_status,
        "catalog_hash": run.catalog_hash,
        "catalog_response_row_count": run.catalog_response_row_count,
        "catalog_unique_count": run.catalog_unique_count,
        "catalog_excluded_count": len((run.stage_id_sets.get("catalog") or {}).get("excluded") or []),
        "duplicate_catalog_record_count": run.duplicate_catalog_record_count,
        "invalid_catalog_record_count": run.invalid_catalog_record_count,
        "eligible_catalog_count": run.eligible_catalog_count,
        "requested_universe_count": run.requested_universe_count,
        "not_requested_due_to_limit_count": run.not_requested_due_to_limit_count,
        "attempted_instrument_count": run.attempted_instrument_count,
        "unattempted_instrument_count": run.unattempted_instrument_count,
        "valid_price_count": run.valid_price_count,
        "invalid_price_count": run.invalid_price_count,
        "missing_or_failed_price_count": run.missing_or_failed_price_count,
        "pending_price_count": run.pending_price_count,
        "ranking_eligible_count": run.ranking_eligible_count,
        "ranking_excluded_count": run.ranking_excluded_count,
        "pool_selected_count": run.pool_selected_count,
        "ranked_not_selected_count": run.ranked_not_selected_count,
        "candidate_selected_count": run.candidate_selected_count,
        "candidate_excluded_count": run.candidate_excluded_count,
        "candidate_not_selected_count": run.candidate_not_selected_count,
        "candidate_ids": sorted(run.candidate_ids or []),
        # Candidate data is sealed into this manifest because EntryCandidate is a
        # mutable daily projection and can be replaced by a same-day refresh.
        "candidate_items": candidate_items,
        "catalog_request_count": run.catalog_request_count,
        "price_snapshot_request_count": run.price_snapshot_request_count,
        "price_snapshot_batches_planned": run.price_snapshot_batches_planned,
        "unattempted_price_snapshot_batches": run.unattempted_price_snapshot_batches,
        "pending_price_snapshot_batches": run.pending_price_snapshot_batches,
        "failed_request_count": run.failed_request_count,
        "price_snapshot_count": run.price_snapshot_count,
        "price_data_dates": run.price_data_dates or [],
        "stage_id_sets": run.stage_id_sets or {},
        "stage_reason_id_sets": run.stage_reason_id_sets or {},
        "stage_reason_counts": run.stage_reason_counts or {},
        "authority_by_instrument_id": run.authority_by_instrument_id or {},
        "selection_config_version": run.selection_config_version,
    }
    run.manifest = manifest
    run.manifest_hash = _hash(manifest)

    # Refs must carry the final hash. The hash does not include this back-link,
    # which avoids a self-referential manifest while keeping linkage atomic.
    final_ref = run_reference(run)
    for row in candidate_rows:
        refs = [ref for ref in (row.scan_run_refs or []) if ref.get("scan_run_id") != run.id]
        if run.id in (row.scan_run_ids or []):
            refs.append(final_ref)
            refs.sort(key=lambda ref: int(ref.get("scan_run_id") or 0))
            row.scan_run_refs = refs
            row.scan_run_id = run.id if len(row.scan_run_ids or []) == 1 else None
            meta = dict(row.meta or {})
            meta["scan_run_refs"] = refs
            row.meta = meta

    db.flush()
    db.query(DiscoveryExclusion).filter(DiscoveryExclusion.discovery_run_id == run.id).delete(
        synchronize_session=False
    )
    for stage, outcomes in (run.stage_id_sets or {}).items():
        for outcome, instrument_ids in outcomes.items():
            db.add(DiscoveryExclusion(
                discovery_run_id=run.id,
                stage=stage,
                outcome=outcome,
                reason_code="",
                instrument_ids=sorted(set(instrument_ids or [])),
                item_count=len(set(instrument_ids or [])),
            ))
    for stage, reason_map in (run.stage_reason_id_sets or {}).items():
        for reason_code, instrument_ids in reason_map.items():
            values = sorted(set(instrument_ids or []))
            if not values:
                continue
            outcomes = (run.stage_id_sets or {}).get(stage) or {}
            # Reason rows describe the actual stage outcome, including valid
            # prices and ordinary top-N omissions. Secondary diagnostics can
            # overlap a primary partition and must not be called exclusions.
            reason_outcome = next((
                outcome for outcome, ids in outcomes.items()
                if outcome not in {"unique", "eligible", "attempted", "requested"}
                and set(values).issubset(set(ids or []))
            ), "diagnostic")
            db.add(DiscoveryExclusion(
                discovery_run_id=run.id,
                stage=stage,
                outcome=reason_outcome,
                reason_code=reason_code,
                instrument_ids=values,
                item_count=len(values),
            ))
    db.flush()


def discovery_run_payload(run: DiscoveryRun) -> dict[str, Any]:
    """Return a stable API projection of the persisted manifest."""
    return {
        "scan_run_id": run.id,
        "revision": run.revision,
        "manifest_hash": run.manifest_hash,
        "manifest": run.manifest or {},
        "archived": bool(run.archived),
        "archived_reason": run.archived_reason or "",
        "retention_expires_at_utc": run.retention_expires_at_utc,
    }


def prune_discovery_runs(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired unreferenced runs and archive any run with durable refs."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_naive = current.astimezone(timezone.utc).replace(tzinfo=None)
    expired = db.query(DiscoveryRun).filter(
        DiscoveryRun.retention_expires_at_utc.is_not(None),
        DiscoveryRun.retention_expires_at_utc < current_naive,
        DiscoveryRun.archived.is_(False),
    ).yield_per(100)

    def is_referenced(run_id: int) -> bool:
        # Query the bounded run id in place instead of materializing all historical
        # candidate/snapshot rows during each refresh.
        for table, has_scalar in (
            ("entry_candidates", True), ("market_scan_snapshots", False),
            ("strategy_signal_runs", True), ("ranking_snapshots", True),
            ("ranking_snapshot_items", True),
        ):
            if has_scalar:
                scalar = db.execute(text(
                    f"SELECT 1 FROM {table} WHERE scan_run_id = :run_id LIMIT 1"
                ), {"run_id": run_id}).first()
                if scalar:
                    return True
            array = db.execute(text(
                f"SELECT 1 FROM {table}, json_each({table}.scan_run_ids) "
                "WHERE CAST(json_each.value AS INTEGER) = :run_id LIMIT 1"
            ), {"run_id": run_id}).first()
            if array:
                return True
        return False

    archived = deleted = 0
    for run in expired:
        if is_referenced(int(run.id)):
            run.archived = True
            run.archived_at_utc = current_naive
            run.archived_reason = "referenced_by_candidate_or_decision"
            archived += 1
        else:
            db.query(DiscoveryExclusion).filter(
                DiscoveryExclusion.discovery_run_id == run.id
            ).delete(synchronize_session=False)
            db.delete(run)
            deleted += 1
    db.flush()
    return {"archived": archived, "deleted": deleted}
