# TWUX-02：研究讀取排程與區塊重試

優先級：P1。狀態：ready。依賴：無；提供 TWUX-03 的區塊選擇契約。

## 問題與交付結果

研究、公告與公司行動共用四個讀取額度；額度忙碌時直接回 `concurrency_limit`，正常開啟詳情也會失敗。重新載入又清空成功結果。

交付結果：正常重疊請求可在既有總期限內公平等待；同一查詢只執行一次底層讀取。個別失敗可重試，成功區塊不因重試消失。

## 修改範圍

- `src/modules/research/taiwan_research.py`：共用 request／read 限制、讀取 lease、快取與 builders 排程。
- 研究 API 路由及 `frontend/packages/api/src/research.ts`：有界、向後相容的區塊選擇參數。
- `frontend/packages/biz-ui/src/components/taiwan-research-panel.tsx`：保留結果、重試與取消；核對共用排程的公告／公司行動呼叫端。

## 實作步驟

1. 以可控制的慢讀 fixtures 重現研究與公告同時載入，分別量測 request 額度、read 額度及排隊時間。
2. 取代「read 額度忙即拒絕」：建立共用有界等待排程，維持四個實際 worker 與既有總期限。按呼叫者公平分配，限制等候工作量；真正過載仍回明確原因，不無限排隊。
3. 對相同 provider／認證範圍／canonical ID／區塊／selectors 的 in-flight 讀取合併。單一等待者逾時或取消不得取消其他等待者，也不跨 token 共用結果。
4. 保留 lease 安全：已啟動的慢 worker 即使呼叫者逾時，也要等底層讀取結束才釋放 permit；取消尚未執行的工作不得洩漏或重複釋放。
5. API 增加經 allowlist 驗證的選擇性區塊讀取；未傳參數維持原有整包契約。回應明確指出本次請求的區塊，避免將未請求區塊誤判為 missing。
6. 前端按 provider、標的及區塊 selectors 合併結果；刷新時保留相容的成功資料並標示更新中／舊結果，只重試失敗區塊。切換標的、期間或來源時不沿用不相容結果。

## 驗收條件

- 單次詳情的研究／公告重疊讀取及兩個詳情請求，當 fixture 可於期限內完成時，不因瞬間 read 額度占滿而出現 `concurrency_limit`。
- 實際底層並行數不超過四；排隊容量、request admission 與 deadline 都有上限，排隊中的某呼叫者不長期飢餓。
- 相同 cache key 的底層讀取次數為一；不同 provider／認證／venue／期間不合併。
- 排隊取消、執行中逾時、例外及最後一位等待者離開後無 permit 洩漏；逾時仍保留成功區塊。
- 重試只讀指定區塊；成功區塊仍可見，舊標的或舊期間的晚回應不覆蓋新資料。
- 舊 API 呼叫方式與區塊 status／reason／evidence 語意相容。

## 待執行驗證與風險

- 擴充 `tests/test_taiwan_research_service.py`、`tests/test_taiwan_research_api.py`、`frontend/tests/TaiwanResearchPanel.test.tsx`，使用事件／屏障控制時序，不以不穩定的短 sleep 判定並行。
- 後端相關 pytest、前端相關 Vitest／TypeScript／build、`git diff --check`；唯讀實測冷載入與重疊載入，記錄每區塊狀態及底層查詢次數。
- 風險：底層 HTTP 無法立即取消、極慢讀取耗盡容量。改善排程不保證上游必定及時回覆；仍需清楚呈現 timeout／過載與可重試狀態。
- 完成後記錄 queue 上限、deadline 與 selective-block 契約，供 TWUX-03 重用。

## Progress checkpoint

2026-10-10：使用者已授權依序執行，等待前卡交付／依賴確認。

## Handoff

尚未交付。
