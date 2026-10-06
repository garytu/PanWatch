# PW-04：第一批個股研究頁與 AI

狀態：completed。依賴：PW-02、PW-03。Owner：pw04_research_ai_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

使用者在個股頁能看到官方估值、法人、公司資訊與月營收，AI 使用同一組資料回答。

## 範圍

- 在 research 模組建立可 reuse 的資料聚合服務及有界 API；各區塊可獨立成功／失敗。
- 在 frontend API package 增加型別與個股研究顯示，reuse 既有個股入口及 UI 元件。
- 接入現有 assistant fundamentals／capital_flow tools；營收／profile 依用途擴充工具，參數保持有限。
- 將同一份結構化資料帶入 TradingAgents 和個股研究 context，TW 路徑不再依 CN 財務工具取資料。
- 顯示資料日／月份、來源、單位與缺資料；未更新資料不標「今日」。

## 驗收條件

- 個股頁與 AI 對同一標的使用相同資料和 evidence；同碼不同 venue 不混用。
- 估值／法人／營收部分失敗仍可展示其他區塊；loading、unsupported、missing、error 有清楚狀態。
- 財報未接入時，AI 不能把月營收或 quote 假裝成完整三表。
- ETF 顯示可用研究資料，無月營收明確說明；停用市場規則與既有入口不回歸。
- API／AI 請求有範圍、timeout、cache 與併發上限；上游 token 不進 browser。

## 驗證

backend 聚合／API／assistant／TradingAgents 測試；frontend 日期、單位、空值與部分失敗測試；TypeScript、production build；股票與 ETF 的畫面檢查。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/research/、src/modules/assistant/tools.py、src/modules/automation/tradingagents/；frontend/packages/api/、frontend/packages/biz-ui/、frontend/src/pages/Stocks.tsx。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不改交易策略、風險權重或 allocations；研究資料不能授權交易。

## 進度與交接

Coordination checkpoint：completed。目前 checkout：`codex/taiwan-market-support`；派工提交 `00a2b61`。Worker：pw04_research_ai_worker（gpt-6-luna／xhigh），交件 review 後由主代理獨立審查與修正。最終成果透過 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，未合併或部署。

Progress：完成可重用台股研究聚合服務、受登入與市場啟用規則保護的有界 API；官方估值、法人股數、公司資料、月營收分別返回 data／status／reason／evidence。日期最多 366 日、月份最多 120 月；預設為前 30 個 completed Taipei 日期與前 12 個月份。整體期限 25 秒、transport 單次最多 20 秒且不 retry，最多 4 個聚合請求及 4 個實際上游讀取。256 項快取依服務、憑證雜湊、canonical ID、dataset、範圍隔離，回傳 defensive copies；catalog／估值／法人 TTL 5 分鐘、profile 6 小時、營收 30 分鐘，error 不快取。

既有個股 overview 增加正式研究面板，顯示日期／月份、原生單位、null、部分失敗、來源與 revision／receipt／capture／hash。Assistant 官方 fundamentals／capital-flow 路徑與專用台股工具使用同一服務，明確配置的 FinMind 路徑保留。TradingAgents 與個股研究 context 共用同一結構資料；完整台股三表明確 unavailable，月營收與 quote 不替代財報。

Coordinator review：修正 catalog 讀取未納入整體 wall deadline、逾時後實際讀取 permit 過早釋放、報價 venue 蓋過指定 canonical 身分、TradingAgents 快取研究 payload 未核對標的，以及 response headers 潛在憑證暴露。只公開 TWMD schema／coverage headers；工具 timeout override 使用 StrictInt，拒絕 bool／小數。補充逾時／實際併發、header whitelist、canonical 身分、不同服務／憑證／venue／範圍快取與三表 unavailable 回歸；前端已覆蓋同碼跨 venue 舊回應隔離。TPEx 非四碼估值 selector 預先返回 unsupported，保留其他可用區塊。

最終驗證（主代理修正後）：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1177 passed、3 skipped、14 warnings**（17.67 秒；warning 類型與基線相同）。
- `.venv/bin/python -m pytest -q packages/pan-agent-runtime/tests`：**42 passed**。
- `node node_modules/vitest/vitest.mjs run`（frontend）：**20 files、50 tests passed**；既有 React Router／act／duplicate key warnings 保留。
- `node node_modules/typescript/bin/tsc -b`（frontend）：passed。
- `node node_modules/vite/bin/vite.js build`（frontend）：passed；caniuse-lite 資料較舊提示保留。
- `git diff --check`、`git diff --cached --check`：passed。

唯讀 live smoke：2026-10-07 **06:43–06:44 Taipei**，服務 `http://127.0.0.1:8000`，來源 checkout 仍為 `3acd67ffd98bbcf1713f9484d8a7b77871db6ade`，服務未公開 deployed commit，兩者不可視為相同版本。Selectors：date `2026-10-02..2026-10-02`、revenue `2026-07..2026-08`，profile latest-only。一次 instruments catalog 及三個標的共 **12 個 GET 均 HTTP 200**；ETF 不送已知不相容的 valuation selector。

| 標的 | 聚合延遲 | Profile GET 延遲 | 結果 |
| --- | --- | --- | --- |
| TWSE:2330 | 10,617.885 ms | 9,811.203 ms | 估值／法人／公司資料 available；營收 partial，July missing／August present |
| TPEX:5347 | 12,048.117 ms | 11,918.787 ms | 估值／法人／公司資料 available；營收 partial，July missing／August present |
| TPEX:006201 | 12,061.499 ms | 11,951.859 ms | 法人 available（64,433 股）；估值 selector、profile、營收 unsupported |

其他 GET 延遲：catalog 676.628 ms；valuation／flow／revenue 20.378–365.150 ms。實際來源期間、receipt、revision 與不支援狀態保留於 payload；未知 publication time 仍為 null。這是固定小範圍唯讀觀察，不證明全市場覆蓋、未來延遲或盤中到達。

畫面檢查：Chrome 使用實際上述 API payload、正式 TaiwanResearchPanel 與專案 CSS 的隔離 preview，確認 TWSE 股票原值／股／TWD／推定千元、七月缺值、八月來源，以及 ETF 可用法人／unsupported 說明。保存股票與 ETF 截圖於本工作階段的 visualizations 目錄；臨時 preview 檔已移除，未啟動完整 server lifespan 或寫入使用者 DB。Loading／error／venue 競態另有離線 UI 測試；未執行付費 LLM 完整分析。

Handoff：驗收通過，唯一預設下一項 **PW-05 ready**；依賴已滿足的 PW-07／08／09／10／12 也標為 ready，但均未開始。PW-11 仍等待上游實際 benchmark 採集。未變更策略／風險／allocation，未執行 ingest／scheduler／control mutation／deployment。完整三表、freshness age 提示與更新 runbook 屬後續卡；實際服務延遲及 retained／修訂資料沒有 as-of 保證。既有 `agent_catalog.py` 與未追蹤工具／lock 檔保持原樣、排除於提交。
