# PW-02：官方估值與三大法人接入

狀態：completed。依賴：PW-01。Owner：pw02_valuation_flows_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓既有 fundamentals／capital_flow 呼叫可以取得來源清楚的官方台股數據。

## 範圍

- 新增 twmd valuation／institutional-flow typed reads、vendor 註冊與 TW routing。
- 對應既有 PE、PB、殖利率及 institutional_shares 欄位；保留日期、來源、單位和 coverage／presence。
- 查詢明確使用 API 可接受的 completed-date window，不能一律查今天；未覆蓋日期保留 missing。
- 保留原生類別與來源證據；需要區间／歷史數據時不只回傳最新一筆。
- 官方／FinMind 路徑依明確設定選擇；既有非 TW 呼叫行為不變。

## 驗收條件

- TWSE:2330 與 TPEX:5347 的固定來源樣本對應正確。
- foreign dealer 子項不重複加總；所有現金流欄位保持 null，法人股數不轉成現金。
- 沒有完整五個交易日就不宣稱「5日總計」；不能拿五個日曆日冒充五個交易日。
- null PE／零值／absence／MISSING／error 可區分；來源日期進入宿主 collector 和 AI payload。
- legacy fundamentals 型別不能讓 receipt 被誤稱為財報期別；必要時增添相容的 evidence 欄位。

## 驗證

twmd vendor、registry、TW routing、capital-flow summary 和 assistant tool 回歸；測兩個 venue、重複分類、日期限制、部分覆蓋與 fallback policy。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

packages/marketdata/src/marketdata/{types.py,client.py,registry.py,vendors/twmd.py}；src/platform/marketdata/marketdata_client.py、collectors/capital_flow_collector.py。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不推算市值、EPS 或 ROE，不用 PE 反推盈利；不移除 FinMind 的其他資料用途。

## 進度與交接

Final checkpoint：**completed**，2026-10-07 Taipei。Worker：gpt-6-luna／xhigh；主代理已獨立審查、修正並重新驗證。本次修改涵蓋 `packages/marketdata` 的 TWMD valuation／institutional-flow typed reads、型別、HTTP response/error 輔助、registry、provider-aware cache key 與 TW 日期範圍；host 端 provider 設定與明確 TWMD／FinMind routing、capital-flow evidence 傳遞及 TW outer-cache bypass；`.env.example`、`docs/taiwan-market-support.md` 和對應 package／host／assistant 測試。`src/modules/automation/agent_catalog.py` 與其他既有工作區變更均保留。

驗收對照：TWSE:2330、TPEX:5347 fixture、明確 provider 選擇與無靜默 fallback、canonical instrument IDs、原始估值字串和日期／coverage／presence／headers／provenance evidence 已覆蓋；generic PE 不填 TTM／static 欄位且 receipt 不填 report date；法人 foreign ex-dealer/non-dealer、trust、dealer 和 total 保留 native components 並避免 foreign dealer 重複加總，所有現金欄位與未證實的 5 日股數為 null；FinMind 缺類別亦保留 null；host collector 與 assistant payload 傳遞來源列和日期。完成日曆日 coverage、缺資料與 HTTP error 分流、TPEX 產品日期限制及多筆歷史列有離線測試。TWSE valuation 的空陣列仍是 unknown，符合上游無 coverage headers 的合約。

Worker 驗證命令與結果：

