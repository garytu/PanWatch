# PW-09：官方重大訊息與事件分析

狀態：completed。依賴：PW-01、PW-04。Owner：pw09_material_information_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓個股事件頁與 AI 使用有來源的官方重大訊息，保留新聞作為不同證據。

## 範圍

- 接 /material-information 的 current／history，保留來源家族、事件 ID、日期時間與 coverage-through。
- reuse 現有事件／新聞時間線與分析入口；同一來源內依 authoritative identity 去重。
- AI 可按有界 issuer/date/source 查原文，保留原文連結與取得證據。
- current feed 不表示完整歷史；history 的 issuer/year acquisition 範圍與 query date filter 分開。

## 驗收條件

- current／history 不在無 mapping 時強行 dedup；修訂／重觀察不冒充新事件。
- 沒有捕捉資料不顯示「今日無重大訊息」；partial/truncated 明確。
- 僅限已支援 TWSE 範圍，TPEX 不假裝等價覆蓋。
- 上游文本當資料處理，不能指示 agent 執行其他操作；摘要能回到原文。
- 自動提醒預設不啟用；若做站內提醒，須有穩定事件去重與使用者明確開關。

## 驗證

current/history scope、空／missing／truncated、revision 去重、publication 時區、assistant 非可信內容處理；事件 UI 回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

上游 query material_information_contract；src/modules/research/、market events、assistant tools、既有 announcement eval。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不新增 Slack／email 通知或全市場公告爬取；新聞不被當成官方完整事件流。

## 進度與交接

2026-10-07 協調查核：所列任務依賴已 completed；ready 僅表示可開始，尚未指派或實作。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。

2026-10-07 指派 checkpoint：in_progress；worker pw09_material_information_worker（gpt-6-luna／xhigh），依卡片固定模型規則覆蓋技能一般 max 設定。目前 codex/taiwan-market-support checkout；PW-01／PW-04 在總表與卡片均 completed。既有 automation/agent_catalog.py 修改與 untracked 個人檔案保留且排除於提交；不建立或切換 worktree。

2026-10-07 worker handoff checkpoint：完成 PW-09 scoped implementation，尚待 parent independent review/final fixes；未提交、未改總表或狀態欄。新增 TWMD typed current/history reads、嚴格 selector/issuer/date/identity 驗證、research adapter/service/cache/API、唯讀 assistant 原文工具、announcement-eval TW 路徑、股票公告頁官方來源顯示，以及 sanitized fixture/backend/frontend 回歸測試。來源族群分開保留；current MISSING 不映射成無事件；history acquisition year/coverage-through/receipt 與日期篩選、公告時間分開；MOPS detail source reference 僅由有效 provider_key 重建並標成不可導覽；來源原文只進 structured data/分析 JSON，tool summary 由 host 生成。只支援四位數 TWSE EQUITY；TPEX/ETF 明確 unsupported。Parent 已提供的 live smoke evidence 覆蓋 AVAILABLE、EMPTY、截斷、current MISSING 與 TPEX/ETF preflight；worker 未改寫 parent evidence files。

驗證：`.venv/bin/python -m pytest -q tests packages/marketdata/tests` → 1272 passed、3 skipped；`node node_modules/vitest/vitest.mjs run`（frontend）→ 22 files/59 tests passed；`node node_modules/typescript/bin/tsc -b` → passed；`node node_modules/vite/bin/vite.js build` → passed；`git diff --check` → passed。Frontend build 顯示既有 caniuse-lite 資料過舊提示；pytest 顯示既有 Pydantic/py_mini_racer deprecation warnings。

Review handoff：parent 獨立發現 history `coverage_status=EMPTY` 且 `history_complete=true`、但 `latest_capture=null`、`acquisitions=[]`、`missing_dates=[]` 的無證據組合目前會被 parser 接受；parent 明確接手 parser completeness validation 及回歸測試。Parent 亦預計補強 UI 顯示 acquisition coverage/receipt。這些是尚未完成的最後審查修正，不要將此 checkpoint 當成 PW-09 最終完成狀態。恢復點為目前共享 checkout 的未提交 PW-09 scoped diff；保留既有 user files 和 parent evidence；不 cherry-pick 或 reset。


### 主代理最終審查與驗收（2026-10-07 Taipei）

Final checkpoint：**completed**。Worker：pw09_material_information_worker（gpt-6-luna／xhigh）；指派提交 `df1263c`。主代理已獨立閱讀完整實作／測試 diff、上游 API／material_information_contract 與 current／MOPS history domain／query service，並親自修正下列缺口後重新執行驗證。

