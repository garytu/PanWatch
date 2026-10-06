# PW-08：券商分點研究

狀態：ready。依賴：PW-01、PW-04。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓個股研究顯示分點買賣集中程度、原生單位和可用的成交均價。

## 範圍

- 使用 canonical /broker-flow/quantities、/coverage、/price-levels。
- 第一版提供單檔有界範圍、top 買／賣分點與已定義的集中度；UI／AI 共用 typed results。
- 保留 provider-local branch identity、2026-07-24 cutover、native lot/share quantity、來源 revisions。
- 價格明細只對已 materialized watchlist 標的展示；讀取不加入 watchlist 或 queue。

## 驗收條件

- Capital／TWSE 相同 branch code 不 join；lot 不硬乘固定數字後與 exact shares 混合。
- VWAP 顯示為該來源買／賣成交均價，不稱持倉成本。
- quantities 31 日與 coverage 366 日上限遵守；EMPTY／FAILED／MISSING／未 materialized 明確呈現。
- top-N 與集中度 denominator 可解釋，不以未完整來源推導整市場集中度。

## 驗證

cutover 前後、分點 code collision、native units、價格明細缺口、producer failure、範圍上限與排序；UI／AI evidence。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

上游 docs/broker_flow.md；packages/marketdata、research service、frontend 個股研究。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不自動修復 BSR producer 或新增全市場明細；分點資料不能識別交易者或證明未來方向。

## 進度與交接

2026-10-07 協調查核：所列任務依賴已 completed；ready 僅表示可開始，尚未指派或實作。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
