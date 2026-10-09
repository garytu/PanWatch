# TWUX-05：熱門股榜保留行情來源資訊

優先級：P2。狀態：completed。依賴：TWUX-01 的共用行情標示。

## 問題與交付結果

provider 與 collector 已有 price_kind、trade_date、freshness，但熱門股 API 投影與前端型別將它們丟掉。休市日的榜單因此看不出是前一交易日排名。

交付結果：熱門股及重用同一資料的榜單保留來源資訊；同日 EOD 榜可顯示「10/08 收盤榜」，混合日期不偽裝成同日排名。

## 修改範圍

- `src/platform/marketdata/collectors/discovery_collector.py` 與 marketdata HotStock 模型：確認 metadata 原樣保留。
- `src/modules/market/api/discovery.py`：API 投影、快取及衍生榜單傳遞。
- `frontend/packages/api/src/discovery.ts`、熱門股／發現入口相關元件：型別與日期標示。

## 實作步驟

1. 追蹤成交額榜、漲幅榜與衍生／合併榜的完整路徑，列出會重新組裝列資料或快取的地方。
2. API 以向後相容的附加欄位保留 price_kind、trade_date、freshness 與既有可取得的 provenance；不補造來源缺少的欄位，舊資料缺值可讀。
3. 前端沿用 TWUX-01 的標示規則。同日且同為 EOD 才給單一「日期＋收盤榜」標題；混合資料日／價格種類逐列標示或給明確範圍摘要。
4. 合併、去重與排序時保留獲選那列的 metadata，不能把其中一列日期套給整榜；未知日期顯示未提供。
5. 保持既有排名、候選範圍、部分掃描及市場／證券種類限制；不因新增標示而額外逐股查報價。

## 驗收條件

- 2026-10-09 休市、資料均為 10/08 EOD 時，熱門榜明示 10/08 收盤榜；回應逐列保留日期、種類與時效。
- 混合日期、stale live、日期未知、空榜與舊快取 fixtures 不被標成統一的最新收盤榜。
- 成交額／漲幅合併榜、重複標的及衍生主題榜保留對應列 provenance，不改變原有排序與價格。
- TWSE／TPEX／ETF 維持 canonical identity；不同 provider 的欄位缺少不造成相容性錯誤。
- 不增加底層逐股查詢次數，也不擴大有限候選掃描。

## 待執行驗證與風險

- 擴充 `tests/test_taiwan_discovery.py`、`tests/test_discovery_routing.py`；新增 API 型別／榜單日期呈現與合併 metadata 回歸測試。
- 後端相關 pytest、前端相關 Vitest／TypeScript／build、`git diff --check`；唯讀核對熱門榜日期與 collector 原始 metadata 一致。
- 風險：舊快取缺 metadata、多來源或多日期混合。保留未知狀態，必要時更新快取版本；不可用新的產製時間當成行情日。

## Progress checkpoint

Owner：GPT-6 Luna/max implementation worker；coordinator：Codex。2026-10-10 完成獨立審查與唯讀核對。

2026-10-10：implementation worker 已完成程式修改並交由 coordinator 獨立複核。marketdata `HotStock`、PanWatch collector、熱門股票與板塊成分 API、快取、衍生主題成分摘要及 DiscoveryPanel 都保留逐列 `price_kind`、`trade_date`、`freshness`，並傳遞 provider、adjustment mode、change basis、units 與原始 availability／coverage。合併仍由漲幅榜列覆蓋重複 symbol，候選數、順序、排序、兩次熱門榜請求與台股 legacy 全目錄查詢範圍維持原行為。畫面僅在完整合併清單同日且全為 EOD 時顯示 MM/DD 收盤榜；其他列沿用 TWUX-01 共用行情格式，衍生主題逐成分保留來源資訊。只使用 fixture 驗證；唯讀服務核對待 coordinator 執行，未重建或部署。

## Handoff

2026-10-10：coordinator 已完成完整 diff／呼叫端審查、獨立驗證與有界唯讀核對，本卡 completed。

- marketdata HotStock／collector／熱門股 API／快取／衍生主題成分保留八類來源資訊：price_kind、trade_date、freshness、provider、adjustment_mode、change_basis、units、完整 availability／coverage。來源 receipt 保留原欄位，沒有轉成行情時間。新欄位皆相容附加，舊 provider／快取缺值保留未知。
- 完整合併清單同日且全為 EOD 才標示 MM/DD 收盤榜；每列仍顯示時效。混合、過期 live、未知日期逐列沿用 TWUX-01 格式。For You 去重仍由漲幅榜列覆蓋、評分／排序／各 20 檔兩次請求維持原行為；衍生主題保留實際獲選成分列。
- coordinator 補主題成分股彈窗、TWSE／TPEX ETF 點選 identity 與空榜測試，審查隱藏第七列日期不同時不能給統一標題、重複獲選列 metadata、舊快取與不增加查詢證據。共用 helper 修正未知欄名重複顯示，沒有另一套價格種類解釋。
- 獨立相關後端：`.venv/bin/python -m pytest -q tests/test_taiwan_discovery.py tests/test_discovery_routing.py packages/marketdata/tests/test_twmd.py`：49 passed。最終完整 `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：1465 passed、3 skipped、14 warnings（19.12 秒）。
- 最終前端 `pnpm exec vitest run`：27 files／113 tests passed（包含 DiscoveryPanel 11、market-context 7 項）；固定 Node 24.14.0／pnpm 9.15.9 的 `pnpm exec tsc -b`、`pnpm build` 及 `git diff --check` 通過。警告限既有 React Router／重複 assistant key、Browserslist 及後端相容性警告。
- 唯讀三標的核對：一筆 price-snapshots、2.211 秒，TWSE:2330／TPEX:5347／TPEX:006201 均為 2026-10-08 EOD；八類 metadata provider→collector→API 全數相等，三個衍生主題各成分與原列一致。來源 availability 保留 10/08 AVAILABLE 與 10/09 EMPTY／freshness current。詳見 [實測證據](../evidence/TWUX-05-read-only-smoke-2026-10-10.json)。
- 邊界：實測使用 process-local 三標的 catalog fixture 配合真實來源價格，再重用該 pool 驗證實際 collector/API 投影；不是全市場排名或已部署 PanWatch 驗收。正式 legacy 全目錄／有限選股候選範圍未改。沒有逐股報價、上游採集／訂閱／通知、DB 寫入或部署。
