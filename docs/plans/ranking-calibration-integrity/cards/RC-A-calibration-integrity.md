# RC-A: Calibration containment, immutable cohorts and gated restart

Priority: P1. Status: pending. PR-0A has no implementation dependency; PR-3 depends on PR-0B, PR-1 and PR-2. All gates follow [ranking/calibration 契約](../contracts/ranking-vocabulary-v1.md).

## Problem and delivered result

Repeated blending has no exact input identity, legacy outcomes mix producers, and mutable daily signals/factors cannot preserve decision-time evidence. Adding four version strings alone cannot close the loop safely.

Delivered result: all write paths share a FROZEN/SHADOW/ACTIVE gate; immutable captures and atomic application identities prevent replay; calibration uses explicit primary-horizon, deduplicated cohorts and readiness criteria. Shipping the gate does not mean any market has met restart conditions.

## Current evidence and reuse

- Factor pin/auto_calibrate already exists; StrategyWeight needs both fields. Preserve existing factor gate semantics.
- factor history repeats IC/IR and n=860; no historical fingerprint proves exact input identity. TW 5d rows=860 represent 297 instrument/date units over 4 dates.
- Migration 111 copied candidate outcomes into strategy_outcomes; legacy rows lack enough provenance to classify by table name or overlap alone.
- `_aggregate_recent_outcomes` counts rows across horizons and filters by created_at; `factor_eval` still uses calendar-day maturity. Both need the v2 selector.
- `strategy_catalog.params.horizon_days` exists; copy and reinterpret explicitly as sessions in versioned decisions. Catalog defaults differ, so no blanket reset.

## Change scope

- `src/modules/strategy/{factor_calibration,factor_eval,factor_weights,strategy_engine,strategy_catalog}.py`: shared cohort selector, pin/mode/readiness gate and atomic service application.
- `src/platform/persistence/{models,migrations}.py`: immutable config/ranking snapshots/items, calibration_applications, StrategyWeight fields, persisted mode and baseline archive.
- `src/modules/research/context_scheduler.py`: scheduled/manual ordering; outcome transaction completes before cohort seal.
- `src/modules/research/api/recommendations.py` and factor API callers: same service gate, explicit rejection/mode response; no force bypass.

## PR-0A implementation

1. Export current weights/config to a named rollout baseline with hash; preserve current values as unvalidated baseline. Initialize enabled markets plus ALL as FROZEN before any versioned weight write.
2. Add StrategyWeight is_pinned/auto_calibrate and persisted market/kind mode. Test scheduler/API/direct service rejection; no caller may bypass the service.
3. Capture append-only decision/reference inputs, original factor values, decision/available/receipt times, candidate/source/catalog identity and config versions. Keep UI projection separate; no automatic historical backfill.
4. Implement exact cohort fingerprint and unique application key from the contract. Do not hash live/output config or rounded IC; all factor proposals in one batch share a sealed read snapshot.
5. Make claim/weights/history/output config one short transaction, with expected-config and pin/mode rechecks. Roll back claim as well as weight on failure; stale config rejects the batch.
6. Encode contract floors/confidence policy now (TW/CN/HK/US 60 units and 20 training dates; ALL 120/30 with eligible market strata). Selector uses immutable primary horizons and instrument/date dedupe; no equality-price exclusion.
7. Until RC-D evaluator and all provenance rules exist, any newly labelled outcomes remain ineligible. A version tag on an old calendar-day evaluator must fail readiness.

## PR-3 implementation

1. Use only eligible completed signal-forward-v2 outcomes, actual session maturity and immutable factors. Strategy reads net; factor reads raw factors/gross at fixed 5 sessions. Execution/candidate/legacy never silently merged.
2. Count/report raw rows, signal ids, instrument/date units, dates, valid IC periods and dedupe reasons. Canonical decision selection happens before inspecting returns/availability.
3. Implement date-block confidence and time-ordered shadow replay; training has its own floor, last 5 dates form holdout, crossing training horizons purge. Do not tune on the holdout or treat correlated rows as independent trials.
4. Report readiness, before/after rankings, cost/coverage missingness, config versions and rejection counts. Activate only eligible market/kind; ALL cannot rescue below-floor markets. Keep other markets SHADOW.
5. Verify rollback to FROZEN and audited named-config restoration without deleting evidence.

## Acceptance conditions

- Same cohort applies once across retries, process restart and concurrent scheduler/API calls; new output config does not permit reapplication.
- Failure between claim/history/weight commits leaves no partial state. Concurrent config change yields STALE_CONFIG.
- Same-day refresh, correction or projection deletion cannot change archived factors or the decision joined by an outcome.
- A genuinely unchanged price across complete distinct sessions remains in IC/strategy statistics; reused/missing session bars do not.
- Duplicate strategies/horizons/captures do not increase independent units. Small new rebound cohort fails floors even with many raw rows.
- Insufficient training/holdout/confidence, old producer or unknown PIT inputs yields SHADOW/rejection and no weight write.
- No unlabelled historical read for auto-calibration; any research opt-in is read-only and never enables the writer.

## Planned verification and risks

Extend `tests/test_factor_calibration.py`, `tests/test_factor_calibration_loop.py`, `tests/test_factor_eval.py`, `tests/test_factor_weights.py`, `tests/test_factor_scoring_weights.py`, `tests/test_strategy_signal_candidate_identity.py`, `tests/test_strategy_outcome_efficiency.py`; add temporary-DB migration/concurrency/crash/mode integration tests. Run those tests and the README regression set, then `git diff --check`.

Existing drift stays until an explicit named baseline restoration; freeze is containment, not evidence of recovered weight quality. Captures add storage. Floors and confidence are conservative engineering defaults, not validated performance parameters. Historical data cannot make up missing decision-time inputs; PR-3 may remain SHADOW for weeks.
