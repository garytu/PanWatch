# PW-05：更新流程與第一批運行驗收

狀態：completed。依賴：PW-04。Owner：pw05_freshness_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓第一批功能有可信的更新責任、資料新鮮度與實際可用範圍。

## 範圍

- 為估值、法人、profile、月營收寫更新 runbook：venue、frequency、source release、retry、bounded backfill、success evidence。
- 用唯讀 query/control evidence 盤點實際 job／schedule／最新 coverage；記錄 manual-only 資料，不假設已每日更新。
- 在研究區塊展示個別 age／coverage，按資料頻率定義提示；沒有 publisher SLA 時保持保守。
- 以兩個 venue 的股票與 ETF 做第一批端到端驗收，追蹤公司資料延遲。
- 更新 docs/taiwan-market-support.md；整理採集／排程需上游處理的具體範圍及操作步驟。

## 驗收條件

- 缺 10-05 法人樣本不能以 10-02 數據冒充當日；非交易日與未採集分開描述。
- 月資料的 publication／report date、data month 和 receipt 分開；不因過幾日就宣稱缺失。
- UI／AI 皆展示資料有效範圍；超時區塊不拖垮整頁。
- 記錄真實服務版本、測試時間、選定 selectors、結果與限制。
- 第一批 backend 全套、frontend 測試／型別／build 與 diff check 有記錄。

## 驗證

固定時間 freshness 情境、部分失敗與 degraded 狀態回歸；兩個 venue 唯讀 smoke；完整第一批整合檢查。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

research service／frontend research 狀態、quotes taiwan status 既有證據；twmd control/query docs；docs/taiwan-market-support.md。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

這張卡不自動修改 twmd job／schedule 或部署。讀取／實作可完成；若持續更新需上游變更，交付 runbook 並明列運行前提尚未滿足。

## 進度與交接

2026-10-07 Coordination checkpoint：in_progress。目前 checkout：`codex/taiwan-market-support`；依賴 PW-04 在總計劃與卡片皆 completed。Worker：pw05_freshness_worker（gpt-6-luna／xhigh）。既有 `src/modules/automation/agent_catalog.py` 修改及未追蹤檔案排除於本任務。僅授權實作與唯讀 query/control 驗證；不執行採集、排程變更、訂閱變更或部署。上游持續更新前提須明列於 runbook 與 handoff。

2026-10-07 Worker implementation and verification checkpoint：Per-block freshness is added to the four shared research blocks with one aware UTC request clock; cached blocks are re-evaluated on each return. Data period, publisher report date/publication time, matching original receipt, first-observed time, upstream `served_at`, and current `evaluated_at_utc` remain distinct. Monthly age uses month-end; hints are frequency-specific and keep publisher SLA unknown. Profile snapshot receipt remains separate from retained issuer-row receipt. Coverage summarizes each requested scope without inferring trading sessions, closure, filing deadlines, or zero values. Tests cover latest daily rows, same-month day ordering, missing 2026-10-05 flow observations, retained-vs-newer-absent profile snapshots, month-end period age, UTC/Taipei cross-day bounds, cache re-aging, degraded/timeout blocks, and source `served_at` preservation.

The read-only authenticated control audit at 2026-10-07 00:14 UTC returned 200 for health/capabilities/jobs/schedules/runs: schema v2, 24 jobs, 11 schedules, 8 enabled; only the existing TWSE valuation schedule covered these research products. Query OpenAPI was 0.1.0; health/readiness returned 200 and ready daily-price coverage through 2026-10-06 at both venues. Individual bounded product GETs covered `TWSE:2330`, `TPEX:5347`, `TWSE:00878`, `TPEX:006201`, dates 2026-10-02..05, and revenue months 2026-07..08. The separate parent-run shared-service aggregate completed in 10.638–11.738 seconds per instrument. TWSE:2330 valuation had 10-02 and 10-05 rows; TPEX:5347 had 10-02 only; its 10-05 valuation was missing. Flows showed 10-02 available and 10-05 missing, with calendar-date `MISSING` coverage also for 10-03/04; no exchange-session conclusion was drawn. July revenue was missing; August was present for the two issuers, while ETF rows were unsupported/not in captured reports. Profile route calls were about 13.5 seconds; shared aggregate calls were about 10.6–11.7 seconds. Sanitized route-level evidence and projection correction are in `evidence/PW-05-live-smoke-2026-10-07.json` and `evidence/PW-05-live-smoke-corrections-2026-10-07.json`. The API exposes no deployed build commit.

