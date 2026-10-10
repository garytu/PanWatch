# PW-07：融資融券與集保持股分布

狀態：completed。依賴：PW-01、PW-04。Owner：pw07_margin_shareholders_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

提供個股融資變化與保管帳戶分布，支援籌碼研究及 AI 解釋。

## 範圍

- 接 /margin-short-sale 與 /shareholder-distribution；需要時用 coverage API 交叉檢查。
- 融資沿用相容的既有 margin 介面；集保保留 distribution 專用模型。
- 顯示最新與可比較的前一期；先明確定義大額持股門檻和比例 denominator。
- 在個股研究與 AI 增加籌碼區塊，顯示 native units、report date、report_variant 及來源。

## 驗收條件

- 股／張／trading_units／金額不混用；不可填入不存在的現金融資餘額。
- bulk_current 的 adjustment／total 與 historical_html 的 total 正確處理，不重複加總。
- 不能跨不相容 variant 直接計算變化；缺前一期則沒有變化值。
- TDCC bucket 不稱為實際投資者身分；大額比例公式與可用范围可追溯。
- 無支援 TPEX 樣本／契約時明確 unsupported，不擴稱全市場涵蓋。

## 驗證

margin units、TDCC variant／denominator、total/adjustment、缺週、跨 venue 和空資料；UI／AI 統一語意回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

upstream domain/models/{margin_short_sale.py,shareholder_distribution.py}、query API；MarketData.margin 與 research service。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不把大戶比例解讀為特定主力進出；不變更交易策略。

## 進度與交接

2026-10-07 implementation handoff，等待 parent review。依賴 PW-01／PW-04 已 completed；本次沿用 `codex/taiwan-market-support` checkout，沒有 commit、branch/worktree 變更或修改本卡上方的狀態／Owner。

實作完成的部分：新增 margin-short-sale 與 shareholder-distribution typed reads、coverage/status/evidence、TWMD 相容 `MarketData.margin` 路由與 `TW_MARGIN_PROVIDER`（預設 `twmd`、明確可選 `finmind`），以及研究 UI／AI 共用的兩個區塊。融資數量以整數 `trading_units` 保留在 typed observation/evidence，舊 `MarginItem` 數值欄位只做介面相容且 `total_balance` 留空。TDCC 分布將 level 12–15 明確標成 >400,000 股（下限 400,001），以官方總計列為分母；bulk level 16 adjustment 不進大額 numerator，complete buckets 與 official total 必須 reconciliation。最新日兩種 variant 同時存在時選 `bulk_current`；週變化只比同 variant 的精確前一週，缺週時 `changes=null`。TPEX TDCC 明確 unsupported；保管帳戶不描述為投資人身分。六個區塊依序排入最多四個共用讀取槽，保留既有 25 秒 deadline，未送出的區塊也有明確 timeout 狀態。

修改的工作範圍檔案：`.env.example`；`packages/marketdata/src/marketdata/{__init__.py,client.py,http.py,registry.py,types.py,vendors/twmd.py}`；`packages/marketdata/tests/{test_registry.py,test_twmd_margin_shareholders.py}`；`src/platform/{marketdata/marketdata_client.py,runtime/config.py}`；`src/modules/{assistant/tools.py,assistant/tool_descriptors.py,market/data_collector.py,research/taiwan_research.py,research/twmd_margin_shareholders.py}`；`frontend/packages/api/src/research.ts`；`frontend/packages/biz-ui/src/components/taiwan-research-panel.tsx`；`frontend/src/pages/DataSources.tsx`；`frontend/tests/TaiwanResearchPanel.test.tsx`；`tests/{test_assistant_tools.py,test_datasource_test_path.py,test_taiwan_integration.py,test_taiwan_research_service.py}`；`docs/plans/twmd-integration/contracts/README.md`；`docs/taiwan-market-support.md`。

