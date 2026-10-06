# PW-04：第一批個股研究頁與 AI

狀態：ready。依賴：PW-02、PW-03。Owner：unassigned。

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

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
