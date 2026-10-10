# PW-13：歷史資料與新增研究條件驗證

狀態：completed。依賴：PW-05、PW-06、PW-11、PW-12。Owner：pw13_historical_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

評估新增選股條件是否有可用歷史證據，避免回測使用未來公布或後來修訂的資訊。

## 範圍

- 盤點各資料集完整交易日／月份、source receipt、first observation 與 revision；把缺口和外部補資料需求列清。
- 為 PW-06 條件建立 bounded offline research dataset 與 deterministic evaluation，先記錄日期、scope、eligibility，再計算結果。
- 定義可用時間門檻：沒有 verified publisher time／revision-as-of 時，保守用實際 observation evidence 或只做當期描述研究。
- 對無法還原歷史已知版本的資料，禁止宣稱 point-in-time 回測；需要時從現在開始留存 research snapshots。
- 與對應 benchmark 比較，記錄樣本量、缺口、成本與 raw-price 公司行動限制；只有合格資料才提出後续 factor task。

## 驗收條件

- 2026-10 才採集的 2024 財報不得在無更早 evidence 下用於 2024 決策。
- 目前 revision 的 received_at 不能证明更早內容可知；profile latest-only 不做歷史 universe。
- train/eval 時間分離，不以全期資料調參後宣稱 out-of-sample。
- 缺上櫃歷史營收／基準時對該範圍排除並說明，不補造資料。
- 實際可用樣本太少時交付不足證據結論，不能稱條件已驗證有效。

## 驗證

固定時間 available-at 門檻、修訂、missing windows、future leakage、venue benchmark、dataset 可重現hash與現有backtest/factor回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/strategy/backtest/、factor_eval.py、factor_calibration.py；research context/store；上游 revision／receipt 契約。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不自動新增／調整 production factor weights 或交易訊號。歷史補齊、上游 as-of API 和完整 total-return 模型屬另行工作。

## 進度與交接

2026-10-08：PW-05／PW-06／PW-11／PW-12 均 completed，依賴已滿足；本卡 ready、Owner unassigned，尚未開始，等待使用者另行指定。樣本不足仍須如實交付不足證據結論。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。

2026-10-08 Coordination checkpoint：依使用者 execute PW-13 指令開始；board／card 與四項依賴已核對，使用目前 codex/taiwan-market-support checkout。Worker：pw13_historical_worker（gpt-6-luna／xhigh，依本卡固定值）。既有 agent_catalog.py 與未追蹤個人檔案保留且不納入提交。僅實作 bounded offline dataset／evaluation、文件與相關測試；不授權採集、排程、訂閱、部署或策略權重／交易訊號變更。

2026-10-08 Worker handoff：新增 `src/modules/research/taiwan_historical_research.py` 確定性離線 evaluator、`scripts/research/pw13_historical_research.py` 固定證據 inventory 與 immutable snapshot CLI、synthetic-only fixture、兩組測試、runbook 和安全 bounded evidence JSON。固定 scope 為四個 PW-05/PW-11 selectors；實際捕獲的 20 個 PE/PB/yield/revenue YoY/single-day flow 值／null 被保留並綁定來源 receipt/hash 或本地 snapshot time/hash。JSON 載入保留原始 decimal token，純 Python API 拒絕 binary float，以免 decimal 值先經過近似表示。12 個固定訊號只執行 PIT availability audit：`inventory_only`、`evaluation_performed=false`、0 eligible outcome、`insufficient_evidence`、不允許 efficacy claim。Oct 2 決策不採用 Oct 4 才 receipt 的內容，稽核保留被排除 observation 的 period/clock/version；Oct 7 TPEX 最新空值不回退舊值。財報 document-first 與 semantic-revision-first 時鐘分開，2026 年取得的 2024Q4 報告不進入 2024 決策。明列 TPEx 歷史營收、完整 benchmark/session、point-in-time universe、公司行動與台灣交易成本等外部限制。Train/eval split 與 purge、strict-next-date entry、精確 venue benchmark 日期、顯式 bps 成本、完整 canonical hashes 已在可獨立使用的 snapshot evaluator 中實作；production factor weights/signals 未改。

