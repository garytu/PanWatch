# PW-11：官方大盤基準與相對表現

狀態：completed。依賴：PW-01、PW-04（已完成）；授權有界採集及實際運行驗收已完成。Owner：pw11_benchmarks_worker（gpt-6-luna／xhigh），主代理完成獨立審查／修正／驗證。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

台股首頁顯示加權／櫃買基準，個股研究能比較相同交易日期的價格表現。

## 範圍

- 接 /benchmarks definitions 與 /benchmarks/{id}/bars；benchmark identity 和 stock identity 分開。
- 依股票 venue 選 TAIEX／TPEX，支援首頁指數與每日 spark。
- 計算有明確起訖／共同觀察日期的 raw price return 及相對表現；缺 benchmark 時獨立 unavailable。
- 產出有界上游採集 runbook：TAIEX 月資料、TPEX latest-only 留存；取得授權及資料後再作 live smoke。
- 記錄產品最小可用範圍和 upstream missing，而非等待虛構完整 TPEX 歷史。

## 驗收條件

- index_points 不當成 TWD／可交易股票；沒有 volume/turnover 不生成它們。
- 零筆 benchmark bars 不能生成假的線或報酬；個股圖仍能使用。
- benchmark 缺日不補假價格；以已驗證交易日／共同日期處理，不能因週末 coverage MISSING 就斷言採集故障。
- 以實際取得的日線資料驗證首頁、spark、venue mapping與共同日期比較。
- 相對表現標 raw price basis，含息報酬不混入。

## 驗證

market indices、spark cache、benchmark units／venue、partial/gaps／不相交日期、無資料 fallback；UI及實際 bounded smoke。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/market/api/market.py、kline_collector.get_index_klines、MarketData index methods；upstream docs/benchmarks-api.md。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

計劃基線兩個指數皆零筆；程式契約可先做，卡的運行驗收要等資料實際取得。不自動部署／建立 schedule；即時 quote 留 PW-14。

## 進度與交接

Coordination checkpoint：in_progress。使用者已指定開始 PW-11，覆寫原 waiting 的實作啟動限制；PW-01／PW-04 已完成。2026-10-07 23:50 Taipei 的初始唯讀查詢確認 TAIEX／TPEX 日線均零筆；授權的有界上游採集後，coordinator 已以實際 TWSE／TPEx 資料完成獨立 shared-service positive acceptance。Worker 完成本卡實作、離線驗證與採集 runbook，交由 parent 獨立 review／最終 QA；使用目前 codex/taiwan-market-support checkout，不提交、不編輯狀態／owner／board。

Worker handoff 2026-10-08：加入獨立 benchmark identity、Decimal index-point bars／raw TWSE-TPEX stock daily reads、官方首頁日期型日線與 spark、TW venue routing，以及研究面板以實際共同日期計算不含股利的原始價格報酬和百分點差。有效 partial/truncated 讀取保留來源旗標並可依 provider/credential/identity/period 快取；錯誤讀取不快取，返回資料採 defensive copy。TAIEX 選取 limit 小於 coverage available count 的情況有回歸測試；TWSE 月度來源 query URL 的 `date=...` receipt 保留並依固定 HTTPS publisher path 驗證。操作邊界記於[基準採集 runbook](../runbooks/PW-11-benchmarks.md)：TAIEX 有界 inclusive 月範圍、TPEx latest-only 累積、未啟用 schedule，且不補造 TPEx 歷史。上游正向 shared-service acceptance 由 coordinator 留存在 [`PW-11-shared-service-2026-10-08.json`](../evidence/PW-11-shared-service-2026-10-08.json)；此證據由 coordinator 管理。

