# PW-10：有限範圍台股財報

狀態：completed。依賴：PW-03、PW-04。Owner：pw10_financial_statements_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓已支援上市公司使用原始財務事實進行三表研究，填補 TW 的 AI 財報缺口。

## 範圍

- 接 /financial-statements，明確選 issuer/year/quarter/consolidated/statement。
- 依 upstream admission 支援 TWSE industry 24 的 ordinary equities；保留 qualification 與 unsupported 原因。
- 保留 fact code、taxonomy、期間起訖、current/comparative、unit、原始值、revision 和來源報表。
- 接入個股研究及 TradingAgents 三表工具；首版優先展示原始事實，導出比率需有確定公式與輸入。
- 顯示 fetched report／最新 discovery status 分別的 evidence；truncated results 不作完整報表。

## 驗收條件

- TWSE:2330／2024Q4 fixture 及比較期不混合，累計收入／現金流不當單季值。
- unsupported TPEX／非 industry24／ETF 返回清楚狀態。
- EPS 與千元尺度正確；ROE／毛利率等缺輸入保持缺值。
- 先前 retained report 不因後次 no-report discovery 消失；無報表不能由 quote 猜出數字。
- TW 的 financial context 使用此 typed read；CN 原路徑不回歸。

## 驗證

financial qualification、taxonomy／period／unit、comparative facts、missing/no-report/truncated、AI三表render與非TW回歸；UI型別及展示。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

upstream docs/financial-statements-api.md；TradingAgents data_context.py、toolkit_adapter.py、agent.py；research service。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不擴充上游財報產業或市場，不提供假設性的全市場財務因子。

## 進度與交接

2026-10-07 worker implementation handoff; card status remains `in_progress` for parent review. PW-03/PW-04 dependencies and upstream financial-statements API/accepted contract were read. Work is in the shared `codex/taiwan-market-support` checkout. No commit or PR was created or updated.

Implementation adds typed bounded TWMD reads and strict envelope/fact decoding in `packages/marketdata/src/marketdata/financial_statements.py`, with explicit issuer/year/quarter/consolidated/statement/limit, Taipei completed-quarter validation, credential/base-url/scope-separated cache, exact values and lexical scale evidence, report/discovery separation, and partial truncation status. Research/API now exposes an independent financial block with a latest-completed-quarter default and explicit fiscal selectors. The stock research panel has year/quarter selectors and three source-fact tables; AI and TradingAgents share the typed block, and the three TradingAgents statement tools render their respective facts. CN paths remain covered by regression tests. Documentation and the upstream contract summary were updated.

The authentic captured query fixture is `packages/marketdata/tests/fixtures/twmd/captured/financial-statements-twse-2330-2024q4.json` with safe observation metadata beside it. Parent supplied its bounded GET observation: TWSE:2330 2024Q4, HTTP 200, 19,610.907 ms, 394 facts, qualified industry 24 and latest discovery present. Report raw SHA-256 is `1deba772079ed08cef1ccaf0430f40b4d36819ae5e5405073e793e6dc1932677`; profile qualification/source evidence has a separate hash and receipts. No further service requests were made. A separate parent attempt with the production 20-second bound timed out; the live latency is close to the app's per-read limit and remains an operational risk to review.

