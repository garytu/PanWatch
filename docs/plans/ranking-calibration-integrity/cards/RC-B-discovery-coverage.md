# RC-B: Discovery stages and instrument/cost authority

Priority: P1. Status: completed. Owner: coordinator (implementation: rc_b_worker, GPT-6 Luna/max). Ships entirely in PR-0B, after PR-0A identity and before RC-D cost evaluation. Contract: [discovery/authority](../contracts/discovery-vocabulary-v1.md).

## Assignment checkpoint

2026-10-10: User explicitly requested `execute RC-B`; the pending design card is promoted for execution. The prerequisite is PR-0A decision identity, not RC-A's future PR-3 restart work. Identity prerequisite verified in the current checkout: `tests/test_calibration_integrity.py -k 'capture or sealed_catalog'` — 11 passed, 66 deselected. RC-A review changes were committed separately by the user as `57c12c1` and remain outside this assignment. Calibration remains contained; this task does not activate it.

## Progress checkpoint

2026-10-10: Implemented structured Taiwan discovery stage partitions, frozen request-attempt evidence, catalog authority capture, and a versioned selection-config payload/hash. Persisted immutable run manifests and exclusion ledgers with atomic candidate linkage, same-day run-specific candidate/authority summaries, 180-day referenced-run retention, migration 128 support, dated cost-authority resolution, and run-linked API/UI/AI/strategy/dashboard consumers. Legacy `/stocks` fields and list-only discovery callers remain compatible; Taiwan EQUITY and ETF dashboard opportunities use separate bounded queries and unknown unheld classifications remain inactive. Calibration remains FROZEN.

Verification: RC-B backend suite — 209 passed, 12 warnings; `DiscoveryPanel.test.tsx` — 12 passed; frontend `tsc -b` and production build passed; `git diff --check` clean. Added fixtures cover migration from populated pre-128 tables, finalization rollback, fixed-clock retention, empty/failed runs, candidate and decision linkage, same-day refresh, stage reason partitions, same-code cross-venue identity, ETF saturation, and dated cost authority.

2026-10-11 coordinator verification: full backend regression — 1,585 passed / 3 skipped; final card/integration regression after review fixes — 239 passed; full frontend — 127 passed in 28 files; TypeScript and production build passed. Fixed-input grouping replay retains 20/21 ordinary rows, independently retains one ETF, excludes one unknown unheld row and preserves one unknown held row. Details and limits are in the linked verification evidence.

## Handoff

Coordinator review completed on 2026-10-11. Fixed pre-dispatch deadline/pending accounting, malformed IDs/catalog fields, UNKNOWN legacy-cache scope, all-valid-price date coverage, conflicting duplicate compatibility, counter/ledger validation, capture-reference identity, and ordinary/ETF filtering before limits in Dashboard/Opportunities/AI (including candidate fallback). Added exact-revision AI counts and actual refresh → file DB reopen → same-day empty failure coverage. Final evidence: [review and verification](../evidence/2026-10-11-rc-b-verification.md). Read-only inspection of local `tw-market-data` source at HEAD `4bdccdb4e01ea70a55ce5aafdd80e6014248430d` found `security_type`, venue, `is_active`, and CFI fields, with no ETF subtype or tax-category/effective-cost policy. Captured authority therefore remains latest-only and point-in-time unverifiable; missing dated classification returns `cost_authority_missing` and net cost stays unavailable. This is local source inspection, not a claim about deployed service behavior.

## Problem and delivered result

TW hot_stocks keeps only pool.items; downstream loses stage coverage and catalog authority. Requested IDs are called scanned even when requests fail. ETF/cost grouping is incomplete, and catalog security_type alone cannot identify a bond-ETF exemption.

Delivered result: persisted bounded run/ledger with reconcilable stage counts, run-linked API/UI/AI data, catalog authority in immutable decisions, and dated cost classification or explicit UNKNOWN. No wider scan or new per-symbol quote request.

## Current evidence and reuse

