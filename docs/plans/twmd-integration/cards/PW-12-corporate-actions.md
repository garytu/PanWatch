# PW-12：除權息與減資事件標記

狀態：completed。依賴：PW-01、PW-04。Owner：pw12_corporate_actions_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

個股圖及研究報告能辨識公司行動造成的價格變化，回測明確標示未調整限制。

## 範圍

- 接 /ex-right-dividend-results 與 /capital-reduction-results。
- 以 canonical ID／事件日保留 realized adjustment 與來源價格欄位。
- 在圖表／研究 evidence 顯示事件標記與影響說明，對 raw daily bars 附限制提示。
- 回測輸出增加已知事件期間／coverage 說明；資料完全缺失時不宣稱沒有公司行動。
- 定義後續調整價或含息回測所缺資料，不在本卡偷偷改寫歷史。

## 驗收條件

- 原收盤、reference、combined adjustment 等欄位不誤當現金股利。
- 減資事件與除權息分開；相同日期／同碼不同 venue 不碰撞。
- 不以 realized result 推定公告日／付款日；缺事件保留未知。
- raw price 回測結果保留原算法並標示限制，新增標記不改持倉／現金。

## 驗證

事件映射、exact values、date／venue、缺資料、chart annotation與backtest result metadata；既有 cost／execution 回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

upstream query corporate-action routes；screenshot/chart collector、research、src/modules/strategy/backtest/。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不交付完整復權／股利再投資／減資持倉模型。完整公司行動與 total-return 回測需另立有資料契約的任務。

## 進度與交接

2026-10-07 Coordination checkpoint：in_progress。依賴 PW-01／PW-04 在總計劃與卡片均 completed；目前 checkout `codex/taiwan-market-support`。Worker：pw12_corporate_actions_worker（gpt-6-luna／xhigh）。既有 agent_catalog.py 與未追蹤個人檔案排除於本卡，保持原樣。上游契約讀取自 `78e6e5b103886456434b6ee3032cdd31ce16943b`，部署版本仍需以唯讀證據分開記錄。

Progress：2026-10-07 implementation worker checkpoint：已完成 typed bounded TWSE TWT49U／TWTAUU reads、獨立 corporate-actions research block 與 route、research panel、Taiwan screenshot chart markers、ChartAnalyst caveats、raw-price BacktestResult metadata、synthetic offline fixtures、contracts 與 Taiwan support 限制文件。Empty responses preserve unknown coverage; event kinds and canonical IDs remain distinct; source decimal tokens remain strings. Parent-owned bounded query-only evidence is in [PW-12-live-contract-2026-10-07.json](../evidence/PW-12-live-contract-2026-10-07.json).

Worker verification: focused backend `packages/marketdata/tests/test_marketdata_twmd_corporate_actions.py tests/test_twmd_corporate_actions.py tests/test_backtest.py` passed **24**; frontend `node node_modules/vitest/vitest.mjs run` passed **24 files／67 tests** after updating the Taiwan panel fixtures; `node node_modules/typescript/bin/tsc -b`, `node node_modules/vite/bin/vite.js build`, and `git diff --check` passed. The full `.venv/bin/python -m pytest -q tests packages/marketdata/tests` run reported **1326 passed／3 skipped／4 failed**. Remaining failures: architecture boundary because screenshot_collector imports a research service; and three prior service tests still assert old block names/read counts (`test_four_research_blocks_keep_exact_values_dates_units_and_period_evidence`, `test_research_reads_queue_all_six_blocks_with_four_active_workers`, `test_timed_out_reads_keep_permits_and_queued_blocks_are_explicit`). Parent owns resolving the injected loader direction, updating expectations, full review and final verification.

Handoff：**review**。Parent independently reviews and owns all subsequent implementation fixes on these paths: `docs/plans/twmd-integration/contracts/README.md`, `docs/taiwan-market-support.md`, `frontend/packages/api/src/research.ts`, `frontend/packages/biz-ui/src/components/taiwan-research-panel.tsx`, `frontend/tests/TaiwanResearchPanel.test.tsx`, `packages/marketdata/src/marketdata/__init__.py`, `packages/marketdata/src/marketdata/types.py`, `packages/marketdata/src/marketdata/vendors/twmd.py`, `packages/marketdata/tests/fixtures/twmd/synthetic/corporate_actions.json`, `packages/marketdata/tests/test_marketdata_twmd_corporate_actions.py`, `src/modules/automation/chart_analyst.py`, `src/modules/research/api/taiwan.py`, `src/modules/research/taiwan_research.py`, `src/modules/research/twmd_corporate_actions.py`, `src/modules/strategy/backtest/engine.py`, `src/platform/marketdata/collectors/screenshot_collector.py`, `tests/test_backtest.py`, and `tests/test_twmd_corporate_actions.py`. Unrelated `src/modules/automation/agent_catalog.py`, `.DS_Store`, `.agents/skills/archify*/`, `.archify/`, `skills-lock.json`, `uv.lock`, and the parent-owned evidence file were not changed by this worker. No commit or next card was started.


