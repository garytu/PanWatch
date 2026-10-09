# TWUX-05：熱門股榜保留行情來源資訊

優先級：P2。狀態：planned。依賴：TWUX-01 的共用行情標示。

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

2026-10-10：使用者已授權依序執行，等待前卡交付／依賴確認。

## Handoff

尚未交付。