- Discard is the TW branch of `packages/marketdata/src/marketdata/client.py::hot_stocks`, through discovery collector → `_load_market_scan_inputs`; retain the current list API for other callers.
- TaiwanDiscoveryPool already has catalog/eligible/request/batch/failure/pending/ranked fields. Its scanned_instrument_count is attempted/requested units, and security_type_by_instrument_id covers only selected items. Detailed stage ledger requires capturing existing filtering decisions.
- TWSE:00631L has 8 active buy/100 rows. Existing security_type can separate ETF from EQUITY; leveraged/inverse subtype availability is unverified.
- `cost_model_for_market` distinguishes EQUITY/ETF using security_type. The B suffix branch is a bond-exemption heuristic, not general ETF identification.

## Change scope

- `packages/marketdata/src/marketdata/{client,types}.py`: structured pool result, stage reason sets and existing-catalog authority.
- `src/platform/marketdata/collectors/discovery_collector.py`, `src/modules/strategy/entry_candidates.py`: pass structured result, persist run/candidate linkage.
- `src/modules/strategy/strategy_engine.py`, `src/platform/persistence/{models,migrations}.py`: decision authority, run/ledger/retention schema.
- `src/modules/strategy/backtest/cost_model.py`: accept versioned authority and session date; unknown policy must be explicit.
- `src/modules/market/api/discovery.py`, `frontend/packages/api/src/discovery.ts`, `frontend/src/components/DiscoveryPanel.tsx`, dashboard/recommendation consumers: expose linked run and scope.

## Implementation steps

1. Add structured items+manifest+ledger+authority path without changing list-only hot_stocks callers. Use existing requests, limits, deadlines, ordering and cached catalog.
2. Persist stage id sets/reason counts per contract, including pre-scan exclusions, universe bound, attempted/unattempted, valid/invalid/missing/pending, ranked/top-N and candidate selection. Deduplicate reason counting within each stage.
3. Define COMPLETE/PARTIAL/MISSING/UNKNOWN from measured completeness plus FULL_ELIGIBLE_CATALOG/BOUNDED_REQUESTED_UNIVERSE. A failed batch cannot become COMPLETE because it was attempted.
4. Propagate scan_run_id/revision and catalog capture/source/hash/as-of plus security_type/venue/is_active through candidates and immutable decisions; projection/outcome adapters read the same authority.
5. Initial TW ordinary opportunity eligibility is EQUITY only. ETF candidates remain a separately labelled group; unknown subtype remains UNKNOWN and never inferred from ticker/name. This post-scan policy does not widen or reorder vendor discovery.
6. Verify actual upstream tax/subtype contract. If absent, define versioned local authority input with source/effective dates; if missing, net cost remains unavailable. Never replace the bond B heuristic with plain ETF as an exemption rule, or price historical tax using today's date.
7. Persist candidate linkage with the completed manifest; archive referenced manifests/authority when the 180-day unreferenced retention expires. Expose scoped counts and missingness to API/UI/AI after restart.

## Acceptance conditions

- Each stage equation and canonical id set reconciles; catalog exclusions never count as attempted prices.
- Failed/missing/stale/pending rows have explicit counts; COMPLETE within a bound is distinguished from full catalog coverage.
- scan_run_id joins the candidate/decision/API/UI manifest used at decision time, including empty/failed runs.
- TWSE:00631L is ETF, outside ordinary-EQUITY cap; it is not declared leveraged by an invented source field.
- Dated authoritative cost classification is usable by RC-D, or UNKNOWN yields cost_authority_missing/net unavailable with valid gross retained.
- Request count/universe/deadline/top-N ordering unchanged; no per-symbol quote call; old list callers remain compatible.

## Planned verification and risks

Extend `tests/test_taiwan_discovery.py`, `tests/test_discovery_routing.py`, `packages/marketdata/tests/test_twmd.py`, `frontend/src/components/DiscoveryPanel.test.tsx`; add run persistence/restart/linkage, stage reconciliation and authority/cost-date fixtures. Backend pytest for these modules, frontend vitest/tsc/build, then `git diff --check`.

Do not promise upstream subtype/tax fields exist. Missing classification reduces the eligible net population; it does not justify guessed tax. Eligibility changing EQUITY/ETF grouping is user-visible and needs replay counts. Catalog latest status cannot retrospectively prove historic universe membership.
