# 驗證證據 2026-10-10（V1.3 解讀修正）

Method: read-only against this checkout and `data/panwatch.db` (216 MB). No DB write, no code change, no deployment.
Environment: branch `codex/taiwan-market-support`, HEAD `7d1a334c`. DB latest `snapshot_date` 2026-10-08; latest calibration write 2026-10-10T05:41Z.

## V1.3 evidence boundaries and read-only recheck

Original source/DB observations below were recorded at HEAD `7d1a334c`. Review at HEAD `ba57b1a` reproduced the following bounded DB counts on 2026-10-10. The DB is a local snapshot, not a deployed environment. No runtime change or DB write was made.

| Observation | Reproduced count | Interpretation |
| --- | --- | --- |
| TW active strategy rows / distinct symbols per date | 20 / 5 on 09-29, 09-30, 10-01, 10-02, 10-08 | cap counts strategy rows, not 20 instruments |
| strategy outcome total / NULL / equal-price zero | 3,341 / 0 / 805 | equal-price set is suspect; without actual bar identity, not all 805 are proved stale |
| TW 1d rows / instrument-date units / dates | 1,078 / 370 / 5 | row count is not an independent-sample count |
| TW 3d rows / instrument-date units / dates | 860 / 297 / 4 | duplicate strategy rows inflate the denominator |
| TW 5d rows / instrument-date units / dates | 860 / 297 / 4 | the repeated n=860 is not evidence of five matured 5d dates |
| TW 10d rows / instrument-date units / dates | 402 / 146 / 2 | long horizons have fewer observed dates |

Read-only recheck queries (run against a sealed snapshot / quiescent DB; `-readonly` must not be removed to work around access errors):

```sh
sqlite3 -readonly data/panwatch.db "SELECT snapshot_date, COUNT(*), COUNT(DISTINCT stock_symbol) FROM strategy_signal_runs WHERE stock_market='TW' AND status='active' GROUP BY snapshot_date"
sqlite3 -readonly data/panwatch.db "SELECT horizon_days, COUNT(*), COUNT(DISTINCT stock_symbol || '|' || snapshot_date), COUNT(DISTINCT snapshot_date) FROM strategy_outcomes WHERE stock_market='TW' GROUP BY horizon_days"
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*), SUM(outcome_return_pct IS NULL), SUM(outcome_return_pct=0 AND base_price=outcome_price) FROM strategy_outcomes"
```

Review used URI `mode=ro&immutable=1` after checking that no WAL sidecar was present. Immutable reading is appropriate only for a sealed/quiescent snapshot, not a live WAL DB; otherwise obtain a coherent read-only snapshot. Counts are point observations, not guarantees about later ticks.

Additional source corrections:

- `MAX_SINGLE_STRATEGY_SHARE` is a scalar .42, not a map missing a TW key.
- `endswith("B")` is a bond-ETF exemption heuristic. EQUITY versus ETF is already selected through `security_type`; subtype/tax authority must be verified separately.
- `scanned_instrument_count` is requested/attempted ids, including failed requests; catalog exclusions occur before price requests. Coverage needs stage accounting.
- Existing backtest engine already has next-session open / gap / stop-first evaluation. The strategy evaluator lacks OHLC evaluation; this is not a claim that the entire repository lacks barriers.
- Daily StrategySignalRun and StrategyFactorSnapshot are upserted. Research provenance helpers do not make those projections immutable; v2 needs decision capture and joins.
- Candidate/strategy overlap proves matching keys, not exact migrated-row identity. No historical cohort fingerprint means repeated rounded IC/IR and sample counts do not prove identical input membership.

## 1. Legacy TW 20-row cap is currently binding

```sh
sqlite3 -readonly data/panwatch.db "SELECT snapshot_date, COUNT(*) FROM strategy_signal_runs WHERE reason LIKE '%組合約束%' GROUP BY snapshot_date"
# 2026-09-29|112  2026-09-30|150  2026-10-01|143  2026-10-02|186  2026-10-08|155  total 746

sqlite3 -readonly data/panwatch.db "SELECT DISTINCT reason FROM strategy_signal_runs WHERE reason LIKE '%組合約束%'" | head -2
# 均線多頭排列，MACD金叉，漲幅+4.49%；組合約束: TW 未持倉機會超限(20)

sqlite3 -readonly data/panwatch.db "SELECT snapshot_date, status, COUNT(*) FROM strategy_signal_runs GROUP BY snapshot_date, status"
# every date: active|20
```

