# PW-12：除權息與減資事件標記

狀態：waiting。依賴：PW-01、PW-04。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

個股圖及研究報告能辨識公司行動造成的價格變化，回測明確標示未調整限制。

## 範圍

- 接 /ex-right-dividend-results 與 /capital-reduction-results。
- 以 canonical ID／事件日保留 realized adjustment 與來源價格欄位。
- 在圖表／研究 evidence 顯示事件標記與影響說明，對 raw daily bars 附限制提示。
- 回測輸出增加已知事件期間／coverage 說明；資料完全缺失時不宣稱沒有公司行動。
- 定義後續調整價或含息回測所缺資料，不在本卡偷偷改寫歷史。

## 驗收條件

- 原收盤、reference、combined adjustment 等欄位不誤當現金股利。
- 減資事件與除權息分開；相同日期／同碼不同 venue 不碰撞。
- 不以 realized result 推定公告日／付款日；缺事件保留未知。
- raw price 回測結果保留原算法並標示限制，新增標記不改持倉／現金。

## 驗證

事件映射、exact values、date／venue、缺資料、chart annotation與backtest result metadata；既有 cost／execution 回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

upstream query corporate-action routes；screenshot/chart collector、research、src/modules/strategy/backtest/。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不交付完整復權／股利再投資／減資持倉模型。完整公司行動與 total-return 回測需另立有資料契約的任務。

## 進度與交接

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