Coordinator final checkpoint：**completed**。指派提交 `0e2b6e8`；worker 完成初版並交件 review，主代理接手後獨立讀取完整 diff、上游契約、呼叫端及測試，修正下列問題並重新驗證：

- 圖表 collector 改由 ChartAnalyst 應用層注入共用 research loader，保持 platform 不依賴 modules；明確指定的 TWSE／TPEX 不受 quote hint 蓋過，錯誤 block／row ID 不畫事件。
- 聚合服務的部分事件來源失敗不快取，成功／unknown 結果保持 defensive copies；實際 read permits 與既有 deadline 不變。既有 block／call count 測試更新為九區塊，保留四個實際讀取上限。
- 補上價格非負／減資 principal 嚴格正值、nullable 欄位必須存在、簽名合併調整值與超過 Decimal 預設精度的 exact-token 回歸。
- 部分來源失敗仍保留事件標記與參考價說明；圖表摘要只 escape 一次、換行並完整匯出，修正身份文字與價格刻度的重疊。非 TW chart prompt 保持原行為。
- 回測 metadata 核對 canonical 身分與事件日期／種類，保留 bar range、查詢範圍、各來源狀態與 raw-price 限制，使用 deep copies，不改 trades／metrics／equity／cash／holdings；缺註記仍明示 coverage unknown。
- 新 route 回歸確認登入、市場停用、bounds 與安全 503；正式 UI 回歸確認同日除權／除息、精確原值、TWD／股、部分失敗及未知覆蓋。

最終驗證（主代理修正後）：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1344 passed、3 skipped、14 warnings**，18.38 秒。
- `.venv/bin/python -m pytest -q packages/pan-agent-runtime/tests`：**42 passed**。
- frontend `node node_modules/vitest/vitest.mjs run`：**24 files／68 tests passed**。
- frontend `node node_modules/typescript/bin/tsc -b`、`node node_modules/vite/bin/vite.js build`：passed。
- 畫面定位修正後 `.venv/bin/python -m pytest -q tests/test_twmd_corporate_actions.py tests/test_architecture_boundaries.py`：**14 passed**。
- `git diff --check` 與完整 staged diff／whitespace 查核通過。既有 deprecation、React Router／act／duplicate-key 與 Browserslist warnings 保留。

唯讀運行證據：22:34 Taipei 的四個固定 GET 全部 HTTP 200、6–12 ms、空清單，見 [query evidence](../evidence/PW-12-live-contract-2026-10-07.json)。23:11 Taipei 的正式 `TaiwanResearchService.collect` 查詢 `TWSE:2330`、日 2026-10-02、营收 2026-07..08、2024Q4，aggregate **11444.294 ms**；兩事件讀取 10.756／5.758 ms，均空清單且 block／component 維持 unknown；其他官方資料 available／partial 獨立保留，見 [shared-service evidence](../evidence/PW-12-shared-service-2026-10-07.json)。來源 checkout 與部署 commit 分開，沒有正例 live 事件、完整覆蓋、receipt／revision、publisher SLA 或 as-of 保證；正例映射與畫面使用明確標記的 synthetic fixtures。

畫面查核：正式 TaiwanResearchPanel 元件與專案 CSS 的隔離離線 preview，以及三個 period 的實際 `taiwan_chart_html`，均確認事件、原始數值、null 與限制可见；各圖兩個 marker，SVG text 沒有裁切，研究面板沒有橫向溢出。修正後日／週／月圖再次查核，截图留於本工作階段 visualizations 目錄；臨時 frontend preview 檔與 Vite server 已清除，不提交原型。

Final Handoff：驗收通過，在 `codex/taiwan-market-support` 的唯一最終 Conventional Commit 以 `feat(research): 標記台股公司行動並保留回測價格限制` 為標題，可由本卡 git history 定位；透過既有 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，未合併或部署。PW-11 waiting（上游 benchmark 實際採集），PW-13 waiting（PW-11）、PW-14 waiting（PW-11／交易時段前提），目前沒有 ready 卡。本次到 PW-12 為止。既有 agent_catalog.py 與其他未追蹤個人檔案保留並排除提交；未變更採集、排程、訂閱、控制、DB 或交易策略。
