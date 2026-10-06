# PW-14：即時指數與完成分鐘 K 接入

狀態：waiting。依賴：PW-11；上游配置與交易時段驗收。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

在有能力與健康證據時提供即時大盤背景和完成的分鐘 K，保留現有交易安全門檻。

## 範圍

- 開始前重新核對 deployed bars／benchmark quote API 和上游 MD-10 或後續等價驗收記錄。
- 接 /benchmarks/{id}/quote、bars capabilities／coverage／collection health；區分 supported/configured/connected/accepted/fresh。
- 在既有前端 1m/5m 圖與首頁指數接 bounded polling，顯示 last received／資料日／來源；後端 reuse現有session。
- 完成的 1m 與 5m parent按 upstream status／authority顯示；missing/pending/no-trade留gap。
- 準備一般交易時段驗收步驟，包含 source time、expiry、disconnect/reconnect、session rollover與quote gate回歸。

## 驗收條件

- baseline live_collection=false／benchmark unconfigured不能通過啟用驗收。
- upstream capability flag和實際fresh observation一致才顯示live；ACK不等於收到行情。
- interval_start與來源interval_end不偏移一格；未完成5m不能宣稱finalized。
- 盤後停止或降級顯示；斷線／stale明確，不靠request time更新freshness。
- stock quote、EOD fallback、紙上交易 gate、原訂閱與非TW 行為保持相容。
- 留存真實交易時段證據；離線tests不能取代live runtime驗收。

## 驗證

時間固定的 capability／freshness／interval／partial5m／reconnect測試；現有 intraday/chart/quote gate回歸；交易時段有界端到端驗收。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/market/api/{klines.py,market.py,quotes.py}；frontend taiwan-intraday-chart、taiwan-feed-status；upstream docs/plan/cards/MD-10-deployment-acceptance.md。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不開第二個Shioaji登入、不提供forming candle／index minute bar／全市場stream；不新增分鐘策略、自动訂閱或放寬交易門檻。上游配置與部署需使用者另行授權。

## 進度與交接

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
