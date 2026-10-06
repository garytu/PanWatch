# PW-01：資料契約、能力與唯讀樣本

狀態：completed。依賴：—。Owner：pw01_contracts_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

建立後續接入共用的欄位／日期／來源規則，避免把「API 存在」當成「資料完整」。

## 範圍

- 比對 twmd 現行 query API 與對應 docs；記錄 checkout commit、已部署 API 的可觀察版本／能力，不能假設兩者一致。
- 為估值、法人、company profile、月營收建立有日期的正例、缺資料、unsupported 和失敗樣本；公司資料 timeout 重查一次並記錄延遲。
- 建立 sanitized 離線 fixtures 與 contract matrix：端點、selectors、日期限制、回傳型別、numeric/null、單位、presence／coverage、receipt／revision。
- 確定 research 各區塊的 status/reason 與 evidence 欄位，reuse 既有型別；此卡先交付契約和測試樣本，不建通用框架。
- 標出後續每卡的資料與上游前提；修訂現有台股文件中的「目前限制」，保留原歷史觀察。

## 驗收條件

- TWSE／TPEX 有獨立 canonical ID 樣本；active 股票／ETF 與權證清楚區分。
- 來源日期、資料期別、receipt、served_at、未知 publication time 分開。
- 當天法人／TPEx估值不可讀、空 schema、月資料部分缺口均有明確契約；HTTP error 不包裝成正常空資料。
- Fixtures 能離線讀取，無 token、帳號或私人設定。profile 未成功取得時不得把這部分標為驗收完成。

## 驗證

離線驗證 fixtures 格式／型別與重要不變量；日期上限使用固定 Taipei clock；唯讀 smoke check 記錄實際 HTTP 與延遲。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