驗證通過：`.venv/bin/python -m pytest -q tests packages/marketdata/tests`（1227 passed、3 skipped、14 warnings）；`cd frontend && node node_modules/vitest/vitest.mjs run`（21 files、55 passed）；`cd frontend && node node_modules/typescript/bin/tsc -b`；`cd frontend && node node_modules/vite/bin/vite.js build`；`git diff --check`。測試涵蓋整數單位與 Decimal 字串精度、unknown/missing/explicit-empty/error、coverage read failure 保留已選 rows、venue identity、current/historical TDCC row-level semantics、adjustment、完整與不完整分母、variant tie-break 與 exact-week change、四讀併發排程、timeout/cancel permit，以及 UI/AI 狀態摘要。

邊界：2026-10-07 的本地上游 smoke 記錄由 parent 保存於 [PW-07 live contract evidence](../evidence/PW-07-live-contract-2026-10-07.json)。兩個 venue 的樣本各有 3 筆 margin row；TDCC 查詢只取得 2026-09-04 一期，因此沒有前週變化可顯示。這是保留資料的有限觀察，不代表整段日期覆蓋、完整來源 SLA 或已部署服務。無 commit／PR；parent 負責獨立 review、必要修正、重跑驗證及 commit。

未觸碰原有 `src/modules/automation/agent_catalog.py` 修改，也保留既有 `.DS_Store`、`.agents/skills/archify*`、`.archify`、`skills-lock.json`、`uv.lock`；parent 新增的 live evidence 檔亦未修改。


Coordinator final checkpoint：**completed**，2026-10-07 Taipei。派工提交 `e158cf5`，worker gpt-6-luna／xhigh；主代理已逐項審查完整 diff、呼叫端與測試，並親自修正以下缺陷：

- 融資 freshness 原先未帶入已返回交易日，現保留逐期資料並正確計算 period age；來源列 receipt 不以 partition acquisition 代替。
- 部分 coverage 不再代表整段 EMPTY／MISSING；必須涵蓋所有請求分區且沒有 coverage error。TDCC 指定 variant 時只讀對應 dataset，coverage 失敗不抹去已取得 rows。
- 前週官方分母為零時保留各期與股數變化，占比／占比變化維持 null；官方 total 百分比必須為 100。
- 聚合期限到達後不再發出尚未開始的區塊；實際未結束 worker 持續持有讀取 permit。
- UI 的集保來源改為 TDCC，顯示 bulk adjustment 已納入官方分母；相容 margin evidence 的使用率保留精確字串，日期 clock 單次擷取。AI 說明明列集保支援範圍與帳戶／前週語意。

主代理最終驗證：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1234 passed、3 skipped、14 warnings**（17.68 秒）。
- `node node_modules/vitest/vitest.mjs run`（frontend）：**21 files／55 tests passed**。
- `node node_modules/typescript/bin/tsc -b`（frontend）：passed。
- `node node_modules/vite/bin/vite.js build`（frontend）：passed。
- `git diff --check`、`git diff --cached --check`：passed；新增檔案納入 staged diff 審查。

唯讀驗證：[三個固定範圍 GET](../evidence/PW-07-live-contract-2026-10-07.json)，2026-10-07 16:46 Taipei 全數 HTTP 200。另於 **17:03–17:04 Taipei** 使用修正後的實際共用服務查詢 [兩標的六區塊結果](../evidence/PW-07-shared-service-2026-10-07.json)，日範圍 10-02..06、營收 07..08，TDCC 為前 90 個 completed 日期。TWSE:2330 **12,956.19 ms**，融資 available、集保 available（報表 09-04、bulk_current、無前週／changes null）；TPEX:5347 **10,295.98 ms**，融資 available、集保 unsupported。既有估值／法人／營收仍保留各自 available／partial 狀態。服務未提供 deployed commit；本機上游 checkout 不能視為線上版本。

最終 Handoff：透過既有 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，未合併或部署。集保僅支援四位數 TWSE，bulk adjustment 不指派到特定 bucket；分布／比例不證明實際投資人身分或進出。空 range 缺 coverage 時為 unknown，沒有 publisher SLA、逐列 receipt 或 revision 時保留未知；90 日預設範圍不保證兩期資料。沒有採集／排程／control mutation／訂閱變更／策略變更或付費 LLM 驗證。既有 automation 與未追蹤個人檔案保留且排除於提交。依賴重新查核後，唯一預設下一項為 **PW-08 ready**；PW-11 仍需上游採集，PW-13／PW-14 依原依賴 waiting。本次不啟動下一卡。