- Provider：補強 positive 年度 bundle 的 details-complete／event count／coverage-through、最新 capture membership 與唯一 acquisition 身分檢查；按原始 generation／receipt 的保守截止推導逐日 missing_dates，跨年逐年核對，截止當日維持部分覆蓋。沒有採集、annual unverified_no_data 或不完整明細不能宣稱 AVAILABLE／EMPTY／完整歷史。修訂號是內容身分，不當成時間序。
- UI：官方公告元件共用既有個股公告入口，展示逐來源狀態、回傳／留存／截斷、採集年度、覆蓋截止、原始 receipt、事件及內容首次觀察與 capture ID；發布時間固定台北時區。來源內 authoritative ID 去重，以最新觀察時間選取內容（包含修訂號回退），current／history 不跨來源合併。保留全文以 React text／pre 展開；提供官方資料來源連結，MOPS POST selector 保留於 evidence，不冒充事件 permalink。其他市場公告卡片連結行為保留。
- AI：新增唯讀 `get_taiwan_material_information` 與原公告解讀 API 的 TW 路徑。單一工具來源、明確日期／issuer／limit；analysis cache 隔離來源／日期／模型／內容及修訂觀察。上游原文只作 structured tool data 或分析 JSON；可信 tool summary 僅含 host 生成的狀態／筆數。解讀 prompt 帶逐來源覆蓋、截斷、觀察時間，明示不可信原文與無歷史 as-of 保證；摘要保留原文與事件身分。API 意外失敗回傳無私人 provider 細節的 503。
- 有界讀取：日期 floor 2024-01-01，最多 366 日，允許 Taipei 今日；query limit 1–1000，AI tool 1–100（default 50），公告分析最多每來源讀 20 筆並分析最新 3 事件，clause／detail 各最多 6000 字並保留截斷提示與未截短原文。共用服務維持 25 秒 request deadline、最多四個 request／實際 reads、五分鐘快取且錯誤不快取。快取依 provider／credential／canonical ID／來源／日期／筆數隔離並回傳 defensive copies；逾時實際工作結束前不釋放 read permit。

修改範圍：`packages/marketdata` typed types/client/TwmdClient/exports 與 fixture/provider tests；`src/modules/research` adapter、bounded service/API 和 announcement-eval；assistant prompt/tools/descriptors；frontend API、原個股公告入口、timeline/display helper；backend／frontend 回歸；本卡、總表、contract matrix 和兩份唯讀 evidence。既有 `src/modules/automation/agent_catalog.py`、個人未追蹤檔案 checksum 保持原樣，排除於提交。

最終驗證（主代理修正後）：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1285 passed、3 skipped、14 warnings**，18.35 秒。
- `.venv/bin/python -m pytest -q packages/pan-agent-runtime/tests`：**42 passed**。
- `node node_modules/vitest/vitest.mjs run`（frontend）：**24 files／64 tests passed**。
- `node node_modules/typescript/bin/tsc -b`（frontend）：passed。
- `node node_modules/vite/bin/vite.js build`（frontend）：passed。
- `git diff --check` 與全 staged diff whitespace／範圍查核：passed。
- 回歸包含 positive／EMPTY／missing schema／annual unverified／partial cutoff／truncation／跨年缺證據、UTC 與 Taipei 跨日、row issuer／provider key／publication date、不同 scope 快取／defensive copies／error retry、deadline 後 permit、登入／停用市場／safe 503、未可信原文及修訂後重分析。實際 DOM 元件覆蓋來源文字不執行 markup、發布／取得／coverage clocks、來源失敗與成功並存；正式 stock modal 跨股票舊回應隔離通過。

唯讀驗收：[query contract evidence](../evidence/PW-09-live-contract-2026-10-07.json)（18:21:42 Taipei）與 [final shared-service evidence](../evidence/PW-09-shared-service-2026-10-07.json)（18:54:52–53 Taipei）。服務 `http://127.0.0.1:8000`，上游 source checkout `3acd67ffd98bbcf1713f9484d8a7b77871db6ade`；deployed commit 未公開，不視為同版本。

- TWSE:2608／history／2024 全年：GET 200、26 事件 AVAILABLE；一月所選窗口在完整 positive 年度 bundle 內為 EMPTY；同年度 limit=1 回傳一筆、保留 26 筆、truncated=true，shared block 為 partial。年度 acquisition 2024／coverage-through 2024-12-31 與原始 receipt 2026-10-04 分開。
- TWSE:2330／current／2026-10-01..07：GET 200、MISSING／零留存，但最新快照是 10-04；不判定今天或所選範圍沒有事件。TPEX:5347／TWSE:00878 原 API 為 400，新 service 預檢為 unsupported，未送事件讀取。
- 最終初次 catalog＋history service 約 1005.923 ms，其餘查詢／預檢 103.416–155.162 ms；固定樣本不代表 future SLA 或全市場覆蓋。保存 bounded 原文樣本與完整筆數，沒有 token 或請求憑證。

邊界：本產品僅涵蓋 catalog active 四位數 TWSE EQUITY；不宣稱 TPEX／ETF、全市場事件、完整 historical as-of 或發布 SLA。上游 current 無事件專屬 URL，history 原始 detail 使用 POST 且未提供可導覽 permalink；官方來源連結、保留全文與 authoritative selector／receipt 保留，可核對摘要，未捏造外部事件網頁。UI 沿用近 1–180 日選擇，完整有界歷史日期可透過 API／AI 工具查詢。官方事件為獨立來源服務／timeline，不把 current/history 合併到原七個非事件研究區塊。沒有付費 LLM 實際呼叫、原生瀏覽器畫面驗收、採集、全市場爬取、control mutation、通知／提醒啟用、排程、策略、live DB、部署或合併變更。既有 backend deprecation、React Router／act／duplicate-key、Browserslist warnings 保留。

交付：唯一最終任務提交標題 `feat(research): 接入官方重大訊息與原文分析`；透過既有 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，不自動合併。依賴重新查核後唯一預設下一項 **PW-10 ready**（PW-03／PW-04 均 completed）；PW-12 仍 ready、PW-11／PW-13／PW-14 仍 waiting。本次不啟動下一卡。
