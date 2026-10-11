# RC-C independent review and verification

Date: 2026-10-11 (Asia/Taipei). Branch: `codex/rc-c-ranking-constraints`. Coordination commit: `bbde3aa`. Implementation worker: GPT-6 Luna/max (`rc_c_worker`); coordinator reviewed the complete implementation and added fixes and integration tests. This report covers local code and disposable fixtures, not a deployed service. Calibration remains **FROZEN**; no weight reset, production DB migration, live order, push or deployment was performed.

## Delivered behavior

- `ranker-v2` retains full precision `ranking_value`; fixed sigmoid display uses center 100 and scale 20. Ordering uses raw values; display rounding does not change filtering or sizing. Eligibility continues to use candidate score and the original plan/status/action gates. Missing/non-finite ranking inputs are excluded; missing features stay explicit and use the documented neutral percentile policy.
- Pre-constraint bounded candidate reference, feature values, factors, effective weights and scoring configuration are archived and hashed. Average tied ranks, singleton 50, stable identity ties and selection do not depend on projection timestamps. Same-day corrected inputs/reference/config produce new immutable revisions.
- TW ordinary EQUITY uses 5 distinct instruments, risk share .32 and strategy share .42. Selection uses active/action/raw ranking/strategy/instrument/decision identity. Maximum eligible member risk determines the instrument risk; duplicate strategies remain auditable but contribute one budget input. Final-count floor quotas, weakest-member pruning without replenishment and small-set exceptions are recorded.
- ETF retains the separate legacy **20 ROW** cap and Python-round quota behavior; CN/HK/US retain their existing row caps and ratios. Held management remains outside unheld quotas. No instrument cap increase is included.
- Migration 130 adds versioned projections plus append-only selection runs and per-row constraint ledger. Counts, units, quota versions and actual cap exclusions are recorded. Missing immutable selection evidence demotes all eligible duplicate members; a write failure rolls back capture, projection and ledger together.
- Dashboard, opportunities, fallback candidates, assistant enrichment and paper sizing carry explicit score versions. Ranking thresholds select one version cohort and report omitted legacy count; the unfiltered held-management query preserves both labelled versions. Candidate fallback remains a distinct raw-score scale. Paper entries use selected representatives before requesting quotes and budget allocation.

## Coordinator corrections

Independent review corrected source-pool-first UI ordering, rounded-threshold comparisons, candidate-score substitution for missing v2 ranks, immutable decision tie keys, display-derived confidence, hidden secondary high risk, mixed-version held-management omission, missing-ledger duplicate promotion and filtering open positions after an early paper limit. It restored the ETF row policy, made actual cap binding explicit, included all market quota values in the configuration hash and aligned ORM indexes with migration 130. The ranking contract's percentile wording now clearly says average rank position in the requested direction; it preserves the existing strongest-input-to-100 convention.

## Verification

Commands ran from repository root except the frontend commands. `NODE` below denotes the bundled executable `/Users/garytu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`; existing installed frontend dependencies were used without installing or updating packages.

| Command | Result |
| --- | --- |
| `.venv/bin/python tests/run_isolated_pytest.py -q tests/test_ranking_v2_integration.py tests/test_ranking_v2_selection.py` | 26 passed |
| `.venv/bin/python tests/run_isolated_pytest.py -q tests packages/marketdata/tests` | 1,646 passed / 3 skipped / 14 existing warnings |
| `NODE node_modules/vitest/vitest.mjs run` in `frontend/` | 28 files / 129 tests passed |
| `NODE node_modules/typescript/bin/tsc -b` in `frontend/` | passed |
| `NODE node_modules/vite/bin/vite.js build` in `frontend/` | passed |
| `.venv/bin/python scripts/replay_rc_c_frozen.py --output docs/plans/ranking-calibration-integrity/evidence/2026-10-11-rc-c-frozen-synthetic-replay.json` | generated deterministic report; equality checked by regression |
| `git diff --check` and final staged check | passed |

The new file-backed SQLite integration fixture exercises the actual refresh/capture/selection path with 6 instruments and 24 strategy rows: 20 retained active rows, 5 selected representatives, 4 rows belonging to the sixth excluded instrument, 24 immutable decision items and 24 ledger rows. It verifies rerun identity after changing projection timestamps, idempotent migration, append-only protection, archive retention across projection deletion and session reopening, and an injected failure after ledger insertion rolling back every new row. Consumer tests cover the 64.996 display-rounding boundary, secondary group risk filtering, mixed held versions, ETF row limits, invalid ranks, decision identity ties, missing evidence across duplicates, sizing boundaries immediately below/at 65/75/85 and actual disposable paper entries (5 orders from 20 active strategy rows; repeat scan adds none). Frontend tests cover raw-order display ties, missing v2 rank and candidate fallback scale.

Existing warnings remain: backend Pydantic/third-party deprecations, React Router future flags and repeated mock conversation keys in unrelated assistant tests, and stale Browserslist data during build. They do not fail these checks.

## Frozen synthetic replay and limits

[Machine-readable replay](2026-10-11-rc-c-frozen-synthetic-replay.json) uses 13 fixed synthetic ranking inputs, with one repeated value 110. It measures display/filter/sizing behavior and a deterministic constraint kernel; it does not claim historical PIT provenance or investment performance.

| Raw ranking | v2 display | Existing target budget band |
| --- | --- | --- |
| 110 | 62.24593312 | .08 |
| 130 | 81.75744762 | .18 |
| 150 | 92.41418200 | .25 |

Before constraints, thresholds 55/70/80/90 retain respectively 7/5/4/2 v2 inputs versus 12/11/10/9 under capped legacy display. The fixture selects 5 of 13 instruments and excludes 8 by cap; final high-risk count is 1 of 5, quota 1, with no small-set exception. The selected instruments' **sum of requested per-position target budget ratios** is 1.05 versus 1.25 for capped legacy display on that same selected set, delta -.20. This sum may exceed 100%; it is not realized exposure, cash allocation or a comparison against an archived historical portfolio. The paper integration fixture separately verifies distinct orders and non-negative available cash with fixed offline quote/cost inputs; it does not establish returns or live execution quality.

Sigmoid and quotas remain initial engineering defaults. A live/historical distribution or economic validation is still required before any parameter change or activation. Unknown authority, unavailable PIT inputs, costs and benchmarks are not fabricated. No existing weight is reset and no historical record is backfilled.

## Handoff

RC-C can be completed locally. RC-B, RC-D and RC-C implementation dependencies for RC-A PR-3 are now satisfied; **RC-A PR-3 is ready for a separately requested implementation**, while calibration activation remains gated on qualified cohorts, training/holdout floors, confidence and shadow/readiness evidence. This task does not start PR-3 or enable SHADOW/ACTIVE on a running service.