Worker verification：`.venv/bin/python -m pytest -q tests packages/marketdata/tests`（1376 passed、3 skipped）；`frontend/node_modules/.bin/vitest run`（25 files、70 passed）；`frontend/node_modules/.bin/tsc -b && frontend/node_modules/.bin/vite build`（passed）；`git diff --check`（passed）。`pnpm test -- --run`／`pnpm build` 因 pnpm 嘗試寫入 workspace 外的 global lockfile、遇到 `ERR_PNPM_LOCKFILE_WRITE_FILE`，改以相同已安裝的 Vitest／TypeScript／Vite 執行檔完成驗證。等待 parent 獨立 contract review 與最後 UI QA；worker 不提交，未執行額外上游操作。


Coordinator final acceptance 2026-10-08：主代理獨立審查並修正 stock canonical ID（首碼數字、4–6 碼）、coverage 與 total／complete／partial／evidence_truncated 的一致性，補 malformed／零筆來源及 identity 預檢回歸。逐日共同觀察保留 stock dataset／partition／status／record_count／acquired_at／checksum 與 benchmark 實際月 URL／revision／capture／captured_at／hash；UI 可展開逐日來源，採集與發布時間不混用。正式研究面板手機查核發現 TDCC 精確百分比和財報 revision 長字串溢出，補換行而不截短原值。

- 使用者明確授權兩次手動採集：TAIEX 2026-09-01..10-06（`run_71260d6aeb9b464b`）及 TPEX 空 scope 最新一次（`run_ba60ae238ae749fc`），均 SUCCEEDED。留存 TAIEX 24 日、TPEX 5 日至 10-07；11 個既有 schedules 和 4 個股票 subscriptions 前後一致，兩個 benchmark schedules 保持 disabled。未部署、未新增／啟用排程，沒有進一步採集。
- 正式 TaiwanResearchService：TWSE:2330 13,829.423 ms，共同 24 日（09-01..10-06），相對報酬 -0.1785875955 百分點；TPEX:5347 12,520.527 ms，共同 4 日（10-01..06），+0.6482919039 百分點。與獨立原始收盤 oracle 的共同日期／起訖／兩側報酬／百分點差全部相符。首頁 TAIEX 20 點、TPEX 5 點與實際日期一致，partial／truncated 原旗標保留。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：1393 passed、3 skipped、14 既有 warnings（18.38 s）。frontend `node node_modules/vitest/vitest.mjs run`：25 files／70 tests；補逐日 DOM assertions 後 `node node_modules/vitest/vitest.mjs run tests/TaiwanResearchPanel.test.tsx`：13 passed。`node node_modules/typescript/bin/tsc -b`／`node node_modules/vite/bin/vite.js build` 通過。正式元件＋實際 payload 在桌面 1280×900／手機 390×844、TWSE／TPEX 共四組查核均無 page errors／頁面橫向溢出，逐日 receipt 可展開且實際時間與官方 URL 可見。`.venv/bin/python -m pytest -q packages/pan-agent-runtime/tests`：42 passed。`git diff --check`／staged diff 檢查通過。正式驗收後已清除臨時 preview 並停止本機 server。
- 安全證據：[初始零筆](../evidence/PW-11-live-baseline-2026-10-07.json)、[採集](../evidence/PW-11-acquisition-2026-10-07.json)、[取得資料](../evidence/PW-11-live-acquired-2026-10-07.json)、[正式共用服務](../evidence/PW-11-shared-service-2026-10-08.json)、[畫面查核](../evidence/PW-11-ui-qa-2026-10-08.json)。
- 限制：TPEx latest-only 不代表完整歷史；日曆 gaps 不推斷休市／採集故障；raw price 不含股利或公司行動調整，無 point-in-time／發布時刻／更新 SLA 保證。上游 source checkout 已知，deployed commit 未公開。正式研究及首頁在本機驗收，尚未合併／部署。

PW-11 completed，PW-13 的 PW-05／PW-06／PW-11／PW-12 依賴均已完成，標記 ready，未開始實作；PW-14 仍等待獨立上游配置與交易時段驗收。最終任務提交與交付 PR 以 Git history／[PR #1](https://github.com/garytu/PanWatch/pull/1) 為準。