Validation on the final worker tree:

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests` — 1298 passed, 3 skipped, 14 existing warnings.
- `cd frontend && node node_modules/vitest/vitest.mjs run` — 24 files, 67 tests passed.
- `cd frontend && node node_modules/typescript/bin/tsc -b` — passed.
- `cd frontend && node node_modules/vite/bin/vite.js build` — passed.
- `git diff --check` — passed.

Parent's read-only review identified follow-up decoder cases to fix and retest: unknown concept taxonomy accepted as available; simultaneous decimals/precision; one-fact non-truncated all-statements response completeness; malformed comma grouping; exact decimals beyond ambient Decimal precision; and unsupported ETF qualification evidence. Parent will own those fixes and rerun the affected checks, then independently review and commit. Resume from this shared checkout; preserve the existing `src/modules/automation/agent_catalog.py` edit and unrelated untracked tooling files. No upstream ingest/control/SQLite reads, acquisition, schedules or deployment were run.


## 主代理最終審查與完成（2026-10-07）

Worker 使用 `gpt-6-luna`／`xhigh`，協調指派提交為 `20bec09`。主代理在同一 checkout 獨立檢查完整實作、呼叫端及上游 accepted contract，接手修正後完成驗收；沒有開始其他 implementation 卡。

主代理增加回歸測試，初次針對 decoder 的檢查暴露 11 項失敗，再修正 taxonomy admission、來源千分組／transform、decimals 與 precision 互斥、高精度 Decimal 正規化、必要本期非 nil 事實、完整筆數／連續 ordinal、資格與接收時間語意、unsupported ETF 證據及報表 URL 錯誤隔離。保留有效的較早比較期、nil／零值／dimensions，不把完整報表 admission 套用在被截斷前綴。

現行 catalog 遺漏或停用 canonical TWSE ordinary equity 時，留存財報仍依上游 frozen admission 透過同一有界 cache／deadline 讀取；其他研究區塊各自保留 unsupported。另修正 fiscal period age、unsupported selectors、移除未使用 helper，以及 Q4 報表內九個月 duration 的 YTD 標籤。TradingAgents 三表以真正 captured payload 回歸，缺報表不從 quote 或 CN abstract 補值，CN 路徑仍通過。

最後畫面查核使用正式財報元件及實際共用服務 payload 的暫時唯讀預覽，確認三表、比較期、精確值與來源尺度；過長概念造成欄寬溢出的問題已修正。暫時 preview、server 和 browser tab 已移除／關閉。此查核不是部署，也沒有啟動正式後端或改動資料庫。

最終實際驗證（主代理修正後）：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests` — **1318 passed, 3 skipped, 14 warnings**。
- `.venv/bin/python -m pytest -q packages/pan-agent-runtime/tests` — **42 passed**。
- `cd frontend && node node_modules/vitest/vitest.mjs run` — **24 files, 67 tests passed**，最後欄寬修正後再跑通過。
- `cd frontend && node node_modules/typescript/bin/tsc -b` — **passed**。
- `cd frontend && node node_modules/vite/bin/vite.js build` — **passed**。
- `git diff --check` 與 `git diff --cached --check` — **passed**。

正式 `TaiwanResearchService` 在原 25 秒 aggregate deadline 與不超過 20 秒單次 transport 下進行有界唯讀查核，2026-10-07T14:06:38.586662Z 返回 financial `available`、394/394 筆，aggregate 13,548.789 ms。其他區塊缺口獨立保留，月營收 partial、分點 partial（來源 scope／409），沒有被財報成功掩蓋。[安全服務查核證據](../evidence/PW-10-shared-service-2026-10-07.json)保留 selectors、逐讀取結果、report 和 discovery；已知 source checkout 為 `5f79d4452b823cf3187fc019c37be909676ce3bd`，實際 deployed commit 未驗證。

邊界：僅 2024 起 TWSE industry-24 ordinary equity 合併財報；TPEX／ETF／其他產業及個別財報不擴充。發布時間、amendment 狀態仍未知。曾有 20 秒讀取逾時，獨立 GET 19.61 秒與正式服務 13.55 秒的成功樣本不能建立未來延遲或全標的完整性保證。不採集、修改上游、排程、部署或合併 PR。既有 agent_catalog.py 與未追蹤工具檔保持原狀。

交付在目前 `codex/taiwan-market-support` 分支，以本卡最終 Conventional Commit 與 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付；最終提交可由本卡 git history 定位（避免提交自身 SHA 的循環更新）。PW-11 仍等待上游實際 benchmark 採集；PW-12 為唯一下一張 ready 卡，尚未執行。