packages/marketdata/src/marketdata/vendors/twmd.py；上游 src/twmd/query/api.py、docs/*-api.md；docs/taiwan-market-support.md。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不採集、不改排程、不改 provider routing；禁止直接讀取或寫入 twmd live SQLite。

## 進度與交接

Final checkpoint: **completed**, 2026-10-07 Taipei。Worker：gpt-6-luna／xhigh；主代理已獨立審查、修正並重新驗證。PW-01 的全部必要驗收通過；provider 接線與交易時段驗收由後續卡處理。

### 修改檔案

- `docs/plans/twmd-integration/contracts/README.md` — endpoint matrix, version boundary, identity/security-type rules, date and status/evidence semantics, PanWatch transport gap, and PW-02–PW-14 prerequisites.
- `packages/marketdata/tests/fixtures/twmd/captured/2026-10-06.json` — sanitized query-only HTTP captures, including catalog/security-type samples, flow/valuation/revenue samples, current-date/future-period 400s, readiness/OpenAPI, bounded company-profile timeouts, and later successful profile bodies.
- `packages/marketdata/tests/fixtures/twmd/synthetic/offline_edge_cases.json` — separately labelled, schema-shaped API examples and research envelopes for missing schema, unsupported profile types, retained profile after snapshot absence, selected issuer absence, partial month coverage, recognized empty, unknown, stale, unsupported preflight, and HTTP 503.
- `packages/marketdata/tests/test_twmd_contracts.py` — offline fixture format/type and contract-invariant checks.
- `docs/taiwan-market-support.md` — appended a dated observation, preserving the existing document contents and history.

### 證據與驗收

- Upstream source reviewed at clean checkout `3acd67ffd98bbcf1713f9484d8a7b77871db6ade`. Deployed `/openapi.json` returned title `Taiwan Market Data Query API`, version `0.1.0`; no deployed commit was exposed, so source/deployment identity is not assumed.
- Captured active identities include `TWSE:2330` and `TPEX:5347` equities, `TWSE:00878` and `TPEX:006201` ETFs, and `TPEX:700019` warrant. The TPEx ETF revenue read returned `qualification=unsupported_etf`.
- On 2026-10-06 Taipei, both venue flows had 2026-10-02 observations and 2026-10-05 `MISSING` coverage. TPEx valuation had an Oct 2 row and Oct 5 missing coverage; the TWSE valuation endpoint returned rows for both dates. TWSE/TPEX July–August revenue ranges had July missing and August present.
- Company-profile requests for both equities first timed out twice at the explicit 10-second bound. A later bounded read returned full HTTP 200 responses: TWSE:2330 in 15,518.640 ms and TPEX:5347 in 15,595.329 ms. Both were qualified and present. This meets the successful-capture requirement but does not establish a latency guarantee; PanWatch's current configured client default is 5 seconds.
- Captures distinguish `trade_date`, `data_month`, profile `report_date`, source receipt/acquisition, row observation, response `served_at`, and unknown publisher publication time. Captured JSON fields preserve decimal strings, integer/null values, units, and response headers where present.
- Research status/reason/evidence examples separately represent `available`, `partial`, `absent`, `empty`, `missing`, `stale`, `unsupported`, `error`, and `unknown`. A six-digit warrant valuation is represented as a research preflight exclusion; it is not attributed an API `unsupported` response. Direct API 400s remain errors.
- `TwmdClient.get` currently returns parsed JSON without HTTP status or response headers, while `market_get` returns `None` after exhausted failures. This remains a PW-02 transport prerequisite; no provider routing or generic framework was added here.

### 驗證與限制

- `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_contracts.py` — 7 passed.
- `.venv/bin/python -m pytest -q packages/marketdata/tests tests/test_marketdata_client.py` — 233 passed.
- `git diff --check` — passed. A separate whitespace scan covered the new untracked files; the pre-existing uncommitted `docs/taiwan-market-support.md` prefix matches `/private/tmp/pw01-support-before.md` byte-for-byte.
- Evidence is a dated observation from the local API; it does not establish a deployed build commit or future service availability. The profile route took over 15 seconds in successful reads. The synthetic unsupported profile examples follow upstream documentation; the live unsupported ETF example is the revenue endpoint. No frontend, upstream service, SQLite database, ingest, control, schedule, or subscription state was changed.
- Worker 未建立提交；主代理的指派提交為 `07b2d45`。最終任務提交以 `test(marketdata): 完成 PW-01 資料契約與唯讀樣本驗證` 為標題；交付至既有 [PR #1](https://github.com/garytu/PanWatch/pull/1)，不自動合併。

### 主代理獨立審查與最終驗證

- 比對完整 fixtures／diff、upstream API／validators、既有 Fundamentals／CapitalFlow 與宿主 collector，確認每個 venue 的值、日期、來源及 coverage 語意。主代理另取得兩個完整公司 profile 成功樣本，驗收不依賴合成 profile。
- 補上非零 foreign-dealer 合成列與兩個實測 venue 的總計不變量、null PE／零 PB、營收 null／零字串尺度／負百分比；補上 UTC 與 Taipei 日期不同的固定 clock、日範圍 366／367 天和月上限案例。
- 完整區分缺 schema、標的未出現、保留舊 profile、部分缺月、unsupported preflight、來源 empty／unknown 與 HTTP error；研究 evidence 在混合來源範圍保留每列 provenance，不以單一 receipt 代替所有日期。
- 補充 `bars-capabilities-2026-10-07.json`：2026-10-07 00:10:54 Taipei，唯讀 GET HTTP 200、16.522 ms，support=true／configured=false／collection=false。修訂現行 TPEx／分鐘採集限制，保留原 2026-09-29 歷史觀察。
- `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_contracts.py`：**11 passed**。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：最終 **1075 passed、3 skipped、14 warnings**（17.33 秒）。先前基線全量一次為 1063 passed、3 skipped、1 failed，失敗是既有 SSE tail 時序測試；單独重跑 `.venv/bin/python -m pytest -q tests/test_sse_endpoints.py` 為 **5 passed**，最終全量也通過，未改動該測試或 runtime。
- `git diff --check` 與最終 staged whitespace 檢查通過；新 JSON 可離線解析，所有 fixture 已掃描敏感 key。無前端變更，因此本卡未重跑前端測試／build。
- 未驗證部署 commit、future service availability、全市場完整度、歷史 as-of 或盤中到達；這些是後續卡邊界，不影響本卡的契約／樣本驗收。profile 的 15.5 秒實測與 HTTP headers/error transport gap 是 PW-02／PW-03 明列前提。
- PW-02／PW-03 更新為 ready；預設唯一下一項是 PW-02，未執行下一卡。原工作區其他修改／未追蹤工具檔保留；本任務提交包含既有總計劃所引用的任務卡與 execute-task 工作流程基線，供 PR 中的連結與狀態可查閱。
