# PW-01：資料契約、能力與唯讀樣本

狀態：in_progress。依賴：—。Owner：pw01_contracts_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

建立後續接入共用的欄位／日期／來源規則，避免把「API 存在」當成「資料完整」。

## 範圍

- 比對 twmd 現行 query API 與對應 docs；記錄 checkout commit、已部署 API 的可觀察版本／能力，不能假設兩者一致。
- 為估值、法人、company profile、月營收建立有日期的正例、缺資料、unsupported 和失敗樣本；公司資料 timeout 重查一次並記錄延遲。
- 建立 sanitized 離線 fixtures 與 contract matrix：端點、selectors、日期限制、回傳型別、numeric/null、單位、presence／coverage、receipt／revision。
- 確定 research 各區塊的 status/reason 與 evidence 欄位，reuse 既有型別；此卡先交付契約和測試樣本，不建通用框架。
- 標出後續每卡的資料與上游前提；修訂現有台股文件中的「目前限制」，保留原歷史觀察。

## 驗收條件

- TWSE／TPEX 有獨立 canonical ID 樣本；active 股票／ETF 與權證清楚區分。
- 來源日期、資料期別、receipt、served_at、未知 publication time 分開。
- 當天法人／TPEx估值不可讀、空 schema、月資料部分缺口均有明確契約；HTTP error 不包裝成正常空資料。
- Fixtures 能離線讀取，無 token、帳號或私人設定。profile 未成功取得時不得把這部分標為驗收完成。

## 驗證

離線驗證 fixtures 格式／型別與重要不變量；日期上限使用固定 Taipei clock；唯讀 smoke check 記錄實際 HTTP 與延遲。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

packages/marketdata/src/marketdata/vendors/twmd.py；上游 src/twmd/query/api.py、docs/*-api.md；docs/taiwan-market-support.md。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不採集、不改排程、不改 provider routing；禁止直接讀取或寫入 twmd live SQLite。

## 進度與交接

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
