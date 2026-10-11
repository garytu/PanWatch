# RC-B coordinator review — 2026-10-11

Checkout: `codex/rc-b-discovery-coverage`, after user-owned RC-A commit `57c12c1` and assignment commit `2af16c0`. Implementation worker: `rc_b_worker` (GPT-6 Luna/max); coordinator independently reviewed and repaired the result. These are local fixture/build results, not deployed-service or production-DB verification.

## Delivered behavior

- Taiwan strategy discovery preserves the existing catalog, request universe, four-request concurrency, deadline, batch size and top-N ordering. List-only callers and `/discovery/stocks` retain their response shape. No new per-symbol quote lookup is introduced.
- Each run seals canonical stage ID sets, primary reason sets/counts, raw catalog accounting, catalog receipt/hash/classification, request/batch counts, selection-config payload/hash, coverage state/scope and run-specific candidate summaries. Empty failures also persist their exact reason. A malformed or conflicted source cannot become COMPLETE; a compatibility-only catalog cache remains UNKNOWN.
- Migration 128 adds run/ledger/cost authority and candidate/signal/immutable-decision linkage. Run, ledger, candidate and market-scan writes share one transaction. Same-day replacement and a database reopen retain prior sealed manifests. Expired unreferenced runs are removed after 180 days; referenced manifests, ledgers and authority are archived intact.
- API reads saved candidates from the requested manifest. UI displays its own counts/dates/scope separately from live popular-stock queries. AI resolves the candidate's exact ID/revision/hash, returns scoped counts for each contributing run, and returns UNKNOWN for missing or mismatched references.
- Ordinary Taiwan opportunities accept EQUITY only. Dashboard, Opportunities, candidate fallback and AI filter before their result limits; ETF opportunities are separately labelled. Existing held management remains available. The existing legacy row cap remains until RC-C implements instrument-level ranking and quotas.
- The strict dated cost adapter accepts explicit instrument/date/source/version/rates. Missing, mismatched, ambiguous or invalid authority produces unavailable net costs without inventing tax classifications. Existing legacy cost callers remain unchanged for RC-D to replace through explicit v2 adapters.

## Coordinator repairs

1. Freeze dispatch evidence and classify pre-dispatch timeout/cancellation as unattempted; late work cannot change the saved partition.
2. Reject malformed snapshot identities and catalog field shapes safely. Preserve the prior last-qualifying-row request universe when a later duplicate is inactive; capture conflicting authority as unusable.
3. Reconcile stored numeric counters and primary reasons against canonical sets; ledger reason rows use their actual outcome, including valid prices and diagnostics.
4. Store dates from all valid scanned prices, including rows outside top-N; distinguish UNKNOWN old caches from full catalog coverage.
5. Include supplied per-item references in immutable decision identity and reject conflicting header/item IDs before capture reuse.
6. Finish ordinary/ETF filtering and fallback authority propagation, exact-revision AI coverage, and plain UI coverage labels.

## Verification

Full regression after migration/ledger/cache repairs (before the last duplicate-compatibility fixture):

```sh
.venv/bin/python -m pytest -q tests packages/marketdata/tests
```

Result: **1,585 passed, 3 skipped, 14 warnings**. Warnings are existing Pydantic/ctypes deprecations.

Final RC-B/card integration regression after the duplicate-compatibility repair:

```sh
.venv/bin/python -m pytest -q tests/test_discovery_coverage.py tests/test_calibration_integrity.py tests/test_factor_calibration.py tests/test_factor_calibration_loop.py tests/test_factor_eval.py tests/test_factor_scoring_weights.py tests/test_factor_weights.py tests/test_factors_api.py tests/test_strategy_outcome_efficiency.py tests/test_strategy_signal_candidate_identity.py tests/test_backtest.py tests/test_paper_trading_position.py tests/test_paper_trading_allocation.py tests/test_discovery_routing.py tests/test_taiwan_discovery.py tests/test_pw13_evidence_inventory.py packages/marketdata/tests/test_twmd.py tests/test_assistant_tools.py
```

Result: **239 passed, 12 existing deprecation warnings**. Fixtures include stage partitions, failed/pending/pre-dispatch requests, stale/malformed/missing prices, populated migration upgrade, rollback, fixed-clock retention, exact decision references, same-code cross-venue identity, actual refresh/file-DB reopen/same-day empty failure, immutable old run API reads, dated costs and AI stale-reference rejection.

Frontend commands in `frontend/`, using repository-pinned Node 24.14.0 and pnpm 9.15.9:

```sh
pnpm exec vitest run
pnpm exec tsc -b
pnpm build
```

Result: **28 test files / 127 tests passed**; TypeScript and production build passed. New Opportunities tests exercise separate ordinary/ETF requests and candidate fallback labels; DiscoveryPanel tests exercise saved-run counts and separation from live results. Existing React warnings and stale Browserslist metadata do not fail these checks. Final `git diff --check` and staged whitespace check passed.

Synthetic grouping replay: 21 ordinary EQUITY rows → 20 active ordinary rows under the unchanged legacy cap; one ETF remains active in its own group; one unknown unheld row becomes inactive; one unknown held management row stays active. A 25-ETF saturated query still returns the two ordinary TW/CN rows with limit 2; ETF queries return ETF rows separately. This is fixed-input regression evidence, not a historical or economic shadow replay.

## Upstream evidence and limits

Read-only local source inspection: `/Users/garytu/works/tw-market-data-main`, HEAD `4bdccdb4e01ea70a55ce5aafdd80e6014248430d`, `src/twmd/query/api.py` instrument response and `/instruments` route. The response has instrument ID, venue, `security_type`, `is_active`, ISIN/list date/industry/CFI fields, but no ETF subtype, tax category or effective cost policy. This does not verify the deployed service. CFI/name/ticker are not converted into invented subtype or tax evidence.

Captured catalog authority is latest-only and historically unverifiable. Missing dated classification yields `cost_authority_missing`; no government tax rates or classifications are seeded. Valid gross results can be retained by RC-D while net remains unavailable. No runtime activation, weight change, deployment or upstream change occurred. RC-D is ready; RC-C and RC-A PR-3 retain their dependencies.
