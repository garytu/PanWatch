# PanWatch Ranking & Calibration Integrity 計劃（V1.3）

建立／修訂日期：2026-10-10（Asia/Taipei）。狀態：**設計已修訂，待實作與驗證**。此次只修改計畫；未修改 runtime、寫入 DB 或啟用 calibration。

目標：分開 discovery universe、ranking reference、signal-forward outcome、simulated execution outcome 與 calibration cohort；每筆決策可按當時 inputs 重現，同一 cohort 不會因排程重試持續改 weight。

## 現況與證據範圍

[2026-10-10 驗證證據](evidence/2026-10-10-db-verification.md) 的原記錄基於 `7d1a334c`；V1.3 review checkout 為 `ba57b1a`，read-only DB 重驗主要 counts。均為本地 checkout，不能當部署環境驗證或已交付能力。

| 已直接觀測 | 能支持的結論／限制 |
| --- | --- |
| 五個 TW snapshot dates 每日 active=20、distinct instrument=5；746 rows 含 unheld cap reason | legacy cap 正在限制 **strategy rows**，不能推論需要 20 個 instrument |
| strategy outcomes=3,341；NULL returns=0；805 rows 等價零 return，756 為 1d | calendar-day/on-or-before producer 有缺陷風險；805 是 suspect set，不是全部已證明髒資料；真實零 return 必須保留 |
| Migration 111 把 candidate outcomes 複製到 strategy table；2,490 rows 按 symbol/date/horizon overlap | table name 不能識別 producer；overlap count 本身不是精確 migration provenance |
| TW 5d outcome=860 rows、297 instrument/date units、4 decision dates | strategy/horizon 重複造成 row sample size 膨脹；不同 signal ids 也不全是獨立樣本 |
| factor history 記錄重複 IC/IR、n=860 並伴隨 weight 漂移 | 缺 cohort identity 的多次 blend；歷史無 fingerprint，不能證明每輪 input 集合完全一致 |
| `TWSE:00631L` 有 8 筆 active buy / score=100 | ETF 已進普通 opportunity path；metadata 與 eligibility policy 必須明示 |
| active buy score 飽和；paper sizing 的 85/75/65 對應 .25/.18/.12 | rank ordering、display scale、eligibility 與 sizing 必須分開並一起驗證 |

現可復用：`TaiwanDiscoveryPool` 的 bounded scan metadata、`trading_calendar.py`、research `_forward_outcome` 的欄位/provenance/reason shapes、`strategy_catalog.params.horizon_days`、`backtest/engine.py` 的 open/gap/barrier path，以及現有 factor pin gate。它們不保證互相 policy 相同；需 pure kernel + 明示 adapter，不能直接重用不同口徑的 return。

## 已定義的契約

- [Ranking / calibration](contracts/ranking-vocabulary-v1.md)：版本、不可變 decision snapshots、cohort fingerprint、atomic write、floor/confidence、ranker-v2 consumer mapping、TW instrument constraint。
- [Outcome](contracts/outcome-vocabulary-v1.md)：forward vs execution、session/entry/barrier policy、v2 schema、retry、gross/net/benchmark availability 與發布分母。
- [Discovery / authority](contracts/discovery-vocabulary-v1.md)：每 stage accounting、COMPLETE 的 scope、bounded ledger、run linkage、catalog/tax authority 與 compatibility。

初始工程數值已具體寫入契約，但**尚未獲 shadow replay 或經濟有效性驗證**：sigmoid display mapping、TW cap=5 instruments、risk=.32、strategy share=.42、calibration floors 與 confidence policy。這些是受驗證的 proposed defaults，不能寫成已證明最佳值；變動必須新 policy/config 與驗證報告。

## 工作流與交付結果

| card | 內容 | PR / 依賴 |
| --- | --- | --- |
| [RC-A](cards/RC-A-calibration-integrity.md) | FROZEN mode、pin、不可變 decision/reference、cohort/application ledger、primary horizon、floor/confidence、恢復 gate | PR-0A；PR-3 恢復依 PR-0B、PR-1、PR-2 |
| [RC-B](cards/RC-B-discovery-coverage.md) | structured pool plumbing、stage manifest/ledger、catalog/security/tax authority、API/UI | PR-0B，依 PR-0A 的 decision identity |
| [RC-D](cards/RC-D-point-in-time-outcome.md) | v2 populations、完整 sessions、fill/barrier policy、persistent states/retry、gross/net/benchmark | PR-1，依 PR-0A、PR-0B |
| [RC-C](cards/RC-C-portfolio-constraint.md) | ranker-v2、consumer mapping、instrument-level stable selection、explicit TW quotas與可見性 | PR-2，依 PR-0A、PR-0B、PR-1 |

## PR 順序與不可跳過的 gates

**PR-0A containment / identity → PR-0B metadata / coverage → PR-1 outcomes → PR-2 ranking / constraints → PR-3 gated calibration restart**。

