# Ranking / Calibration 契約 v1

狀態：V1.3 設計契約，待實作與驗證。建立／修訂：2026-10-10。此文件定義版本身分、不可變決策、標定門控與排名；outcome 與 discovery 分別依另外兩份契約。

## 1. 版本與母體

| field | 定義 |
| --- | --- |
| `ranker_version` | 排名公式、橫截面 tie 處理、代表選擇與排序規則的版本 |
| `scoring_config_version` | 不可變設定內容的 hash：策略／因子權重、候選 eligibility gate、顯示映射、篩選與 constraint policy；設定 payload 必須可回讀 |
| `evaluation_version` | entry、session horizon、barrier、價格調整與 cost policy 規則版本；不得只寫一個常數掩蓋規則變更 |
| `outcome_population_id` | producer 與測量對象的種類；不是一批樣本的唯一身分 |
| `calibration_policy_version` | 標定公式、horizon、window、dedupe、sample floor 與 confidence policy 的版本 |
| `cohort_fingerprint` | 本次真正參與統計的不可變 input records 與 policy 的 canonical SHA-256 |

母體固定為 `legacy-unlabelled`、`signal-forward-v2`（下一完整 session 收盤起算的固定 horizon）、`strategy-execution-v2`（entry order 與 barrier 模擬）。前者不得進自動標定；candidate outcomes 另用 `candidate-forward-v2`，不得併入 signal 母體。Dashboard 的可執行名單不是標定母體。

新的 decision、outcome 與 calibration audit write 都須帶適用版本。不得用「當下 live 設定」替歷史 signal 補版本。版本缺失時寫明 rejection reason；不得進入自動 weight write。

## 2. 不可變決策與橫截面 reference

PR-0A 新增 append-only `ranking_snapshots`（一次 capture）與 `ranking_snapshot_items`（capture 中的 instrument × strategy）。記錄 `snapshot_id`、`decision_snapshot_id`、timezone-aware `decision_at_utc`、市場當地 session/date、`captured_at_utc`、candidate/source identity、quote/bar 的 source time、available time、receipt time、capture/hash、catalog authority、原始因子值與版本、entry/stop/target、eligibility、ranking input 與所有設定版本。

- `decision_snapshot_id` 為不可變 item 的持久 id；重試相同 capture/item 不得重建不同內容。新 refresh 是新 capture；舊 item 永不 upsert 或 cascade delete。
- `StrategySignalRun` 與舊 factor snapshot 可以繼續作為可變 UI projection；v2 outcome/calibration 只能 join 不可變 item。修訂資料須另建 capture，不能回寫舊 decision。
- factor/quote availability 必須 `<= decision_at_utc`；事後取得或無可驗證 available time 的輸入標為 `point_in_time_status=UNVERIFIABLE`，不得進 v2 自動標定。receipt time 與 available time 分開保存。
- 保存當次 pre-constraint、已限定數量的 reference universe id、完整 item ids/hash、market/source partition、scan run id 與 eligibility reasons；不得日後用最新 universe 重算舊 percentile。
- `_rank_map` 改為 average rank for ties。N > 1 時 percentile = `100 * (N - average_ascending_rank) / (N - 1)`；N = 1 時為 50。未觀測值保持 missing，由 versioned scoring policy 處理，不冒充實際零值。
- PR-0A 開始 capture 的資料只有通過 RC-D producer 與 provenance 驗證後才可能合格；新 population 名称不是品質保證。UI projections 的刷新與歷史 outcome 排程不得把已知未来資料加入舊 decision。

## 3. Atomic calibration application

`calibration_applications` 保存 kind（factor/strategy）、market、target（factor code 或 strategy code + regime）、policy version、cohort fingerprint、source config versions、expected live config、output config、sample/date counts、horizon、old/target/new weight、mode、reason 與時間；`calibration_provenance` 放上述 input provenance，避免和 research 的 `_factor_provenance` 混用。

Fingerprint inputs：實際選中的 immutable decision ids/raw factors、已完成 outcome revision ids/input hashes/returns、population、ranker/evaluation versions、primary horizon 與 calibration policy。採 canonical serialization、固定排序；hash 只涵蓋該 kind 實際使用的 input（factor gross 或 strategy net）。benchmark-only 補齊、retry timestamps 或 display metadata 不變更 stock cohort fingerprint。不得加入 scheduler tick time、live/output weight、四捨五入後 IC 或單純 `sample_size`。相同 inputs 即使 window 的當天日期改變，仍是同一 cohort；數值相同但新樣本 ids 加入則是新 cohort。

Application unique key = `(kind, market, target, calibration_policy_version, cohort_fingerprint)`。新 output `scoring_config_version` 不會解除這個唯一性。

