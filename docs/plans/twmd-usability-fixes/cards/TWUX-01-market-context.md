# TWUX-01：行情日期、休市背景與 AI 上下文

優先級：P1。狀態：completed。依賴：無。

## 問題與交付結果

詳情「問 AI」固定使用「即時行情」，忽略報價的資料日與時效。休市日產生的日報又可能把昨日行情稱為今日，並把週末稱為「明日關注」。

交付結果：詳情、助手上下文與盤後報告明確區分產製日、行情日、價格種類、時效及可交易狀態；研究報告可以在休市日產生，但不暗示當日開盤。

## 修改範圍

- `frontend/packages/biz-ui/src/components/stock-insight-modal.tsx`：行情摘要、`buildPageContext` 與問 AI 入口。
- `src/modules/automation/daily_report.py`、`prompts/daily_report.txt`：報告背景及下一交易日用語。
- `src/platform/scheduling/trading_calendar.py` 與既有市場狀態／報價型別：核對可用日曆能力，補足必要的共用日期與標示 helper。
- 盤中逐股與批次報告呼叫端：核對背景傳遞，不一律加交易時段禁用條件。

## 實作步驟

1. 盤點報價至詳情、助手、日報的 metadata 傳遞；以 `trade_date`、`price_kind`、`freshness`、`usable_for_trading` 及來源觀察時間為依據，缺值保留未知。
2. 定義共用標示規則：官方 EOD 標「日期＋收盤行情」；stale live 標「日期／時間＋過期盤中報價」；日期未知不使用「今日／即時」。同份背景包含市場時區及今天的日曆狀態。
3. 詳情畫面與「問 AI」傳送相同背景。API 可交易狀態為 false 時，文字不得推論為可交易；標示過期不覆寫來源欄位。
4. 日報將產製日與每個標的行情日分開。提示詞依背景描述「截至某交易日」，下一交易日僅由有覆蓋證據的日曆產生；跨年／日曆未知時使用「下次開盤待確認」。
5. 核對日報讀者畫面的標題與正文日期是否一致；保留休市研究排程，不修改既有報告或重跑通知。

## 驗收條件

- 固定 2026-10-09 休市、報價日 10/08：畫面與 AI 上下文都包含日期、種類、stale／不可交易狀態，沒有無條件的「即時行情」。
- 相同日期的 EOD 與 stale live 有不同標示；`timestamp=null` 不由產製時間補成盤中時間。
- 日報背景明示休市及資料截至日；「下一交易日」來自日曆，不直接把產製日加一天。
- 驗證正常開盤、盤後、週末、跨年、未知日曆及不同標的資料日不一致；不把研究資料的期別套成行情日。
- 模型輸入與報告用語的回歸測試通過；模型自由文字不作為唯一日期正確性的判準。

## 待執行驗證與風險

- 新增行情背景、日期標示與問 AI 上下文測試；補入 `tests/test_daily_report_index.py` 及相關日曆測試。
- 前端相關 Vitest、TypeScript／build，後端相關 pytest，`git diff --check`；唯讀核對 TWSE:2330、TPEX:5347、TPEX:006201 的不同價格種類。
- 風險：日曆年份覆蓋不足、metadata 缺少、報告多標的日期不同。上述情況明示未知或逐標的日期，不用提示詞猜測補齊。
- 完成後提供 TWUX-04、05 可重用的標示規則與實際欄位契約。

## Progress checkpoint

2026-10-10：完成詳情行情／問 AI 共用標示、日報日期背景與有界下一交易日日曆 helper。詳情與 AI 共用 `market-context` 輸出，來源 `timestamp=null` 保持未知，API 的 `usable_for_trading=false` 明示不可交易；日報以 Asia/Taipei 產製時間另列每檔行情日期、價格種類、時效及來源觀察時間。下一交易日只在 TWSE／A 股日曆覆蓋內提供，跨年或未知時顯示待確認。Owner：implementation worker；coordinator：Codex。

## Handoff

已完成實作與 coordinator 獨立複核：

- `frontend/packages/biz-ui/src/components/stock-insight-modal.tsx`、`frontend/packages/biz-ui/src/lib/market-context.ts`：詳情行情與「問 AI」使用同一行情／日曆上下文；保留 EOD、過期 live、來源觀察時間與可交易狀態。狀態來自既有 `/stocks/markets/status` API；盤中更新仍可執行。
- `frontend/packages/api/src/dashboard.ts`：補上市場時區與日曆回應型別。
- `src/platform/scheduling/trading_calendar.py`：新增只在明確日曆覆蓋內回傳的下一交易日和市場日曆背景；不改交易排程守衛。
- `src/modules/automation/daily_report.py`、`prompts/daily_report.txt`：分開報告產製日與每檔行情日期、價格種類、時效、可交易狀態、來源觀察時間；指數缺日期／種類時保留未知；下一交易日未知時提示待確認。新建議用「下次開盤關注」，並相容舊模型的「明日關注」輸入。
- 回歸測試：`tests/test_daily_report_index.py`、`tests/test_trading_calendar.py`、`frontend/tests/research/market-context.test.ts`。

驗證結果：

- `.venv/bin/python -m pytest -q tests/test_trading_calendar.py tests/test_daily_report_index.py`：28 passed。
- `.venv/bin/python -m pytest -q tests/test_taiwan_integration.py tests/test_taiwan_research_api.py tests/test_taiwan_research_service.py tests/test_taiwan_discovery.py tests/test_discovery_routing.py packages/marketdata/tests/test_twmd.py`：115 passed。
- 固定 Node 24.14.0／pnpm 9.15.9：`pnpm exec vitest run tests/research/market-context.test.ts`：4 passed；`pnpm exec tsc -b` 通過；`pnpm build` 通過。
- `git diff --check` 通過。

唯讀欄位核對（coordinator，2026-10-10）：報價請求 HTTP 200、1.433 秒；TWSE:2330 回傳 stale live、trade_date `2026-10-08`、來源觀察時間 `2026-10-08T13:30:00+08:00`、freshness 標示不可交易；TPEX:5347 與 TPEX:006201 使用 EOD fallback、trade_date `2026-10-08`、來源觀察時間為 null。查詢 image digest 為 `sha256:c6bfd31e9fc2d65f1abfa95dffab789e648e7a0a37d8292df3eeb99d77543795`。執行中的 PanWatch image 仍是舊版 `sha256:d4223a4a4b831d74d412228afc6e0abb0934582e39393ca77f0a22bf0f7dfa8f`，因此這是欄位契約核對，不是本次程式碼的部署驗收；本次未重建或部署。


Coordinator 最終複核（2026-10-10）：已修正 analysis_date 以台北日期正規化，補 UTC 跨日儲存／模型輸入一致性測試；日曆 fixture 改為 monkeypatch 復原。另為詳情報價及市場狀態加入序號，忽略切換標的／關閉後晚到回應；新增實際 modal 的「問 AI」背景與晚回應測試。獨立執行指定日曆／日報加跨模組 pytest：144 passed；前端 market-context 與 StockInsightMaterialInformation：9 passed；固定 Node 24.14.0／pnpm 9.15.9 的 tsc -b、build 通過。

共用呈現契約：buildQuoteMarketContext 使用 trade_date、price_kind、freshness.status、timestamp、availability、usable_for_trading；market status 使用 calendar.status/date/is_trading_day 及 timezone。EOD 為收盤行情，stale live 為過期盤中報價；缺日期／時間／時區或交易可用性明示未知，不使用生成時間補值。TWUX-04、05 可重用此規則。新成果未部署；既有使用者修改保留。