- `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_research.py tests/test_taiwan_integration.py tests/test_capital_flow_routing.py tests/test_assistant_tools.py` — 75 passed。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests` — 1099 passed, 3 skipped, 14 warnings。
- `git diff --check` — passed。

### 主代理獨立審查與修正

- 比對 pinned upstream `3acd67ffd98bbcf1713f9484d8a7b77871db6ade` 的 API／service、完整 diff、新測試、registry、host routing、collector 與 assistant 序列化。確認 primary symbol 保留 canonical ID，foreign 欄位使用 non-dealer／ex-dealer，所有 native 類別和 publisher total 留在 evidence。
- 加強 TPEx valuation 的 coverage counts／schema／selected presence 與列資料一致性；缺 PE key 是 invalid response，來源 null 仍是合法缺值；用 Decimal 驗證原始字串，拒絕 NaN、Infinity、空字串及非數字，原始精度與尺度不變。
- 加強 flow 的 schema／coverage／presence 一致性；TWSE AVAILABLE 無指定列為 absent，MISSING 的 presence 為 missing。混合 EMPTY/MISSING 或 absent/MISSING 是 partial，保留逐日原生狀態；增加混合與矛盾 coverage 回歸。
- HTTP 5xx 重試沿用共享 backoff；HTML／純文字 HTTP 400／503 保留 status，不誤稱 JSON／transport error；成功 HTTP 200 的無效 JSON 仍拒絕。預設非 strict HTTP 呼叫行為保留。
- 快取除 canonical request／日期範圍外，納入 provider 與設定的雜湊；切換 vendor 或 twmd service URL 不會命中舊來源資料，設定／token 不以明文出現在 cache key。更新設定文件與 `.env.example`。

### 主代理最終驗證

- `.venv/bin/python -m pytest -q packages/marketdata/tests/test_http.py packages/marketdata/tests/test_twmd_research.py tests/test_taiwan_integration.py tests/test_capital_flow_routing.py tests/test_assistant_tools.py` — **100 passed**（在最後新增 service/range cache 回歸之前）。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests` — **1117 passed、3 skipped、14 warnings**（17.22 秒）；既有 Pydantic／py_mini_racer deprecation warnings，無新失敗。
- `git diff --check` — passed；提交前另檢查 `git diff --cached --check`，涵蓋新檔案。
- 2026-10-07 00:41:26–27 Taipei，以 `.venv/bin/python - <<'PY'`／httpx、5 秒上限、無重試執行四個唯讀 GET：本機 `http://127.0.0.1:8000/api/v1/{valuations,institutional-flows}`，`TWSE:2330`／`TPEX:5347`，`start=end=2026-10-02` 或 `start_date=end_date=2026-10-02`。HTTP 均為 200，依序耗時 23.352／18.401／48.677／52.872 ms。TPEx valuation headers 為 schema ready=true，available=1／missing=0／selected=present。
- 2026-10-07 00:55:03–04 Taipei，另以 `.venv/bin/python - <<'PY'` 執行實際 `TwmdClient.valuation_history`、`TwmdFundamentalsVendor.fetch`、`MarketData.capital_flow`（同服務與日期範圍、每個 request 設定 timeout=5、共六個唯讀 GET）。兩個 venue 均通過 mapping／source date／status／PE／shares／null cash／null 5-day assertions：TWSE PE=28.98、法人 total=-5,343,414 股（合計 54.889 ms）；TPEx PE=40.48、total=1,232,412 股（合計 139.434 ms）。generic PE 未填入 pe_ttm，report_date 為空。
- 無前端變更；本卡未執行前端測試、TypeScript 或 build。

### 邊界與交付

- 本卡交付程式、設定文件及離線／唯讀驗證，未部署，也未改 ingestion、control、SQLite、採集或排程。上述夜間 checks 不代表交易時段交付或 deployed commit identity。
- 來源 API 沒有完整交易日曆契約，兩條 flow provider 路徑的 5 日總計皆保守維持 null；未把五個日曆日或任意五筆歷史觀察冒充五個完整交易日。
- TWSE valuation 沒有 coverage headers；其空列表維持 unknown。TPEx valuation headers 只提供 range counts／aggregate presence，不推定缺資料的日期。查詢以已完成日期範圍讀取，目前留存／修訂內容不等於歷史 as-of 資料。
- FinMind 仍可明確指定；其其他資料用途保留。完整 research service、個股畫面與 TradingAgents 共用研究內容由 PW-04 處理，profile／月營收由 PW-03 處理。
- 指派提交：`7500fbb`。最終任務提交標題：`feat(marketdata): 完成 PW-02 官方估值與法人接入`；交付到既有 [PR #1](https://github.com/garytu/PanWatch/pull/1)，不自動合併。worker 未建立 commit／PR，主代理負責最終提交。
- 唯一下一項為 **PW-03（ready）**；PW-04 仍等待 PW-03，未開始下一卡。
