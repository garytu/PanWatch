# Outcome 契約 v1

狀態：V1.3 設計契約，待實作與驗證。修訂：2026-10-10。與 [ranking/calibration 契約](ranking-vocabulary-v1.md) 共用不可變 decision identity；與 [discovery 契約](discovery-vocabulary-v1.md) 共用 catalog/cost authority。

## 1. 明確分開兩種測量

| population | entry | exit / 用途 |
| --- | --- | --- |
| `signal-forward-v2` | decision date 之後第一個完整交易 session 的 **close**，屬 synthetic reference entry | entry 後第 h 個交易 session 的 close；h=1 是下一 session；只作 signal/factor calibration |
| `strategy-execution-v2` | 以下明示的 buy/add limit-order 模擬 | first stop/target、expiry 或 ambiguity；獨立報告，不替代固定 horizon returns |
| `candidate-forward-v2` | 相同 forward policy，producer 為 candidate snapshot | 保持獨立統計母體，不 migration 到 signal 母體 |
| `legacy-unlabelled` | 舊 midpoint／on-or-before／不明來源 | 展示可保留並標示 legacy；永不自動標定 |

复用 research `_forward_outcome` 的 gross/net/benchmark 欄名、reason codes 與 first-after-decision 的概念；**session horizon 改為有完整 expected-session 驗證的 sequence**。research 的 retained-observation sequence 不能把缺漏 session 直接跳過。共用現有 `trading_calendar.py`，保存 market timezone、calendar version/source/confidence；官方 calendar 缺失／fallback 不可驗證時為 `INSUFFICIENT_DATA`，不能宣稱完整 session。未實作市場留 legacy／shadow，不能回退成 calendar days。

現 `backtest/engine.py` 已有 next-session open、gap 與 stop-first 模擬；它與 research 的 next-session close 是不同 policy。抽 shared pure evaluation kernel／adapter，將 policy 參數化。既有 backtest legacy policy 保留；v2 ambiguity policy 另版本，不能暗中改舊 backtest 語義。

## 2. 不可變 inputs 與 schema

`strategy_outcomes_v2` 新表，unique key = `(decision_snapshot_id, outcome_population_id, horizon_sessions, evaluation_version)`。不受舊 `uq_strategy_outcome_signal_horizon` 阻擋；不改寫或重新標定舊 outcome。每個 population 有自己的欄位與版本，不重用同一 row 承載两種 return。

`evaluation_version` 引用不可變 entry/barrier/calendar-validation/adjustment/cost policy。cost rate/effective-date 或 adjustment 規則改變即新版本；資料修訂需新 `evaluation_version` 的 revision identity 與 rollout record。避免以 tick time 當 version。保留舊結果供比較，不得因新 evaluator skip 已有 legacy row。

保存：decision id/time、instrument/venue/security authority、ranker/scoring versions、policy payload/hash、calendar version、source contract、bars/benchmark capture ids/hashes、adjustment mode、實際 entry/exit session、price basis、available/receipt timestamps、gross/net/benchmark fields、entry/barrier/evaluation states、reason code、attempt count、last/next retry time。每日 OHLC 只保留 session/date 與可知範圍，不編造 intraday execution timestamp。

`outcome_evaluation_attempts` append-only 保存每次 input capture/hash、result/reason 與 retry decision；新 attempt 可以完成此前不足資料的 v2 row。row 作該 version 的 current state，stock/execution 只有完成前允許合法 transition；其 `COMPLETE`／`AMBIGUOUS` facts 封存，資料修訂另建版本。benchmark availability 是獨立可重試的 annotation/projection，補齊不得修改封存 stock facts，也不得觸發同 stock cohort 再標定。

## 3. 狀態與 retry lifecycle

entry 與 outcome／benchmark 分開，不能以 exit ambiguity 改写 entry 成未成交。

| axis | values / transition |
| --- | --- |
| `entry_status` | `PENDING`, `FILLED`, `NOT_FILLED`, `INSUFFICIENT_DATA`；forward 的 FILLED 明示 synthetic_reference，非真實交易 |
| `evaluation_status` | `PENDING`, `COMPLETE`, `INSUFFICIENT_DATA`, `AMBIGUOUS` |
| `barrier_order` | `STOP_FIRST`, `TARGET_FIRST`, `BOTH_SAME_BAR`, `NONE`；只適用 execution |
| `benchmark_status` | `PENDING`, `AVAILABLE`, `UNAVAILABLE`；缺 benchmark 不清空已有效的 stock return |

- 未到預期 session/horizon：persist PENDING，安排 next due session，不列為 data failure。
- 到期但缺 required stock/calendar/provenance：persist INSUFFICIENT_DATA + reason、所有不可計算 return 為 NULL。retry 同一 logical row，backoff 1h → 6h → 24h，最長 observation window 30 calendar days；過期設 `retryable=false`，仍保留 insufficiency，不改零 return。
- data 補齊可轉 COMPLETE；entry window 完整觀察且沒有 fill 才 NOT_FILLED（evaluation COMPLETE、trade return NULL）。缺 bar 不能判未成交。
- 同一完整 bar 兩 barrier 都觸發且 open 無法確定先後：FILLED + evaluation AMBIGUOUS + BOTH_SAME_BAR，trade return NULL；可另列 bounds，不把 bounds 放 calibration return。
- benchmark 缺日：stock COMPLETE 保留 gross/net，benchmark UNAVAILABLE + `venue_benchmark_date_missing`，relative return NULL；benchmark retry 同上述 backoff。缺 benchmark 不阻止只需 stock return 的標定，也不能把 relative NULL 當零。
- catalog/tax/cost policy 不足時 gross 可以獨立有效，net unavailable 且 reason 明確。需要 net 的 strategy calibration 排除此樣本，gross IC 只有其餘 provenance 合格才可使用。

