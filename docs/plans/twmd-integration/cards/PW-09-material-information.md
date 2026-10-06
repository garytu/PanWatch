# PW-09：官方重大訊息與事件分析

狀態：waiting。依賴：PW-01、PW-04。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓個股事件頁與 AI 使用有來源的官方重大訊息，保留新聞作為不同證據。

## 範圍

- 接 /material-information 的 current／history，保留來源家族、事件 ID、日期時間與 coverage-through。
- reuse 現有事件／新聞時間線與分析入口；同一來源內依 authoritative identity 去重。
- AI 可按有界 issuer/date/source 查原文，保留原文連結與取得證據。
- current feed 不表示完整歷史；history 的 issuer/year acquisition 範圍與 query date filter 分開。

## 驗收條件

- current／history 不在無 mapping 時強行 dedup；修訂／重觀察不冒充新事件。
- 沒有捕捉資料不顯示「今日無重大訊息」；partial/truncated 明確。
- 僅限已支援 TWSE 範圍，TPEX 不假裝等價覆蓋。
- 上游文本當資料處理，不能指示 agent 執行其他操作；摘要能回到原文。
- 自動提醒預設不啟用；若做站內提醒，須有穩定事件去重與使用者明確開關。

## 驗證

current/history scope、空／missing／truncated、revision 去重、publication 時區、assistant 非可信內容處理；事件 UI 回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

上游 query material_information_contract；src/modules/research/、market events、assistant tools、既有 announcement eval。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不新增 Slack／email 通知或全市場公告爬取；新聞不被當成官方完整事件流。

## 進度與交接

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