Worker 驗證通過：`.venv/bin/python -m pytest -q tests/test_taiwan_historical_research.py tests/test_pw13_evidence_inventory.py`（15 passed）；`.venv/bin/python -m pytest -q tests/test_backtest.py tests/test_factor_eval.py tests/test_factor_calibration.py tests/test_factor_calibration_loop.py tests/test_factor_weights.py tests/test_factor_scoring_weights.py tests/test_taiwan_discovery.py`（60 passed）；`.venv/bin/python -m pytest -q tests packages/marketdata/tests`（1408 passed, 3 skipped）；預設 evidence CLI 連跑兩次位元相同；synthetic fixture CLI 保持 `insufficient_evidence` 且 efficacy false；`git diff --check` 通過。尚未 commit／開 PR；由原工作階段獨立 review，必要時修正並重跑驗證，再處理完成狀態。可重現入口見 `docs/plans/twmd-integration/runbooks/PW-13-historical-research.md`。


2026-10-08 Coordinator final acceptance：**completed**。指派提交 `689175b`；worker gpt-6-luna／xhigh 交件後，主代理獨立檢查完整 module／CLI／fixture／測試／runbook、上游 receipt／semantic revision 契約與 PW-06 條件。親自修正：

- 同一決策 instant 的不同 UTC／Taipei 表示不得重複計樣本；相同 instrument／entry／exit forward window 只計一次。Train purge 與超出 eval window 的 purge 分開計數。
- 在 latest known period/version 選定後才檢查 unit、source、null 和 freshness；無效最新資料不回退舊列，保留 PW-06 每日 7 個日曆日／月末起 90 日上限與整數法人 threshold 契約。
- 用明確 universe_kind 排除 latest-profile 歷史 universe；條件登記需早於 eval_start 的台北午夜時鐘。Stock raw TWD/share 與 benchmark raw index_points 口徑不得混用，已知 invalid stock window 不移動 horizon 補空缺。
- 限制 20 個 canonical IDs、366 個 inclusive decision dates、各 array 筆數、16 MiB CLI input、integer horizon／explicit costs。快照匯出拒絕覆寫不同版本，report 不能蓋掉 input／snapshot。
- 修正 inventory venue 和 fixture 文件連結；full input manifest 包含財報 raw fixture。保留 2024Q4 report 的 Oct 2026 semantic observation，2024 決策不使用此版本。Generated evidence 與實際 CLI／hash／snapshot replay 一致。

主代理最終驗證（所有實作修正後）：

- `.venv/bin/python -m pytest -q tests/test_taiwan_historical_research.py tests/test_pw13_evidence_inventory.py tests/test_backtest.py tests/test_factor_eval.py tests/test_factor_calibration.py tests/test_factor_calibration_loop.py tests/test_factor_weights.py tests/test_factor_scoring_weights.py tests/test_taiwan_discovery.py`：**95 passed**（其中 PW-13 35 tests）。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1428 passed、3 skipped、14 既有 warnings，18.03 秒**。
- Default CLI 連跑兩次，輸出與本卡 evidence JSON **byte-identical**；snapshot export／input replay／different-content overwrite rejection／same-path report rejection 回歸通過；synthetic CLI 維持 insufficient_evidence、efficacy false。Dataset／policy／report hashes 再核對通過。
- `git diff --check`／`git diff --cached --check` 通過；本卡無 frontend 修改，不重新宣稱前端交付。最終提交範圍排除既有 `agent_catalog.py` 與全部個人未追蹤檔。

最終 Handoff：交付程式、可保留的 bounded offline snapshot、[runbook](../runbooks/PW-13-historical-research.md) 及 [實際盤點](../evidence/PW-13-offline-inventory-2026-10-08.json)，透過 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，最終任務 commit 以本卡 Git history 為準。預設真實資料為 **12 fixed availability audits／20 retained condition values or nulls／0 eligible forward-outcome samples**；明確 inventory_only、evaluation_performed=false、insufficient_evidence，不驗證因子有效性。歷史 TPEx revenue／完整 benchmark-session history、PIT universe、revision-as-of、公司行動與完整台股成本仍需外部資料及另行研究，runbook 記錄恢復條件。Operator-managed snapshot export 不啟用自動排程；没有新增 live 採集／control mutation／訂閱／部署／策略權重或訊號。未合併或部署。下一張 **沒有 ready 卡**；PW-14 保持 waiting，本次完成 PW-13 後停止。既有工作區修改保持原樣，task-owned paths 已提交。