Operational bounds, release/retry guidance, and success evidence are in [the PW-05 runbook](../runbooks/PW-05-refresh.md); `docs/taiwan-market-support.md` now records freshness semantics, first-batch coverage, manual-only jobs, and continuous-update prerequisites. Sanitized route evidence is in [the initial smoke](../evidence/PW-05-live-smoke-2026-10-07.json) and [its projection correction](../evidence/PW-05-live-smoke-corrections-2026-10-07.json). No ingest, schedule/control write, subscription change, deployment, or direct SQLite read was made.

Final worker verification: `.venv/bin/python -m pytest -q tests/test_taiwan_research_service.py tests/test_taiwan_research_api.py` — 25 passed; `node node_modules/vitest/vitest.mjs run tests/TaiwanResearchPanel.test.tsx` — 4 passed; `.venv/bin/python -m pytest -q tests packages/marketdata/tests` — 1,180 passed, 3 skipped, 14 warnings; `node node_modules/vitest/vitest.mjs run` — 20 files / 50 tests passed; `node node_modules/typescript/bin/tsc -b` passed; `node node_modules/vite/bin/vite.js build` passed; `git diff --check` passed. An earlier full-backend run concurrent with frontend tests hit timing-sensitive `tests/test_sse_endpoints.py::test_logs_sse_tail_only_new`; the isolated test and a subsequent full backend run passed.

Handoff: implementation is ready for parent independent review; card state and Owner remain unchanged, and no commit was created. Parent review has identified final wording/visibility follow-ups before acceptance: avoid trading-day phrasing without a calendar, keep primary UI summaries focused on periods/coverage instead of raw status/receipt/SLA labels, distinguish selected-row from whole-snapshot recency, and classify the assistant tool as static rather than near-real-time until a publisher SLA exists. The worker leaves `src/modules/automation/agent_catalog.py`, card state/Owner, and those parent-owned final review edits untouched. Continuous update remains unmet: several research adapters are manual-only or reject schedules; an upstream adapter and schedule review is needed before any ongoing cadence is claimed.

2026-10-07 Coordinator acceptance：completed。派工提交 `b191a33`；worker review 交件後，主代理檢查完整實作、相關 UI／AI／TradingAgents／context 呼叫端與上游 scope 契約。早期審查發現同月日資料排序會選錯列，worker 已修正並加入最新列／receipt 配對回歸。主代理再修正 research discovery 的 near-real-time 標籤為 static，主要 UI coverage 改為中文、未核對日曆的日期不稱交易日，區別所選範圍最新列與全域最新資料，明示日曆日及月末 age 基準。新增部分區塊逾時仍顯示其他資料的 UI 回歸，並確認 assistant 相容工具保留 freshness evidence。Runbook 補齊精確 control job IDs、有限 scope／retry action，修正 aggregate 延遲與 profile endpoint 延遲的區別。

主代理以真實 `TaiwanResearchService` 在 2026-10-07 08:16:07–08:16:52 Taipei 唯讀驗證四個指定標的，selectors 為 2026-10-02..05／2026-07..08；每個 aggregate 10.638–11.738 秒。安全精簡結果保存在 [shared-service evidence](../evidence/PW-05-shared-service-2026-10-07.json)。資料期別、對應 receipt、source served_at、每月 presence 與 ETF unsupported 均符合契約；未核對交易日曆、不宣稱每日更新、歷史 as-of 或盤中到達。

最終主代理驗證：`.venv/bin/python -m pytest -q tests/test_taiwan_research_service.py tests/test_taiwan_research_api.py tests/test_twmd_profile_revenue_blocks.py tests/test_assistant_tools.py` — **49 passed**；`.venv/bin/python -m pytest -q tests packages/marketdata/tests` — **1180 passed、3 skipped、14 warnings，16.89 秒**；frontend `node node_modules/vitest/vitest.mjs run` — **20 files／51 tests passed**；`node node_modules/typescript/bin/tsc -b` 與 `node node_modules/vite/bin/vite.js build` 通過；`git diff --check` 通過。既有 backend deprecation、frontend router／act／duplicate-key 和 Browserslist warnings 保留。最終 backend 未重現 worker 的 SSE 時序失敗；實作前基線為 1177 passed／3 skipped。

最終 Handoff：經主代理修正及獨立驗證後驗收通過，透過 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，未合併或部署。預設下一張 **PW-06 ready**，其他未滿足依賴的卡保持 waiting；本次不啟動下一卡。上游持續更新條件仍未滿足，具體範圍與回復步驟已交付 runbook，沒有執行採集／排程／control mutation／訂閱變更。既有 `agent_catalog.py` 和未追蹤工具／lock 檔保留且排除於提交。

最終範圍檢查：17 個本任務路徑；三份新 JSON 可解析且沒有憑證欄位或含憑證 URL。`git diff --cached --check` 通過，未暫存既有 automation 修改或其他未追蹤檔案。
