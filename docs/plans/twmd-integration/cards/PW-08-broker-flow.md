# PW-08：券商分點研究

狀態：completed。依賴：PW-01、PW-04。Owner：pw08_broker_flow_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓個股研究顯示分點買賣集中程度、原生單位和可用的成交均價。

## 範圍

- 使用 canonical /broker-flow/quantities、/coverage、/price-levels。
- 第一版提供單檔有界範圍、top 買／賣分點與已定義的集中度；UI／AI 共用 typed results。
- 保留 provider-local branch identity、2026-07-24 cutover、native lot/share quantity、來源 revisions。
- 價格明細只對已 materialized watchlist 標的展示；讀取不加入 watchlist 或 queue。

## 驗收條件

- Capital／TWSE 相同 branch code 不 join；lot 不硬乘固定數字後與 exact shares 混合。
- VWAP 顯示為該來源買／賣成交均價，不稱持倉成本。
- quantities 31 日與 coverage 366 日上限遵守；EMPTY／FAILED／MISSING／未 materialized 明確呈現。
- top-N 與集中度 denominator 可解釋，不以未完整來源推導整市場集中度。

## 驗證

cutover 前後、分點 code collision、native units、價格明細缺口、producer failure、範圍上限與排序；UI／AI evidence。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

上游 docs/broker_flow.md；packages/marketdata、research service、frontend 個股研究。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不自動修復 BSR producer 或新增全市場明細；分點資料不能識別交易者或證明未來方向。

## 進度與交接

2026-10-07 實作交接：目前狀態為 `review`，checkout `codex/taiwan-market-support`；本卡未建立提交。PW-01／PW-04 依賴與上游 contract 查核已由主代理確認。修改入口如下：

- `packages/marketdata/src/marketdata/{types.py,__init__.py,client.py,vendors/twmd.py}` 新增 typed broker quantity、coverage、price-level reads 與 source-preserving parser；相關離線測試在 `packages/marketdata/tests/test_twmd_broker_flow.py`。
- `src/modules/research/twmd_broker_flow.py` 新增 research adapter；`src/modules/research/taiwan_research.py` 將其納入既有有界 service 與共用 payload。回歸測試在 `tests/test_twmd_broker_flow_research.py`、`tests/test_taiwan_research_service.py`、`tests/test_assistant_tools.py`。
- `frontend/packages/api/src/research.ts` 加入 broker-flow 型別；`frontend/packages/biz-ui/src/components/{broker-flow-panel.tsx,taiwan-research-panel.tsx}` 顯示研究區塊；`frontend/tests/TaiwanResearchPanel.test.tsx` 覆蓋單位、來源分組、VWAP 與明細缺口。

驗收狀態與保留事項：

- **來源／型別／cutover：已實作並有離線測試。** 只接受四位數 TWSE selector；2026-07-24 切換日前後保留來源分界；保留 Capital 長度前綴 `source_branch_key`、TWSE key、來源 revisions、原生整數數量與 Decimal 字串價格／VWAP。
- **原生單位／排行：已實作，完整度仍待主代理審查。** provider、native unit、share precision 各自成組，不 join 同碼 Capital／TWSE 分點、不把 lots 換算成 shares。集中度分母只用該來源組已回傳的分點列並附分母定義；主代理指出尚須核對 coverage `record_count` 與回傳分點列，故不宣稱完整來源覆蓋下的集中度已驗收。
- **VWAP／明細：已實作並有 UI 測試。** VWAP 明示為來源買賣成交均價，不是持倉成本；明細限定單日。上游明細端點以 HTTP 409 表示未 materialize；空的 200 列表無法分辨 EMPTY 與 FAILED，故呈現為狀態未知而不推論為 EMPTY。
- **覆蓋／失敗：已實作並有離線測試。** 保留 AVAILABLE、EMPTY、FAILED、CLOSED、MISSING；數量讀取拒絕超過 31 日的區間，不截短；coverage 可請求至 366 日。仍待主代理確認 broker API 專用 date bounds：目前 service selector 最長 366 日，而主代理指出 adapter 還重用一般 `_query_bounds` 的日期下限／當日限制。
- **研究 payload／AI／UI：離線測試通過，實際 host 路由待審。** assistant 與 UI 讀取相同 TaiwanResearchService payload。主代理指出 host `DbConfigProvider` 尚無 `broker_flow` source route，需確認共用 service 實例的正式讀取路徑。
- **Revision 一致性：部分完成。** 數量／coverage、價格明細／數量的版本差異會被標示為 conflict 或 unknown；主代理指出 adapter 產生的 revision warnings 與 panel 目前讀取的 data 欄位需核對。

實際驗證：

- `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_broker_flow.py tests/test_twmd_broker_flow_research.py tests/test_taiwan_research_service.py tests/test_assistant_tools.py`：62 passed。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：1242 passed、3 skipped、14 warnings（測試套件及相依套件的既有 deprecation warnings）。
- `cd frontend && node node_modules/vitest/vitest.mjs run`：21 files、56 tests passed。
- `cd frontend && node node_modules/typescript/bin/tsc -b`：passed。
- `cd frontend && node node_modules/vite/bin/vite.js build`：passed；Browserslist 資料過舊提示。
- `git diff --check`：passed。

主代理另有唯讀 live contract 查核，記錄於 `docs/plans/twmd-integration/evidence/PW-08-live-contract-2026-10-07.json`：包含 TWSE:2330 source cutover／coverage 缺日及 TWSE:4164 已物化 quantities、coverage、price levels 範例。此為盤後契約查核，不代表交易時段驗收。剩餘處理由主代理 review/fix/verify；本卡未執行採集、DB 操作、watchlist／queue 變更、排程或部署，亦未改動總計劃狀態或其他任務卡。


