# RC-B: Discovery stages and instrument/cost authority

Priority: P1. Status: pending. Ships entirely in PR-0B, after PR-0A identity and before RC-D cost evaluation. Contract: [discovery/authority](../contracts/discovery-vocabulary-v1.md).

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