| PR | 交付 | merge / rollout gate |
| --- | --- | --- |
| PR-0A | service-level FROZEN on all enabled markets + ALL；strategy pin；baseline export/hash；immutable captures/reference；application ledger與atomic uniqueness；floor/confidence policy | scheduler/API/direct calls 都不能改 weight；duplicate/restart/concurrent calls tests；captured raw inputs 可回讀。新標籤不代表舊 evaluator 已合格 |
| PR-0B | run/ledger persistence、stage counters、security_type/venue/is_active 与 authority snapshot、tax policy adapter與 unknown fallback；API/UI run linkage | counters reconcile；partial/failed/bounded scopes 正確；不擴 scan、不增加 per-symbol quote request；未知 tax 導致 net unavailable。仍 FROZEN |
| PR-1 | new v2 outcome table與attempt ledger、shared kernel/adapters、session/fill/ambiguity/retry、dated costs與benchmark availability | zero/stale/gap/holiday/missing/final-bar/cost/migration fixtures；legacy 不阻擋 v2、新舊 policy 可辨識。仍 FROZEN，可開始 SHADOW |
| PR-2 | ranking_value/display_score、共同 deterministic selection、TW 5-instrument cap與quota、Dashboard/fallback/AI/sizing mapping | 同 inputs 任意 permutation 得同結果；post-dedupe counts、filter/sizing replay 完整；version分隔。仍 SHADOW，不以這個 PR 解凍 |
| PR-3 | primary-horizon aggregation、factor reference dedupe、training/holdout purge、confidence/readiness report與market/kind activation | offline regression全部通過、合格 v2 cohort達floor/confidence且shadow report完成；不足繼續 SHADOW。只按已合格 market/kind 設 ACTIVE |

PR-0A 凍結會留下現有 drifted weights，這是明示 baseline，不是修復權重品質。此次不 blanket reset；需 restoration 時使用具名設定與 audit。PR-0B 尚未有 tax subtype authority 時先交付 metadata/coverage；net eligibility 維持 unavailable，不能為放行 PR-1 虛構分類。PR-3 可以交付 gate 而市場仍 SHADOW；文件必须列實際 mode，不能把 gate 已實作當已成功恢復自動標定。

## 決策記錄

| 問題 | V1.3 決定 |
| --- | --- |
| 805 零 return 是否一律排除？ | 否；以 bar/session identity 与 completeness 驗證；保留真實零 return |
| population id 是否足夠防重複？ | 否；producer/basis type + exact cohort fingerprint + atomic unique application |
| weight change 是否讓同 cohort 可再 apply？ | 否；output config 不在 application dedupe key |
| 既有 signal/factor upsert 可當 PIT archive？ | 否；append-only captures，projection 可變但 calibration join 不可變 |
| insufficient data 是否不落 row？ | 否；persist state/attempt，maturity 和缺資料分開，可重試 |
| stock valid 但 benchmark missing？ | 保留 gross/net，relative=NULL + benchmark UNAVAILABLE；分母另列 |
| 新 evaluator 遇 legacy unique signal/horizon？ | 新 v2 table，以 decision/population/horizon/version 唯一；不覆寫 legacy |
| TW cap 單位與數值？ | 初始 5 distinct EQUITY instruments；不把 legacy 20 rows 升成 20 instruments |
| rank_score 是何種量？ | 固定 sigmoid display rank tier；ordering用uncapped ranking_value，eligibility依原 candidate gate |
| ETF 普通 opportunity 與 bond cost？ | 普通 EQUITY/ETF 分組；bond tax 需獨立 authority + dated policy，security_type 不足以判 exemption |
| ALL cold start？ | 不讓不足本市場 floor 的市場靠 ALL 解凍；baseline/fallback與版本明示 |
| 如何回退？ | market/kind 設 FROZEN；保留 inputs/outcomes/audit，必要時 audited config restoration |

## 驗證協議與最小矩陣

實作前讀 card、三份契約與 `AGENTS.md`，確認當時 source/upstream contract。實作在 `codex/` branch 依 PR 順序交付；PR title/commit 使用中文 Conventional Commits，正文含背景、變更內容、驗證、邊界與風險、後續計劃。測試 fixtures 離線、time-fixed；production DB 僅做 bounded read-only inspection，migration/retry 以 temporary DB 驗證。

