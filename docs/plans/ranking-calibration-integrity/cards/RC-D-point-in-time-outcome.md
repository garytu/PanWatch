# RC-D: Immutable, session-based forward and execution outcomes

Priority: P1. Status: pending. Ships in PR-1 after PR-0A identity/containment and PR-0B instrument/cost authority. Contract: [outcome vocabulary](../contracts/outcome-vocabulary-v1.md).

## Problem and delivered result

Calendar-day horizons and on-or-before fallbacks can reuse the wrong bar. A midpoint is not proof of entry; final close does not reveal barrier order. New field names cannot turn mutable daily signals into point-in-time evidence.

Delivered result: immutable decision references; separate fixed-horizon and execution populations; full session validation; persisted pending/insufficient/unfilled/ambiguous lifecycle; gross/net/benchmark availability and policy provenance.

## Current evidence and reuse

- Existing strategy/candidate evaluators use calendar days and midpoint/fallback prices. 805 equal-price zero returns are suspect, not proved all invalid; no zero-price-equality filter is allowed.
- Strategy evaluator has no OHLC barrier path. Backtest engine already has next-session open, gaps and legacy stop-first policy; research `_forward_outcome` uses first retained post-decision close. These are different policy adapters, not interchangeable implementations.
- Legacy uq_strategy_outcome_signal_horizon prevents version coexistence; legacy factor/signal snapshots are upserted. v2 needs separate table/decision identity.
- Calendar/provenance primitives exist. KlineData currently lacks all research receipt/content provenance fields, so capture/adapter work is necessary before claiming PIT-valid outcomes.

## Change scope

- `src/modules/strategy/strategy_engine.py`, `entry_candidates.py`: v2 producer selection/adapters, session maturity and retry integration.
- Shared pure evaluation kernel extracted from `src/modules/strategy/backtest/{engine,data_adapter,cost_model,metrics}.py` and research helpers where appropriate; keep explicit legacy policy adapters.
- `src/platform/persistence/{models,migrations}.py`: v2 outcomes/attempts with immutable decision foreign key, policies/availability, new versioned unique key.
- `src/platform/marketdata/collectors/kline_collector.py` and marketdata adapters: preserve provider/adjustment/receipt/source/capture evidence needed by kernel.
- `src/modules/research/context_scheduler.py`: evaluate then commit, then seal calibration cohort; retry queue and maturity scheduling.
- Research recommendations/AI/public statistics: separate population/basis/version/availability counts; no recalibration against current mutable projections.

## Implementation steps

1. Create strategy_outcomes_v2 + outcome_evaluation_attempts. Unique decision/population/horizon/evaluation-version; no old-row relabelling or overwrite. Capture policy payload/hash, exact bar identity and decision provenance.
2. Implement full expected-session validation using existing calendar, with source/confidence/version. Missing session/unfinished daily bar/calendar fallback is distinct from a not-yet-due horizon. No on-or-before replacement or observed-row gap skip.
3. Implement signal-forward-v2: next complete session close entry, h sessions after entry close exit; strategy primary-horizon and factor 5-session outcomes share this policy. Candidate-forward remains a separate producer.
4. Implement strategy-execution-v2 exactly per contract: 3-session buy/add limit window at entry_high, fill only from observed OHLC, open invalidation, overnight exit start, open-gap first, ambiguity when both intraday barriers touched, horizon expiry. Separate entry/session precision from actual intraday timestamp.
5. Persist PENDING / INSUFFICIENT_DATA / COMPLETE / AMBIGUOUS with independent entry and benchmark status. Retry same row 1h/6h/24h, bounded 30-calendar-day observation window; retain terminal insufficiency. Complete unfilled expiry requires all entry-window sessions observed.
6. Record forward gross-minus-explicit-bps net separately from actual-quantity execution CostModel PnL; capture cost authority and entry/exit dated rates, no slip duplication. Unknown costs leave gross valid/net unavailable.
7. Match benchmark entry/exit sessions exactly; missing benchmark keeps stock valid and relative NULL/UNAVAILABLE. Retry benchmark without rewriting valid stock facts; unsupported market mapping is explicit unavailable.
8. Update published statistics and legacy adapters with population, sessions, versions, units/dates and availability denominators. Update legacy horizon_return comparison tests only with explicit policy; do not silently change old backtest stop-first behavior.

## Acceptance conditions

- Different complete required sessions with equal close produce valid gross=0 and possibly negative net; stale entry-bar reuse produces insufficient data and NULL return.
- Holiday horizon waits for correct session; missing weekday bar cannot silently shorten/extend horizon; intraday bar does not satisfy final-close evaluation.
- Mutation/deletion of daily projection after decision leaves v2 input/outcome unchanged; missing PIT provenance cannot be retrospectively backfilled into eligibility.
- Complete entry window without fill is NOT_FILLED, missing window is INSUFFICIENT_DATA; supplied later data retries to valid completion.
- Gap-open exit is resolved before intraday high/low; same-bar both barriers without resolvable open is FILLED + AMBIGUOUS + BOTH_SAME_BAR, not fabricated trade return.
- Forward, execution and legacy backtest policy differences are explicit and independently tested; no shared unlabelled win rate.
- Old signal/horizon row does not block v2; a new evaluation revision coexists without modifying archived results.
- Unknown tax/benchmark cannot become zero cost/excess. Gross/net/relative and their available-sample denominators are independent.
- Calibration remains FROZEN/SHADOW through this PR; merely producing v2 rows cannot activate weights.

## Planned verification and risks

Extend `tests/test_strategy_outcome_efficiency.py`, `tests/test_backtest.py`, `tests/test_pw13_evidence_inventory.py`, `tests/test_factor_eval.py`; add offline fixed-clock fixtures for full-session gaps/holidays, timestamp precision, pending→insufficient→complete retry, entry expiry, open gap, both barriers, missing cost/benchmark, corporate-action basis, v2/legacy coexistence and outcome-scheduler ordering. Run these tests plus README regression and `git diff --check`.

Retained observations alone cannot prove full sessions; adapter/calendar evidence may keep some markets unavailable. Historical 3,341 rows remain legacy with original values. Storage/retry load is bounded and per-symbol bars/benchmark should be reused within a run. This is simulated research evaluation, not verified real execution or total-return performance.
