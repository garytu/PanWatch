# PW-07：融資融券與集保持股分布

狀態：waiting。依賴：PW-01、PW-04。Owner：unassigned。

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

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