`payload.constrained` flag: `{'2026-09-30': 150, '2026-10-01': 143, '2026-10-02': 186, '2026-10-08': 155}`, 634 of 1,164. The 2026-09-29 rows carry the reason but not the flag (older code path).

`MAX_UNHELD_ACTIVE_BY_MARKET` (`strategy_engine.py:204`) has no `TW` key, so the default 20 applies. `active = 20` equals the cap on all five observed days.

## 2. Producer defect and suspect equal-price zero returns

```sh
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*) FROM strategy_outcomes WHERE outcome_return_pct IS NULL"   # 0
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*) FROM strategy_outcomes WHERE outcome_return_pct = 0"       # 805
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*) FROM strategy_outcomes WHERE base_price = outcome_price"   # 805
sqlite3 -readonly data/panwatch.db "SELECT horizon_days, COUNT(*) FROM strategy_outcomes WHERE outcome_return_pct = 0 GROUP BY horizon_days"
# 1|756  3|43  5|4  10|2
sqlite3 -readonly data/panwatch.db "SELECT outcome_status, COUNT(*) FROM strategy_outcomes WHERE outcome_return_pct = 0 GROUP BY outcome_status"
# evaluated|742  hit_target|63
```

Producer (`strategy_engine.py:1664`):

```python
target_day = snap_day + timedelta(days=horizon)              # calendar days
outcome_price = _pick_close_on_or_before(klines, target_day)  # on-or-before fallback
...
base_price = _pick_close_on_or_before(klines, snap_day)       # fallback to signal-day close
```

A calendar-day horizon landing on a non-trading day can reuse the signal-day close and produce a false zero. These counts identify a suspect set, not the bar dates used by every row; different valid sessions can also close at the same price. A NULL-only guard does not detect stale reuse, and an equality-price guard would incorrectly delete legitimate zeros. Validate exact entry/exit session and bar identities instead.

## 3. Migration 111 already merged candidate outcomes into strategy_outcomes

`migrations.py:957-985`, inside `_m111_strategy_layer` (registered `Migration(111, "strategy_layer", ...)` at `:1979`), inserts `FROM entry_candidate_outcomes eco` into `strategy_outcomes` via `strategy_signal_runs.source_candidate_id`.

```sh
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*) FROM entry_candidate_outcomes eco JOIN strategy_outcomes os ON os.stock_symbol=eco.stock_symbol AND os.snapshot_date=eco.snapshot_date AND os.horizon_days=eco.horizon_days"
# 2490 of 3341 strategy_outcomes rows overlap candidate outcomes by (symbol, snapshot_date, horizon)

sqlite3 -readonly data/panwatch.db "SELECT source_pool, COUNT(*) FROM strategy_outcomes GROUP BY source_pool"
# market_scan|3317  mixed|24
sqlite3 -readonly data/panwatch.db "SELECT COUNT(DISTINCT signal_run_id) FROM strategy_outcomes"
# 1126
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*) FROM strategy_outcomes GROUP BY signal_run_id HAVING COUNT(*)>=4"
# 433 signal runs have all four horizons
```

## 4. The outcome population is all-market, not TW-only

```sh
sqlite3 -readonly data/panwatch.db "SELECT stock_market, COUNT(*) FROM strategy_outcomes GROUP BY stock_market"
# TW|3200  US|68  HK|56  CN|17
```

| horizon | n (all) | win% | avg% | n (TW) | win% | avg% |
| --- | --- | --- | --- | --- | --- | --- |
| 1d | 1126 | 21.8 | +0.61 | 1078 | 21.7 | +0.63 |
| 3d | 891 | 69.6 | +2.73 | 860 | 70.9 | +2.83 |
| 5d | 891 | 71.6 | +4.05 | 860 | 72.6 | +4.20 |
| 10d | 433 | 72.1 | +6.52 | 402 | 73.6 | +7.04 |