沿用可對應的 research reasons：`stock_horizon_missing`、`stock_window_invalid`、`venue_benchmark_date_missing`、`invalid_explicit_cost_policy`、`bar_source_contract_missing`、`bar_content_identity_missing`、`bar_receipt_time_missing`、`bar_receipt_time_invalid`。新增 reasons 至少涵蓋 `calendar_unverifiable`、`required_session_missing`、`entry_window_invalid`、`entry_window_expired`、`entry_invalidated_at_open`、`barrier_order_ambiguous`、`cost_authority_missing`、`order_not_applicable`。API exposed status/reason 以此為準；research 的 available/unavailable 用 adapter 明確映射，不能把兩套 vocabulary 直接混用。

## 4. Fill 與 barrier 規則

Execution 初始 policy 只支援 unheld buy / held add 的 long simulation；watch/hold/sell/short 沒有此 entry order，標 `NOT_FILLED` + `order_not_applicable`（evaluation COMPLETE、trade return NULL），不 auto-fill。只有來源真的提供 entry plan 且 `0 < entry_low <= entry_high`、有效 stop/target 才建 order。

- 從 decision date 後的下一 session 起，order 有效 3 個 sessions，`limit_price=entry_high`；這是明示模擬規則，不是 midpoint 假成交。每個 session 要有完整 OHLC 与 provenance。
- open <= stop：未進場即 invalidated，NOT_FILLED；其餘 open <= limit 則按 open fill；open > limit 且 low <= limit 則按 limit fill；沒有觸發則繼續至第 3 session 收盤後 expiry。stop < limit < target 是必要 policy 檢查；缺少／不合法 immutable entry plan 為 terminal INSUFFICIENT_DATA + entry_window_invalid，不能事後把新 plan 填進旧 decision。
- entry-session 不做 exit（初始 overnight simulation policy，和既有 backtest T+1 shape 一致，不宣稱各市場法律規則）。`entry_timestamp` 只有開盤 fill 可用經驗證 session open timestamp；intraday limit touch 為 NULL，另存 entry_session + `entry_time_precision=SESSION_ONLY`。
- exit 從 fill 後下一 session 開始，至 fill 後第 h session。先檢查 open gap：open <= stop → STOP_FIRST 按 open；open >= target → TARGET_FIRST 按 open。否則 low <= stop 且 high >= target → BOTH_SAME_BAR / AMBIGUOUS；只有 stop 或 target 觸發則按 barrier price；都無則第 h session close expiry。
- outcome 不可使用 signal 發出前／同日已形成的 bar 當後續 session。final daily bar 要確定已收盤，不能因當天 date 存在就當完整。

## 5. 零 return、價格 basis 與 costs

805 個等價零 return 是 suspect rows，並無實際 bar dates 證明全部錯誤；NULL-only guard 不足，但也**禁止以 `base_price == outcome_price` 當排除規則**。唯一判斷是 required entry/exit sessions、完整 window、bar身份与 provenance 是否有效。合法的不同 session 同價會 COMPLETE，gross=0，net 可為負。

保存 entry/exit raw price、adjustment mode 与 corporate-action source/version；不能混 raw/adjusted series。corporate-action window 無法證明一致時 `stock_window_invalid`。raw-price return 不宣稱 total shareholder return。

Forward gross = `(exit_close / entry_close - 1) * 100`。net 採明示 `cost_policy.unit=basis_points_per_side`，`stock_cost_assumption_pct=(entry_cost_bps+exit_cost_bps)/100`，`stock_net_return_pct=gross-cost_assumption`，欄位標為 assumed fixed-notional cost；不假裝等同小額 order 的最低佣金實際計算。

Execution net 使用 `cost_model_for_market` + `round_trip_pnl`，保存 quantity/lot/notional、各腿 fill/slippage/fees 與 `execution_net_return_pct`。order quantity/lot/notional 必須在 versioned simulation policy 明示；無 quantity policy 時 net 為 invalid_explicit_cost_policy，不猜預設股數。entry/exit bps snapshot 為其成本 breakdown 的可核對表示；不得重複扣 slip 或把 invested-based PnL 當 research 的 gross-minus-bps 指標。

成本要用 [discovery 契約](discovery-vocabulary-v1.md) 的權威 security/tax classification 與 entry/exit session 各自有效的 dated policy。不能用 `date.today()` 重算歷史稅率，也不能靠 `security_type=ETF` 推論 bond exemption。不得在計畫中宣稱尚未核實的上游欄位已存在。

## 6. Benchmark-relative 與發布

TW 依既有 `_BENCHMARK_FOR_VENUE` 映射 TWSE→TAIEX、TPEX→TPEX。其他市場需明示 benchmark mapping 才有 relative statistic，否则 UNAVAILABLE，仍可保留 stock return。

保存 `benchmark_id`、entry/exit closes、source/hash、價格 basis 與精確 matching sessions；`relative_net_percentage_points=stock_net_return_pct-benchmark_gross_return_pct`，不是扣 benchmark 成本後的兩個 net 相減。缺日不做 on-or-before，不填零。非可比較的価格 basis 標 unavailable。

每份統計列 market、population、horizon sessions、evaluation/score versions、raw/unit/date counts、entry/not-filled/ambiguous/insufficient counts、net/benchmark availability 分母。固定 horizon 與 simulated trade 結果不能共用 win rate 欄位而不標 basis。歷史統計可展示但須註明 legacy-unlabelled；不把 PR-0A capture 前的 replay 當 PIT-valid delivered result。
