# PW-05：更新流程與第一批運行驗收

狀態：ready。依賴：PW-04。Owner：unassigned。

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

2026-10-07 協調查核：所列任務依賴已 completed；ready 僅表示可開始，尚未指派或實作。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
