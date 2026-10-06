# PW-02：官方估值與三大法人接入

狀態：ready。依賴：PW-01。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓既有 fundamentals／capital_flow 呼叫可以取得來源清楚的官方台股數據。

## 範圍

- 新增 twmd valuation／institutional-flow typed reads、vendor 註冊與 TW routing。
- 對應既有 PE、PB、殖利率及 institutional_shares 欄位；保留日期、來源、單位和 coverage／presence。
- 查詢明確使用 API 可接受的 completed-date window，不能一律查今天；未覆蓋日期保留 missing。
- 保留原生類別與來源證據；需要區间／歷史數據時不只回傳最新一筆。
- 官方／FinMind 路徑依明確設定選擇；既有非 TW 呼叫行為不變。

## 驗收條件

- TWSE:2330 與 TPEX:5347 的固定來源樣本對應正確。
- foreign dealer 子項不重複加總；所有現金流欄位保持 null，法人股數不轉成現金。
- 沒有完整五個交易日就不宣稱「5日總計」；不能拿五個日曆日冒充五個交易日。
- null PE／零值／absence／MISSING／error 可區分；來源日期進入宿主 collector 和 AI payload。
- legacy fundamentals 型別不能讓 receipt 被誤稱為財報期別；必要時增添相容的 evidence 欄位。

## 驗證

twmd vendor、registry、TW routing、capital-flow summary 和 assistant tool 回歸；測兩個 venue、重複分類、日期限制、部分覆蓋與 fallback policy。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

packages/marketdata/src/marketdata/{types.py,client.py,registry.py,vendors/twmd.py}；src/platform/marketdata/marketdata_client.py、collectors/capital_flow_collector.py。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不推算市值、EPS 或 ROE，不用 PE 反推盈利；不移除 FinMind 的其他資料用途。

## 進度與交接

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