### 主代理獨立審查、修正與最終驗收

2026-10-07 最終 checkpoint：**completed**。上面的 worker review 保留為交接歷史；以下為最終結果。Worker：gpt-6-luna／xhigh（依本卡固定值）；指派提交 `91507da`。主代理已逐項審查全部實作／測試、上游 source／query model、現有 API／assistant／TradingAgents／research context 呼叫端；交接所列四個待審事項全部修正，沒有未解決的重大契約衝突。

- **日期契約與路由**：typed broker reads 改用獨立 bounds，接受 2024-01-01 至 captured Taipei 當日；數量 31 日、coverage 366 日均為 inclusive。價格明細同樣允許當日、cutover 前 unsupported。共用研究 API 保留既有 completed-date 範圍，超過 31 日的研究範圍只將數量區塊標 unsupported，coverage 及單日 detail 繼續回報，不截短日期。宿主明確路由 `broker_flow` 至 twmd；非四位數 TWSE／TPEX 預先 unsupported、不發送 broker GET。
- **完整度／修訂**：核對逐日 coverage record_count 與實際 branch rows、provider/day revision。缺／失敗日期列入缺口，筆數或 revision 衝突不能標來源覆蓋完整，整個 broker block 為 partial；warnings 同時留在 data／evidence 並由 UI 顯示。價格明細與同日數量的 revision 衝突也為 partial。只在同 provider／unit／precision 分組，top 5 以買量／賣量各自排序、同值按 exact source key，分母為已回傳各側數量；零分母比例 null。
- **失敗／期限**：新增子端點 deadline 及 transient component error 不快取的回歸；部分端點失敗仍保留其他成功結果。保留原服務 25 秒 deadline、最多四個實際讀取，broker 的三筆 GET 在一個 read lease 內順序執行並依剩餘 deadline 限制 timeout。
- **精確資料與 UI／AI**：保留原生整數、來源 decimal strings、逐列 revisions，拒絕負 VWAP、格式錯誤與重複 branch/price 列。補齊 assistant 工具描述／keywords／capability，UI 用中文分母、FAILED／明細缺口及修訂限制；VWAP 為 TWD 來源買賣成交均價，日期由新到舊顯示有界列。未把 Capital lots 換成 TWSE exact shares，沒有新增持倉成本或交易者推論。

最終主代理驗證：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1258 passed、3 skipped、14 warnings**（18.56 秒）；既有 deprecation warning 類型保留。
- `.venv/bin/python -m pytest -q tests/test_twmd_broker_flow_research.py packages/marketdata/tests/test_twmd_broker_flow.py tests/test_marketdata_client.py tests/test_assistant_tools.py`：**55 passed**。
- frontend `node node_modules/vitest/vitest.mjs run`：**21 files／57 tests passed**。
- frontend `node node_modules/typescript/bin/tsc -b`、`node node_modules/vite/bin/vite.js build`：**passed**；既有 React Router／act／duplicate-key 及 Browserslist 提示保留。
- 新 UI 回歸找到原生 FAILED 未翻譯，已修正且完整重跑通過。一次從 repo root 執行 Vitest 未載入 frontend aliases；在規定 frontend 目錄執行後通過，無環境或相依變更。
- `git diff --check`／最終 staged whitespace 及 scope 檢查通過。任務提交題名 `feat(research): 新增可追溯的券商分點研究`，透過既有 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付；未合併或部署。

唯讀實測：17:10–17:13 Taipei 的固定 query contract 見 [live evidence](../evidence/PW-08-live-contract-2026-10-07.json)；17:28–17:29 Taipei 實際共用研究服務見 [shared-service evidence](../evidence/PW-08-shared-service-2026-10-07.json)。Selectors：2026-10-02 單日、營收 2026-07..08。TWSE:2330 aggregate 13.045 秒，816 股數分點列與 AVAILABLE coverage，detail not_materialized、block partial；TWSE:4164 aggregate 11.663 秒，98 股數分點列、185 detail rows、同 revision、block available；TPEX:5347 aggregate 12.447 秒，broker unsupported 而估值／法人／融資等區塊成功。Evidence 保留來源樣本、top groups、筆數、來源日期、覆蓋、revision 與未知 receipt／publication time。Source checkout 與部署 commit 不視為相同。

畫面查核：以正式 BrokerFlowPanel、專案 CSS 及上述實際共用 payload 建立臨時隔離 preview，於 Codex browser 檢查 TWSE:4164 的買賣排行／分母／精確 VWAP／價格列，以及 TWSE:2330 的 partial／未保留明細。版面與來源缺口均可讀；臨時 preview 檔、服務及分頁已清除，未啟動完整 server lifespan 或接觸使用者 DB。AI 共用 evidence 與既有 TradingAgents／context regression 由離線測試確認，未執行付費 LLM。

最終邊界：detail 空的 200 只能表示 EMPTY 或 FAILED closure，來源沒有公開 projection status，保留 unknown。分點觀察不能證明交易者身分、庫存成本或未來方向；集中度不代表全市場。沒有 publisher SLA、逐列 receipt 或歷史 as-of 保證；positive detail live sample 僅為現有已保留 TWSE:4164。沒有採集、watchlist／queue mutation、排程、control mutation、訂閱、策略、部署或 live SQLite 操作。既有 automation 修改及個人未追蹤檔案維持原樣，排除於提交。

依賴重新查核：唯一預設下一項 **PW-09 ready**；PW-10／PW-12 已 ready，PW-11 仍等待上游 benchmark 採集，PW-13／PW-14 保持 waiting。本次不啟動下一卡。
