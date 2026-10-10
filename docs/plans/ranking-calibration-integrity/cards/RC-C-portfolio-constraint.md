# RC-C: Unsaturated ranking and stable TW instrument constraints

Priority: P1. Status: pending. Ships in PR-2 after PR-0A, PR-0B and PR-1. Contract: [ranking/calibration sections 5–6](../contracts/ranking-vocabulary-v1.md).

## Problem and delivered result

Clipping weighted raw scores at 100 destroys order and sends all high scores into maximum sizing. The cap counts strategy rows rather than instruments; Dashboard-only tie changes cannot stabilize which instruments survive earlier constraints.

Delivered result: uncapped ranking_value with explicit display mapping, consistent consumers, pre-constraint instrument representatives and measured TW quotas. Initial cap preserves 5 instruments rather than expanding the legacy 20 rows into 20 instruments.

## Current evidence and reuse

- Five TW dates: active=20 rows, 5 distinct instruments; 746 rows carry cap demotion text, 634 payload flags. These are legacy row constraints, not measured instrument exclusions.
- MAX_UNHELD_ACTIVE_BY_MARKET / MAX_HIGH_RISK_RATIO_BY_MARKET omit TW; MAX_SINGLE_STRATEGY_SHARE is currently a scalar .42, not a market map.
- Existing scoring hard-clips at 100; `_position_weight` bands are 85/75/65. Candidate eligibility 62/55 is a separate score axis.
- Dashboard `_group_signals` selects by active/action/rank with arrival-order ties; active/action priorities must be explicit, not replaced by score-only sorting.
- Dashboard min_score=55, action_limit=6, slice(0,3), Opportunities minScore=70 and >=80 colour are consumers; fallback limits before grouping can yield duplicate instruments.

## Change scope

- `src/modules/strategy/strategy_engine.py`: rank/display fields, average-rank ties, frozen reference, representative/risk grouping, explicit TW unit/quotas and constraint ledger.
- `src/modules/portfolio/api/dashboard.py`, recommendation/AI enrichment and serializers: common instrument selection and score-version mapping.
- `src/modules/paper_trading/paper_trading_engine.py`: consume v2 score bands with one selected instrument/budget input.
- `src/platform/persistence/{models,migrations}.py`: versioned fields/projections, constrained flag and reason-code list.
- `frontend/src/pages/{Dashboard,Opportunities}.tsx`, associated API types: score tier/version, cap count/unit and missingness.

## Implementation steps

1. Persist unsaturated ranking_value. Apply the contract's stable sigmoid display mapping (center 100, scale 20), use unrounded ranking_value for ordering, and retain legacy score version on old projections. Do not use win-probability labels.
2. Implement tie-aware `_rank_map` over the captured pre-constraint reference. Missing features retain missingness; N=1 yields 50, tied equal features share percentiles.
3. Before cap selection, group TW EQUITY rows by canonical instrument. Choose active/action/ranking/stable-key representative and maximum eligible group risk. Keep duplicate strategy rows for audit without duplicate budget/order input.
4. Set TW INSTRUMENT cap=5, risk=.32, strategy share=.42; add per-market override for the currently scalar share. Other markets retain existing behavior/unit. Apply final-count integer quotas, deterministic pruning and explicit SMALL_SET_QUOTA_EXCEPTION from the contract.
5. Surface first-class constrained + code list and ledger with instrument/row before/after counts, quota/unit/version. Only excluded eligible instruments prove cap binding.
6. Update every consumer together: Dashboard and fallback dedupe before limit, min_score uses v2 display, UI rank tiers/version, AI scale, `_position_weight` v2 band input. Keep list size defaults and band budget ratios; the mapping's actual effects must be reported, not presumed.
7. Replay frozen inputs while calibration remains SHADOW. Report score spread/ties/filter retention/representatives/cap exclusions/quotas/sizing/exposure; exploratory legacy replay is labelled non-PIT. Initial cap increase and weight resets are outside this PR.

## Acceptance conditions

- Weighted raw 110/130/150 do not all display 100 and span at least two sizing bands; exact formula values and boundaries verified.
- Permuting identical candidate/reference inputs or changing projection updated_at produces identical percentiles, representatives, selected instruments and constraint reasons.
- Twenty strategy rows for five instruments use five slots; duplicate strategies create no extra budget allocation. High-risk secondary strategy cannot be hidden by a low-risk representative.
- Counterfactual fixture with sixth eligible EQUITY instrument reports UNHELD_CAP; active==cap without an excluded instrument reports no binding event.
- Ratios/quotas use final instrument counts; tiny-set rounding exceptions are visible. Held-management is separate.
- UI/default filters/fallback/AI all expose the correct scale/version; no mixed legacy/v2 threshold comparison. No blanket reset or unverified increase in exposure.

## Planned verification and risks

Extend `tests/test_factor_scoring_weights.py`, `tests/test_strategy_signal_candidate_identity.py`, `tests/test_paper_trading_position.py`, `tests/test_paper_trading_allocation.py`; add ranking permutation/tied-reference, Dashboard/fallback dedupe and constraint-unit fixtures. Run backend regression, frontend relevant vitest/tsc/build, then `git diff --check`.

Sigmoid parameters and quotas are initial engineering defaults. Average-rank ties and ordinary-EQUITY/ETF separation change selection and visibility. Shadow report is a rollout gate, not evidence of live execution or improved investment returns; DB currently has no paper trade outcome evidence.