1. 在一致的 read snapshot 中選 cohort、計算統計与 fingerprint；所有因子共用同一 sealed cohort。
2. 在短寫交易內，先取得 SQLite write reservation，再重查 persisted mode、pin state、expected live config 與唯一 key。
3. 對同一 market/kind 批次，application claim、weight updates、history、output config 一起 commit；失敗一起 rollback。若 live config 已變，整批 reject `STALE_CONFIG`，不可拿舊結果套新 weight。
4. 相同 cohort 重試、重啟、兩個並行 worker、scheduler 與 API 同時呼叫只可成功一次。重複記錄為 `DUPLICATE_COHORT`，不得改 weight/history；audit 不必每 tick 無限新增相同 rejection。
5. evaluation/calibration policy 改版須另有 rollout record；不得每輪自動增加版本繞過 gate。input provenance 改變可另產 cohort，但既有已完成 outcome 不可原地修改。

## 4. Containment、樣本門檻與恢復

PR-0A 落地時各 enabled market 與 `ALL` 的 persisted `calibration_mode=FROZEN`。所有 public API、scheduler、直接 service call 都在 write service 層檢查；API 不提供 force bypass。`StrategyWeight` 新增 `is_pinned`／`auto_calibrate`，factor 保留現欄位。模式：`FROZEN` 不改 weight、`SHADOW` 只產建議與 readiness report、`ACTIVE` 才能通過所有門檻後寫入。

保存 rollout 前每個 weight/config/history 的讀取快照與 hash；初始保留現 weight，標為 `unvalidated-baseline`。不自動重設為 1.0 或 catalog default。需恢復 baseline 時，必須選一份具名、versioned config，以獨立 audited restoration 操作完成。

以下為保守的**工程初始預設**，不是已驗證的統計顯著性或獲利保證；只能在新的 policy version 與離線報告中調整，不能為通過門檻臨時降低。

| market | strategy：unique instrument/date/strategy | factor：unique instrument/date | distinct decision dates | daily IC minimum / valid IC periods |
| --- | --- | --- | --- | --- |
| TW | 60 | 60 | 20 | 5 / 20 |
| CN | 60 | 60 | 20 | 5 / 20 |
| HK | 60 | 60 | 20 | 5 / 20 |
| US | 60 | 60 | 20 | 5 / 20 |
| ALL | 120 | 120 | 30 | 每市場分開計算；至少 2 個市場各通過本市場門檻 |

- Strategy：每 strategy 採 immutable `strategy_catalog.params.horizon_days`（明確改解讀為 sessions，copy 到 decision）。只取該 primary horizon 的 `signal-forward-v2`；不把 1/3/5/10 session 當四筆獨立交易。per-market snapshot date cutoff 以 decision 日期及實際 `exit_session` 完成狀態判斷，不能用 outcome `created_at` 或 calendar-day maturity。
- 同 instrument/date/strategy 多次 capture 取當日第一個 PIT-valid eligible decision，tie 用 immutable id；規則在看 outcome 前決定。記錄 raw rows、distinct signal ids、instrument/date units、decision dates 與去重數量。
- Factor：固定 5 sessions，跨策略同 instrument/date 只取第一個 PIT-valid eligible capture 的 canonical factor item，strategy code ascending tie-break；不得以最高 return、最高排名或 outcome availability 改選另一個 item。目標量明確為此 reference factor exposure，不聲稱涵蓋全部策略因子。
- Factor 使用 raw factor 值與 `stock_gross_return_pct` 的 IC；懲罰因子保留符號翻轉。Strategy 使用 `stock_net_return_pct` 計 win rate/avg return，benchmark excess 為另列診斷。execution 母體不得替代 forward 母體；真實零 return 保留、NULL/未完成/不可驗證樣本排除。
- ALL 的 aggregation 保留 market 欄位與 units，market/date 分層；不足本市場 floor 的市場不能藉 ALL 更新權重。本市場 cold start 保留具名 baseline；不得因 TW 新 cohort 漂移 CN/HK/US fallback。
- 以 decision date block 做 deterministic bootstrap（固定 seed、1,000 次），報告 IC／mean net return 的 95% interval；同日與重疊 horizon 不當獨立資料。interval 跨 0 或缺失，factor/strategy 各自保持 shadow，weight 不更新。這是保守門控，並不宣稱解除所有序列相關。
- PR-3 恢復必須同時通過：只讀 v2 合格母體、floor/confidence、migration/retry/concurrency tests、所有 writer 同一 gate、time-ordered shadow replay。floor 僅計 training cohort，另保留至少最後 5 個 decision dates 作 holdout（training exit 跨 holdout boundary 的樣本 purge）；候選權重只用較早 training 算，holdout 不參與調參。報告 baseline/candidate 排名變動、coverage、net/excess 與 rejection counts。
- 按 market/kind 分別啟用；floor/confidence 缺失就維持 SHADOW。回退立即 FROZEN，保留 snapshot/outcome/audit，不刪歷史以「修正」結果。

## 5. Ranker v2 與 consumer mapping

候選 eligibility 仍依 `entry_candidates` 的 62/55 gate 與 entry plan；它不是 ranking percentile，也不是成功機率。

