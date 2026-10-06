# PW-10：有限範圍台股財報

狀態：waiting。依賴：PW-03、PW-04。Owner：unassigned。

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

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
