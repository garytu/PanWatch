# TWUX-04：歷史分 K 日期選擇

優先級：P2。狀態：in_progress。依賴：無；日期呈現沿用 TWUX-01。

## 問題與交付結果

休市日預設查今天，圖表顯示「尚無歷史分 K 資料」，但昨天已有完整資料。後端支援日期，前端只有 1 分／5 分切換。

交付結果：歷史分 K 可選日期與上一交易日；自動模式優先顯示最近有資料的已完成交易日，清楚標示實際日期與覆蓋狀態。

## 修改範圍

- `frontend/packages/api/src/klines.ts`：傳遞 `start_date`／`end_date`，保留既有 AbortSignal 呼叫相容性。
- `frontend/packages/biz-ui/src/components/taiwan-intraday-chart.tsx`：日期、上一交易日、自動模式及狀態文字。
- `src/modules/market/api/klines.py` 與交易日曆：重用既有日期範圍；僅在需要時補最小的已留存交易日解析能力。

## 實作步驟

1. 日期選擇使用 Asia/Taipei 的交易日期；單日請求明確傳同日 start_date／end_date，切換 1m／5m 保留日期。
2. 自動模式以有覆蓋證據的日曆找已完成候選交易日，優先接 twmd 既有 `/api/v1/bars/coverage` 做有界範圍查詢，再取得候選日 bars 核對可畫價格。PanWatch 補 typed read／服務封裝，不需要 twmd 新增日期索引 API。查找限制在既有 30 日範圍內，先核對各 timeframe 的列數上限，制定固定查詢次數／列數預算，達上限即停止並說明。
3. 休市日自動選到最近可用日後，顯示例如「10/09 休市，顯示 10/08 歷史 5 分 K」。已完成交易日與已有完整資料是兩種證據，不能只憑日曆宣稱 coverage_complete。
4. 提供日期選擇、上一交易日及回到最近可用日。明選日期後不偷偷跳到其他日期；上一交易日缺資料時顯示缺漏，不把更早日期當成所選日期。
5. 分別呈現休市、未成交、不完整、缺留存、來源不支援及日曆未知。保留 gap／coverage metadata，不補零價或連線填平缺棒。
6. 對 symbol／date／timeframe 切換取消舊請求並核對回應 identity；保持歷史資料與 live readiness／可交易狀態分開。

## 驗收條件

- 固定 2026-10-09 休市，fixtures 留存 10/08 的 54 根完整 5m：自動模式可顯示 10/08，日期與休市原因可見。
- 明選 10/09 不換成 10/08；無成交與缺漏訊息依實際證據區分。
- 驗證週末、連續假日、臨時休市、日曆年份缺失、前一交易日缺資料及 UTC／台北跨日。
- 1m／5m 都不因 limit 截斷而宣稱完整；無可用日的查找遵守範圍與請求預算。
- 快速切換標的、日期、timeframe 後不顯示晚到的舊資料；資料單位、原始價格與 coverage 不變。

## 待執行驗證與風險

- 擴充 `frontend/tests/TaiwanIntradayChart.test.tsx`、`frontend/tests/api/klines.test.ts`，補後端日期解析／查找預算測試及相關 twmd 分 K 回歸。
- 前端相關 Vitest／TypeScript／build、後端相關 pytest、`git diff --check`；唯讀核對 10/08 歷史分 K，無需等下一交易日才能驗收此歷史功能。
- 風險：執行中的 twmd 與本機 coverage 契約版本不同、日曆覆蓋未知及 API 列數上限。先核對 deployed 能力，保持有界查找，資訊不足就要求明確日期；不觸發上游補資料。

## Progress checkpoint

2026-10-10：開始歷史分 K 日期選擇。Owner：GPT-6 Luna/max implementation worker；coordinator：Codex。TWUX-01 已完成，TWUX-03 可獨立交付部分已提交，完整財報索引仍 blocked，不阻擋本卡。已核對既有 coverage 與 bars 能力，沒有新增上游 API／採集。

## Handoff

尚未交付。
