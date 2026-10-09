# TWUX-03：財報可用期間與獨立載入

優先級：P2。狀態：blocked（PanWatch 部分已交付）。責任：PanWatch＋twmd。依賴：TWUX-02 的區塊選擇契約；最新留存期間需 twmd 提供索引契約。

## 問題與交付結果

預設選最近已結束季度，沒有確認上游已留存該期。預設 2026 Q3 查詢逾時，2024 Q4 可讀；逾時不能證明 2026 Q3 不存在。慢財報又拖住整包研究呈現。

交付結果：快研究區塊先顯示，財報獨立載入；有可靠可用期證據時，預設最新已留存報表，讓使用者清楚選擇期間。

## 修改範圍

- `frontend/packages/biz-ui/src/components/financial-statements-panel.tsx`、`taiwan-research-panel.tsx`：預設期別、期間選擇與獨立 loading／error。
- `frontend/packages/api/src/research.ts`、研究服務及路由：重用選擇性區塊查詢，傳遞可用期與證據。
- `packages/marketdata` 的 twmd 財報 typed reads：確認既有 availability／retained metadata；若不足，先列出上游契約需求。

## 已核對的上游缺口與分工

本機 twmd checkout 的財報 query 目前要求指定年度／季度；既有 coverage 是該期的結果，不能列出公司所有已留存期間。完成「最新已留存預設」需兩邊修改：

- **twmd**：提供唯讀、有界、按 canonical issuer／report scope 篩選的留存期別索引；包含年度／季度、可讀狀態、來源及 authority／revision 證據，區分留存報表與 latest discovery。不得以索引查詢觸發 MOPS 採集。回應上限／分頁、排序、未知 schema 與錯誤語意先共同確認。
- **PanWatch**：新增 typed read 與隔離快取，使用該索引決定預設／可選期間，將財報獨立載入；索引不可用時明示未知，保留手動選期。
- **聯合驗收**：用一致的契約 fixtures 驗證多期留存、後次 no-report discovery、修訂、缺少標的與來源不支援；接入前核對 deployed twmd 是否具有索引能力。舊服務缺少索引時功能降級，不破壞既有指定期別查詢。

另需 twmd 量測 `get_financial_statement_state → _financial_state_all` 的讀取成本。程式目前先讀取／驗證所有留存 bundle，再選 scope；這是待量測的瓶頸候選，尚未證明是實測 timeout 的原因。若確認，twmd 改善指定 scope 的讀取／索引並保留 integrity／authority 驗證，PanWatch 再以相同 selectors 核對結果及延遲。

## 實作步驟

1. 唯讀核對 twmd 財報契約：是否能按來源、venue、industry、statement 與標的取得「已留存且可查」期別；區分最新發現、整體資料集留存與指定公司 presence。
2. 若沒有足夠的可用期索引，留下具體上游需求：selectors、期別、支援範圍、presence／coverage、取得時間與查詢預算。不得假設新 endpoint 已存在，也不逐季大量探測或硬編碼 2024 Q4。
3. 先交付不依賴索引的載入改善：研究主請求排除財報，財報以獨立請求取得；重用 TWUX-02 排程與快取，不另開無限制 worker pool。
4. 索引契約確認後，支援「最新已留存」及明確年度／季度選擇，顯示來源與可用期證據。latest 模式才自動選擇；使用者明選期間不得靜默回退。
5. 索引未知時明示「可用期間待確認」，提供明確期間選擇；不把目前季度稱為最新可用。財報缺少、不支援與 provider timeout 分開呈現。
6. 保留原始財務事實、累計／比較期、單位、尺度、精度與修訂資訊；不由缺資料推導單季數值或新增比率。

## 驗收條件

- 控制財報讀取延遲，其他研究區塊完成後即可顯示，不等待財報完成或逾時。
- fixtures 有多個留存期時預設最新符合 selectors 的可用期；僅有歷史期時清楚標示歷史期間，不承諾最新發布。
- 2026 Q3 timeout 仍為 timeout；明選缺少的季度保持原選擇且給出原因，不改查 2024 Q4。
- 無索引、部分索引、資料集存在但公司缺少、修訂／cache 更新及快速切換季度都有明確行為。
- TPEX、ETF 與非支援產業保留 unsupported／not applicable，不做無效的期別探索。
- 切換標的與期間後，晚回應不覆蓋新選擇；原始數值與事實筆數不因拆開載入而改變。

## 待執行驗證與風險

- 擴充 `tests/test_taiwan_research_service.py`、`tests/test_taiwan_research_api.py`、`packages/marketdata/tests/test_twmd.py` 與 `frontend/tests/TaiwanResearchPanel.test.tsx`；新增財報期間選擇測試。
- 後端相關 pytest、前端相關 Vitest／TypeScript／build、`git diff --check`；唯讀對照已驗證的 TWSE:2330 2024 Q4，不把它固定成所有公司的預設。
- twmd 索引需新增 query／storage 契約測試，驗證無採集、查詢範圍有界、留存 authority 不受後次 no-report 覆寫。效能改善若必要，先記錄相同資料量／selectors 的基準，再比較指定期別讀取；不能只依整頁載入時間推論改善。
- 若上游索引仍不足，獨立載入可先交付，但「最新已留存預設」維持待驗收，整張卡不可標為 completed。記錄契約缺口及恢復條件，不把上游資料補齊當成 PanWatch 已完成。