| 測試 | 必須證明 |
| --- | --- |
| Legitimate zero / stale bar | 不同 required sessions 同價為 valid zero；reuse entry bar 為不足資料，不填0 |
| Application identity | 同cohort重試／重啟／併發只apply一次；新input ids才新cohort；output version改變不解除唯一性 |
| Atomicity / stale config | crash rollback 不留下claim或半套weights；API/scheduler一致；live config race整批reject |
| Containment / provenance | 新 population + 舊 evaluator、不明 available time、legacy rows 都不能自動write；pin生效 |
| Immutable decisions | same-day refresh／資料修訂／projection delete 不改已封存factor與outcome reference |
| Sample units / floors | 重複策略或多horizon不增加factor unit；小rebound cohort拒絕；20 training dates與5holdout dates分開 |
| Session maturity | holiday、missing required session、calendar fallback、intraday unfinished daily bar 分開處理 |
| Entry / barrier / policy | complete unfilled expiry、gap open、same-bar ambiguity；forward/legacy backtest/execution各自policy不混 |
| Persistent retry / versions | insuff row可補資料完成，NOT_FILLED terminal；legacy唯一key不阻v2，新version共存 |
| Cost / benchmark | unknown tax→net unavailable；gross存續；dated cost与no-double-slip；missing benchmark不填零excess |
| Coverage stages | catalog exclusions、failed/pending/stale/missing、top-N、candidate exclusions、bounded COMPLETE各stage sum正確 |
| Selection / rank consumers | cap按instrument、duplicate不占budget；tie在constraint前稳定；UI/fallback/AI score version一致 |
| Rollout / cold start | ALL与未達floor市場保持SHADOW；回退FROZEN不刪audit；window以decision/exit而非created_at |

Backend 相關 regression（按 card 擴充後執行）：

```sh
.venv/bin/python -m pytest -q tests/test_factor_calibration.py tests/test_factor_calibration_loop.py tests/test_factor_eval.py tests/test_factor_scoring_weights.py tests/test_factor_weights.py tests/test_factors_api.py tests/test_strategy_outcome_efficiency.py tests/test_strategy_signal_candidate_identity.py tests/test_backtest.py tests/test_paper_trading_position.py tests/test_paper_trading_allocation.py tests/test_discovery_routing.py tests/test_taiwan_discovery.py tests/test_pw13_evidence_inventory.py packages/marketdata/tests/test_twmd.py
```

新增 integration fixture/module 覆蓋 v2 migrations、service containment、scheduler ordering、atomic concurrency、run linkage 與 dashboard selection；現有 test 名稱不代表新能力已測。`evaluate_once` 現將 strategy evaluation 與 rebalance 併行，須改為 outcome commit 完成後才 sealed cohort/calibration，且仍由 service gate 防並行 caller。

Frontend 在 `frontend/`：`pnpm exec vitest run src/components/DiscoveryPanel.test.tsx` + PR新增 dashboard/opportunities tests、`pnpm exec tsc -b`、`pnpm build`。每PR需 `git diff --check`，若計畫仍untracked，用 `git diff --no-index --check /dev/null <file>` 檢查新檔。文件修訂只驗證本地 links、契約一致性與 whitespace；不宣稱已執行上述 implementation tests。

## 邊界與風險

- 本地 counts 是一次 DB snapshot；不能推論 deployed container 或所有歷史cohort。Migration 111 已證實，但 exact migrated row identity 不能只靠 overlap 恢復。
- PIT archive 增加儲存與retention成本；180天 manifest/ledger 可清理，但 referenced decision/config/authority/outcome/application 必須 archive，不能cascade丟失audit。
- Snapshot 前的資料保留 legacy；不做無證據 backfill。固定 horizon benchmark是價格return，不是total return。新 simulated execution不是實際成交。
- ranking mapping、EQUITY/ETF eligibility與instrument cap會改名單/數量，需shadow distribution/exposure報告；初始不提高5-instrument cap。不把五觀察日當風控參數最佳化證據。
- 範圍含backtest policy adapter／sizing與offline compatibility驗證；不含真實paper order執行改造、通知規則、twmd upstream部署或自動修改費率。未知tax/benchmark/provenance保留unavailable。
- 樣本不足時保持SHADOW是正常結果，不降低floors為趕交付。之後僅確實需要：足夠新session後重跑readiness、取得缺失authority/benchmark、另案驗證提高cap或調整mapping。

## V1.2 → V1.3 變更

1. 805零return改為suspect set；刪除價格相等即排除的規則。
2. 補不可變decision/reference與exact cohort fingerprint；output weight/version不解除atomic唯一性。
3. pin/floor/containment前移PR-0A；metadata/cost authority前移PR-0B；增加PR-3按market/kind恢復。
4. 拆forward與execution populations；明示entry/expiry/gap/barrier、完整sessions、persisted retry與新v2唯一key。
5. gross/net/benchmark availability拆開，保留有效stock return；cost使用dated authority，修正bond suffix的定位。
6. 明示ranker-v2 formula與每consumer尺度、TW instrument unit/5-cap與constraint前tie-break。
7. 補各stage discovery accounting／scope／failure states與run linkage。
8. 補migration、crash、concurrency、PIT、dedupe、time split／shadow／rollback驗收；修正五snapshot dates与860 matured rows的不同分母。
