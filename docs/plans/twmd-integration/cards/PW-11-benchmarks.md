# PW-11：官方大盤基準與相對表現

狀態：waiting。依賴：PW-01、PW-04；上游 benchmark 日線實際採集。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

台股首頁顯示加權／櫃買基準，個股研究能比較相同交易日期的價格表現。

## 範圍

- 接 /benchmarks definitions 與 /benchmarks/{id}/bars；benchmark identity 和 stock identity 分開。
- 依股票 venue 選 TAIEX／TPEX，支援首頁指數與每日 spark。
- 計算有明確起訖／共同觀察日期的 raw price return 及相對表現；缺 benchmark 時獨立 unavailable。
- 產出有界上游採集 runbook：TAIEX 月資料、TPEX latest-only 留存；取得授權及資料後再作 live smoke。
- 記錄產品最小可用範圍和 upstream missing，而非等待虛構完整 TPEX 歷史。

## 驗收條件

- index_points 不當成 TWD／可交易股票；沒有 volume/turnover 不生成它們。
- 零筆 benchmark bars 不能生成假的線或報酬；個股圖仍能使用。
- benchmark 缺日不補假價格；以已驗證交易日／共同日期處理，不能因週末 coverage MISSING 就斷言採集故障。
- 以實際取得的日線資料驗證首頁、spark、venue mapping與共同日期比較。
- 相對表現標 raw price basis，含息報酬不混入。

## 驗證

market indices、spark cache、benchmark units／venue、partial/gaps／不相交日期、無資料 fallback；UI及實際 bounded smoke。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/market/api/market.py、kline_collector.get_index_klines、MarketData index methods；upstream docs/benchmarks-api.md。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

計劃基線兩個指數皆零筆；程式契約可先做，卡的運行驗收要等資料實際取得。不自動部署／建立 schedule；即時 quote 留 PW-14。

## 進度與交接

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