## Progress checkpoint

2026-10-10：開始 PanWatch 可獨立交付的載入／選期改善。Owner：GPT-6 Luna/max implementation worker；coordinator：Codex。上游索引未提供，完整驗收仍等待。

2026-10-10：已完成可獨立交付步驟 2、3、5，交 coordinator 審查。主研究請求只選擇九個非財報區塊；財報另用既有 selective-block API、共用排程與快取讀取。兩邊有各自的載入、錯誤、重試、取消與標的／期間序號檢查。回應必須符合所選 canonical 標的及財年／季度才會呈現。索引未知時明示可用期間待確認；缺少、錯誤／逾時、unsupported 分開保留；使用者所選期別不自動回退。仍等待上游索引契約；最新已留存預設與整卡完整驗收維持 blocked，不得標 completed。

## Handoff

PanWatch 部分已由 coordinator 獨立複核並提交，未部署；整卡維持 blocked，等待上游期別索引。改動為 `taiwan-research-panel.tsx`、`financial-statements-panel.tsx`、其前端回歸測試，以及[上游期別索引需求草案](../contracts/TWUX-03-retained-period-index.md)。

主研究以九個非財報區塊單獨請求，刷新只處理主研究失敗區塊；選期與財報重試不重載主研究。財報載入、錯誤、重試、取消與主標的／期別序號隔離；只呈現 canonical 標的和財年／季度符合本次請求的回應。預設選擇的已結束季度只當查詢條件，介面明示可用期間待確認，不能推論它已留存或最新。missing／provider timeout／unsupported 保留各自結果，明選期別不回退。已知 TPEX 不送財報讀取；供應商回報證券類型不支援後，不再逐季請求，也不把舊期間的 unsupported evidence 假裝成新期別結果。財報來源事實、精度、尺度、單位與筆數呈現未改。

財報 typed client 的上游查核只支持明確 issuer／year／quarter 讀取，無留存期別索引。coordinator 於 2026-10-10 執行單次唯讀檢查：執行中 twmd OpenAPI 只有 `/api/v1/financial-statements` 且必填年度／季度；`TWSE:2330` 2024Q4 回傳 `available`、394/394 facts，耗時 12.266 秒，執行映像 digest `sha256:c6bfd31e9fc2d65f1abfa95dffab789e648e7a0a37d8292df3eeb99d77543795`。這只證明該指定期別可讀；2026Q3 timeout 不證明缺少，不能據此稱最新已留存。

驗證結果：

- `frontend` 固定 Node 24.14.0／pnpm 9.15.9：`pnpm exec vitest run tests/TaiwanResearchPanel.test.tsx tests/api/research.test.ts`，23 passed。
- `pnpm exec tsc -b` 通過；`pnpm build` 通過（只出現既有 Browserslist 資料過舊提示）。
- `.venv/bin/python -m pytest -q tests/test_taiwan_research_service.py tests/test_taiwan_research_api.py packages/marketdata/tests/test_twmd_financial_statements.py`，71 passed；`packages/marketdata/tests/test_twmd.py`，17 passed。
- `git diff --check` 通過。

整卡仍 blocked on upstream：twmd 需正式確認並部署有界、唯讀、按 canonical issuer／venue／產業／報表 scope／statement selectors 篩選的 retained-period index，提供 period presence、coverage completeness、讀取狀態及 authority／revision 證據，與 latest discovery 分離，且不得觸發採集。恢復條件、範圍與查詢預算已記錄於期別索引需求草案；待雙方確認契約、typed client/UI 接入和多期唯讀驗收後，才能驗收「最新已留存」預設並完成整卡。

2026-10-10 readiness：TWUX-02 已交付選擇性區塊契約，可開始步驟 2、3、5 的 PanWatch 獨立載入／明確選期與上游需求記錄。執行中的 twmd OpenAPI（HTTP 200、0.235 秒）財報路徑僅 /api/v1/financial-statements，沒有留存期別索引；最新已留存預設仍待外部契約與聯合驗收，不能將整卡標 completed。

2026-10-10 coordinator 複核：補強回應各層標的／年度／季度的一致性核對，新增錯標的、錯季度與欄位衝突回歸；測試日期固定為 2026-10-09 台北時間，驗證 2026Q3 timeout 不變成 missing 或回退期別；保留主研究 selective refresh 的跨 provider 隔離回歸。獨立驗證 `pnpm exec vitest run tests/TaiwanResearchPanel.test.tsx tests/api/research.test.ts`：27 passed；`pnpm exec tsc -b`、`pnpm build` 通過；`.venv/bin/python -m pytest -q tests/test_taiwan_integration.py tests/test_taiwan_research_service.py tests/test_taiwan_research_api.py packages/marketdata/tests/test_twmd_financial_statements.py packages/marketdata/tests/test_twmd.py`：126 passed；`git diff --check` 通過。