## 5. Repeated calibration statistics and weight drift

`factor_weight_history` TW = 29 rows over 7 recorded rounds, with repeated `ic`/`ir` and `sample_size=860`; `crowd_penalty` drifted 1.0 -> 0.6196. Five signal snapshot dates exist, but the current 5d outcome rows span only four dates (09-29, 09-30, 10-01, 10-02). Without a historical cohort fingerprint, identical rounded statistics cannot prove exact membership per round. The quoted last-write timestamp is part of the original observation, not a live status claim.

```sh
sqlite3 -readonly data/panwatch.db "SELECT factor_code, market, old_weight, new_weight, ic, ir, sample_size, reason, created_at FROM factor_weight_history WHERE market='TW'"
sqlite3 -readonly data/panwatch.db "SELECT factor_code, market, weight, is_pinned, auto_calibrate, meta FROM factor_weights WHERE market='TW'"
# alpha_score 0.8389 ic -0.039 ir -0.2278 n 860 | catalyst_score 0.7319 | quality_score 0.8528
# risk_penalty 0.7677 ic 0.0495 ir 0.3141 | crowd_penalty 0.6196 ic 0.2049 ir 3.6202
```

`factor_eval.py:99` already filters `isnot(None)`; `_aggregate_recent_outcomes` (`strategy_engine.py:1751`) does not. `factor_calibration.py:104` already gates `row.is_pinned or not row.auto_calibrate`; the production write path is `factor_calibration.py:141 calibrate_all_markets`.

## 6. Strategy weight drift and sample floor

```sh
sqlite3 -readonly data/panwatch.db "SELECT strategy_code, market, weight, reason FROM strategy_weights"
sqlite3 -readonly data/panwatch.db "SELECT code, default_weight, enabled FROM strategy_catalog"
```

| strategy | catalog default | current weight | drift | outcome samples |
| --- | --- | --- | --- | --- |
| macd_golden | 1.10 | 1.3011 | +18.3% | 666 |
| trend_follow | 1.15 | 1.3349 | +16.1% | 561 |
| volume_breakout | 1.18 | 1.342 | +13.7% | 96 |
| market_scan | 1.08 | 1.2443 (TW) / 1.2119 (ALL) | +15.2% / +12.2% | 891 / 987 |
| rebound | 0.95 | 1.136 | +19.6% | 38 |
| pullback | 1.05 | 1.0528 | +0.3% | 61 |

`rebound` was promoted from a default of 0.95 to 1.136 on 38 outcome rows (about 10 distinct signal runs). `min_samples` default 8 permits updates on very small correlated cohorts; V1.3 specifies distinct instrument/date units and date floors rather than treating all outcome rows as independent. `strategy_weights` has 12 rows (6 strategies x {ALL, TW}); CN/HK/US have no rows and fall back to `ALL`.

## 7. An ETF is already in the active buy opportunity population

```sh
sqlite3 -readonly data/panwatch.db "SELECT stock_symbol, action, status, rank_score, snapshot_date FROM strategy_signal_runs WHERE stock_symbol GLOB '*63[12]*'"
# TWSE:00631L  buy | active | 100.0 on 2026-10-01 (4 rows) and 2026-10-02 (4 rows)
# TWSE:00631L  watch | inactive | 65.0 on other dates
# TWSE:00632R  avoid | inactive | 0.0 on 2026-10-08 (2 rows)
sqlite3 -readonly data/panwatch.db "SELECT COUNT(*) FROM strategy_signal_runs WHERE stock_symbol='TWSE:00631L' AND status='active' AND action='buy'"
# 8
```

## 8. Existing reusable infrastructure