初始 ranker-v2 保留 raw factor 合成與 strategy/regime multiplier，另存無上限的 `ranking_value = raw_score * strategy_weight * regime_multiplier`。有限值才可排名；NaN/inf 為 invalid input，不強制變 0。rank/display 分開：

```text
rank_score = 100 / (1 + exp(-(ranking_value - 100) / 20))
```

100 與 20 是固定工程設定，屬 scoring config，不能每 scan 依當日 min/max 改動。用數值穩定的 sigmoid；排序用未四捨五入的 `ranking_value`，API display 四捨五入只在序列化時做。例：110 → 62.25、130 → 81.76、150 → 92.41；不把不同 high raw scores 一律夾成 100。UI 明示是「排名分」，不是 win probability。

| consumer | v2 決策 |
| --- | --- |
| Eligibility / buy action | 62/55 仍看原 candidate score 與 entry plan；rank_score 不產生可執行 action |
| Portfolio selection / API sort | active/action priority 後以 ranking_value descending，stable key ascending |
| Dashboard min_score=55 / Opportunities default=70 | 比較 v2 rank_score；UI 必須標示 score version，replay 報告新增／消失數量，避免悄悄改語義 |
| UI >=80 colour | 顯示分 threshold；標籤為 rank tier |
| `_position_weight` | 同 PR 採 v2 rank_score：>=85 為 .25、>=75 為 .18、>=65 為 .12，其餘 .08；重接 consumer 並測試數值映射，非宣稱必须改這些 budget 比例 |
| Dashboard action_limit=6 / slice(0,3) / fallback | 數量不變；先按 instrument 去重再 limit，fallback 亦使用同 stable selection |
| AI enrichment / `to_ai_score` | 以明示 scale/version 接收 rank_score，不重夾 ranking_value、不宣稱 calibration probability |

舊 action/status 用來壓低分數的 presentation caps 不作用於新 ranking_value；可執行與 inactive 必須分組／標示，不能僅依分数看 action。舊 projection score 保留 legacy version 標記；不要把 legacy 与 v2 scores 混算 min_score。PR-2 須提供離線 replay 的 score 分布、tie 數、top instruments、filter retention 與 sizing bands；synthetic x=110/130/150 須跨至少兩個 budget bands。沒有 frozen inputs 的歷史資料只能作 exploratory replay，不能作 PIT/獲利驗證。

## 6. TW constraint 單位與穩定選擇

TW 初始 `constraint_unit=INSTRUMENT`，cap=5 個 distinct `(market, canonical instrument_id)`，high-risk ratio=.32，single-strategy share=.42。這是保留目前「20 rows → 5 instruments」量級的保守預設，不是把 20 擴為 20 instruments。CN/HK/US 在本次維持既有 row-count 行為與值，但應在 manifest 明示其 unit。`MAX_SINGLE_STRATEGY_SHARE` 現為 scalar，須引入 per-market override（TW=.42），不能聲稱它已是缺 TW key 的 map。

TW 在 constraint 前選 instrument representative：active first；action priority 明確為 buy/add > hold > watch > avoid（sell/reduce 是 held-management 獨立流）；接著 ranking_value descending、strategy_code ascending、canonical instrument_id ascending、decision_snapshot_id ascending。每個 instrument 其餘 strategy rows 留 audit，不占 cap、不產生重複 budget/order；group risk 採該 instrument 所有 eligible strategy rows 的最高 risk，避免低風險代表掩蓋高風險。Dashboard/fallback 共用這個規則。

- selection run/ledger 與 immutable decision 分開保存，連到 decision ids/config version；constraint 不回寫已封存 raw eligibility/factors。未選 instrument 的所有 projection strategy rows 一起標 constrained；selected_for_portfolio 只在代表 row 為 true。
- cap、risk、concentration 都作用在 eligible unheld instruments；held-management 不計入 unheld cap，另報 counts。
- 先以 stable key 取至多 cap，再按 final selected instrument count 算 quota：`max(1, floor(n * share))`。每輪移除最弱的超 quota instrument，再重算到固定點；最多移除 cap 次、不做補位。n 很小時「至少 1」可高於比例，須報 `SMALL_SET_QUOTA_EXCEPTION`、實際 ratio 與 integer quota，不冒充嚴格 percentage 保證。
- Instrument 的 representative strategy 決定 concentration group。ledger 記 `UNHELD_CAP`、`HIGH_RISK_RATIO`、`STRATEGY_SHARE` 或 `DUPLICATE_INSTRUMENT`，附 before/after counts、unit、quota、policy version。一個 boolean 欄位加 reason-code list，不能把多個原因壓成不明字串。
- 見到 `active == cap` 不是 binding 證據；只有存在合格且因 cap 排除的 instrument 才標 binding。現 746 筆是 legacy row constraint 證據，不能當新增 instrument exposure 的理由。
- 初始 cap 不提高；任何日後提高另需 shadow exposure/selection 報告與 policy 變更。資料庫現無 paper trades，僅驗證 sizing/selection，不聲稱實際執行結果。