| Item | Location | Already provides |
| --- | --- | --- |
| Trading-day calendar | `src/platform/scheduling/trading_calendar.py` | `is_trading_day`, `next_trading_day`, `previous_trading_day`, `calendar_status`, `market_calendar_context`; cached `data/tw_trading_calendar.json`; refresh job at `context_scheduler.py:242` |
| Point-in-time outcome | `src/modules/research/taiwan_historical_research.py` (765 lines) | `_resolve_factor` by `decision_at`, `_factor_provenance` (:157), `_bar_provenance_error` (:176), `_forward_outcome` (:394) with `horizon_sessions`, entry = first retained bar after decision date, `cost_policy` entry/exit bps, gross vs net, venue benchmark excess return (`_BENCHMARK_FOR_VENUE` TWSE->TAIEX, TPEX->TPEX), `status: "unavailable"` + reason codes |
| Discovery pool metadata | `packages/marketdata/src/marketdata/types.py:129 TaiwanDiscoveryPool` | `status`, `catalog_count`, `eligible_catalog_count`, `price_universe_selected_count`, `price_snapshot_batches_planned`, `unattempted_price_snapshot_batches`, `ranked_price_count`, `failed_request_count`, `cache_hits`, `price_data_dates`, `excluded_security_type_counts`, `security_type_by_instrument_id`, `provider_scope`; pool already filters `is_active is True AND security_type in {EQUITY, ETF}` |
| Per-strategy horizon | `src/modules/strategy/strategy_catalog.py:30..78 params={"horizon_days": ...}` | trend_follow 5, macd_golden 3, volume_breakout 3, pullback 5, rebound 3, market_scan 3; consumed at `strategy_engine.py:1307` for `holding_days` only |

## 9. Naming collisions

- `coverage_status` already exists in the research layer with a different vocabulary (`AVAILABLE`, ...): `twmd_broker_flow.py:221`, `taiwan_discovery.py:756`, `twmd_benchmarks.py:244`, `twmd_profile_revenue.py:149`.
- `factor_provenance` already exists as `_factor_provenance` at `taiwan_historical_research.py:157`.

## 10. Spec artifacts not yet implemented at original verification

Zero hits in `src`, `packages`, `frontend/src` for: `discovery_runs`, `discovery_exclusions`, `ranking_snapshots`, `ranking_snapshot_items`, `strategy_outcomes_v2`, `ranker_version`, `scoring_config_version`, `evaluation_version`, `cross_sectional_reference`, `NOT_FILLED`, `INSUFFICIENT_DATA`, `ambiguous_barrier`.

## 11. Original source citations (line numbers at original verification)

`strategy_engine.py`: `refresh_strategy_signals` 1209, `list_strategy_signals` 1468, `evaluate_strategy_outcomes` 1587, `_aggregate_recent_outcomes` 1751, `rebalance_strategy_weights` 1786, `_apply_portfolio_constraints` 727, `_rank_map` 637, `MAX_UNHELD_ACTIVE_BY_MARKET` 204, `MAX_HIGH_RISK_RATIO_BY_MARKET` 210, `MAX_SINGLE_STRATEGY_SHARE` 218.
`entry_candidates.py`: `refresh_entry_candidates` 1259, `_load_market_scan_inputs` 981 (default `limit_per_market=60`), `_derive_market_scan_decision` 627, `evaluate_entry_candidate_outcomes` 1675, active gate 1488 (62/55), `MARKET_SCAN_SEED_SYMBOLS` 68 (no TW).
`dashboard.py`: `_group_signals` 61, `min_score=55`, `action_limit Query(6)`, `curate_today` 410. `signal_explain.py:30 to_ai_score`. `recommendations.py:289 market_scan_limit Query(80)`.
`cost_model.py`: `cost_model_for_market` 130, bond-ETF exemption heuristic `endswith("B")` 145. `paper_trading_engine.py:54 _position_weight` (85/75/65).
`models.py`: `StrategyWeight` 738 (no pin field), `FactorWeight` 786 with `is_pinned`/`auto_calibrate` 803-804.
Frontend: `Dashboard.tsx` `opportunities.slice(0,3)` 646, fallback `listStrategySignals status:'active' limit:5` 178; `Opportunities.tsx` `minScore '70'` 86, `>=80` colour 731.
`migrations.py:1174-1181` alpha = rank x 0.35, quality = rank x 0.15.
