# PanWatch × twmd 資料接入計劃

建立日期：2026-10-06（Asia/Taipei）。本計劃依據兩邊原始碼、本機 twmd 唯讀查詢及部署驗收文件整理。

目標：先讓台股個股研究與 AI 使用可追溯的官方估值、法人和月營收，再逐步增加選股、籌碼、公告、財報、大盤比較與歷史研究。每次只執行一張指定的任務卡。

## 下一項

**PW-01–PW-09 已完成；PW-10 執行中（in_progress）。** PW-09 由 pw09_material_information_worker（gpt-6-luna／xhigh）實作，主代理獨立審查、修正並完成驗證。本次到 PW-09 為止。

PW-01 已交付契約、離線 fixtures 與驗證；PW-02 已接入官方估值／法人 typed reads、相容介面與來源證據；PW-03 已接入公司資料／月營收 typed reads 與獨立 research blocks。PW-04 已交付有界 research 聚合、既有個股入口的研究面板，以及 assistant／TradingAgents／研究 context 共用資料。PW-05 已交付各區塊 age／coverage、更新 runbook 與四個股票／ETF selectors 的唯讀運行驗收；持續更新仍有上游 manual-only 前提，未變更採集或排程。PW-06 已交付選擇性的官方條件選股、有限候選與讀取統計、逐筆資料日期／來源／排除理由，以及 UI／AI 共用入口。PW-07 已交付原生交易單位的融資融券與集保持股專用區塊，UI／AI 共用六區塊，並明示大額門檻、官方分母、variant／缺週與 TWSE 限制。PW-08 已交付來源／原生單位分組的分點買賣排行、觀察分母集中度、成交 VWAP、逐日覆蓋與已保留的單日價格明細；UI／AI 共用七區塊，筆數／修訂衝突維持部分可用，未 materialized 與空明細狀態分開。PW-09 已接入獨立的 TWSE current／MOPS history 官方事件時間線、原文查詢工具與既有 AI 公告分析；覆蓋、年度採集、修訂及原始取得時間分開，無有效採集證據不能宣稱完整或沒有事件。其餘卡依下表等待指定。本計劃不授權採集、排程或部署。執行範例：

> 執行 docs/plans/twmd-integration 的 PW-01；完成該卡的驗收與相關檢查，更新計劃狀態，然後停下。

## 執行清單

| ID | 任務 | 狀態 | 依賴 | 交付價值 |
| --- | --- | --- | --- | --- |
| PW-01 | [契約、能力與樣本](cards/PW-01-contracts.md) | completed | — | 確認欄位、日期、來源、單位與資料可用性 |
| PW-02 | [官方估值與法人](cards/PW-02-valuation-flows.md) | completed | PW-01 | 現有基本面與法人介面改用明確的官方資料 |
| PW-03 | [公司資料與月營收](cards/PW-03-profile-revenue.md) | completed | PW-01 | 結構化公司資料及有缺口的營收時間序列 |
| PW-04 | [個股研究與 AI](cards/PW-04-research-ai.md) | completed | PW-02、PW-03 | 使用者能看到、詢問並追溯第一批資料 |
| PW-05 | [更新流程與第一批驗收](cards/PW-05-freshness-acceptance.md) | completed | PW-04 | 確認第一批功能的可用範圍與更新責任 |
| PW-06 | [官方資料選股](cards/PW-06-discovery.md) | completed | PW-05 | 可選擇估值、營收與法人條件並理解結果 |
| PW-07 | [融資融券與集保](cards/PW-07-margin-shareholders.md) | completed | PW-01、PW-04 | 個股籌碼變化及可解釋的持股分布 |
| PW-08 | [券商分點](cards/PW-08-broker-flow.md) | completed | PW-01、PW-04 | 分點買賣集中程度、VWAP 與覆蓋狀態 |
| PW-09 | [官方重大訊息](cards/PW-09-material-information.md) | completed | PW-01、PW-04 | 官方事件時間線與 AI 原文分析 |
| PW-10 | [有限範圍財報](cards/PW-10-financial-statements.md) | in_progress | PW-03、PW-04 | 支援範圍內的台股財務事實分析 |
| PW-11 | [大盤基準](cards/PW-11-benchmarks.md) | waiting | PW-01、PW-04；上游採集 | 台股首頁指數與個股相對大盤表現 |
| PW-12 | [除權息與減資事件](cards/PW-12-corporate-actions.md) | ready | PW-01、PW-04 | 解釋事件附近價格跳動與回測限制 |
| PW-13 | [歷史資料與研究驗證](cards/PW-13-historical-research.md) | waiting | PW-05、PW-06、PW-11、PW-12 | 排除未來資訊、驗證新增研究條件 |
| PW-14 | [即時指數與分鐘 K](cards/PW-14-live-data.md) | waiting | PW-11；上游交易時段驗收 | 啟用有健康證據的盤中圖表與市場背景 |

PW-10 已指派 pw10_financial_statements_worker（gpt-6-luna／xhigh）執行；PW-12 的卡片依賴已滿足而標為 ready，尚未開始。PW-11 仍等待上游 benchmark 實際採集；不以第一批完成代替該前提。PW-13 已滿足 PW-05／PW-06，仍等待 PW-11／PW-12；保持 waiting。

預設按表格順序逐項執行。使用者可指定已滿足依賴的其他卡；不自動延伸到下一張。PW-01–PW-05 為第一批交付，PW-06–PW-10 為研究擴充，PW-11–PW-14 涉及額外採集或歷史／即時驗證。

## 2026-10-06 查核基線

下列是當日約 23:23–23:24 Taipei 的樣本觀察，不代表所有標的、日期或未來服務狀態：

- PanWatch 已接上 twmd 的報價、日 K、歷史 1m/5m bars 與訂閱控制；TW 的基本面、法人、融資和股利仍主要走 FinMind。
- TradingAgents 完整財務採集僅走 CN 路徑；TW 的財報上下文仍有缺口。
- twmd `/api/v1/readiness` 回報資料 ready，TWSE／TPEx 日價最新日期均為 2026-10-06。
- TPEx active catalog 共 12,389 筆，其中 EQUITY 892、ETF 119、PREFERRED 1；其餘主要為權證，不可整批納入股票選股。
- TWSE:2330、TPEX:5347 的 2026-08 月營收可讀；同次查詢的 2026-07 為 missing。
- TWSE:2330 法人、TPEX:5347 估值的 2026-10-02 可讀；查詢中的 2026-10-05 為 MISSING。10-03／10-04 的 MISSING 不能自行判定是缺交易日。
- 法人／TPEx 估值日期上限必須早於當日 Taipei 日期；即使盤後查當天也會被 API 拒絕。
- TAIEX／TPEX benchmark bars API 都回傳零筆、missing coverage；TAIEX live quote 是 unconfigured。
- bars capabilities 回報 `live_collection_supported=true`、`live_collection_configured=false`、`live_collection=false`。
- 股價 collector connected、四檔訂閱已確認，但夜間 `live_ready=false`；這不是交易時段交付驗證。
- company-profile 單次查詢超過 10 秒 timeout；文件記錄部署已驗收，但本次沒有取得新的成功樣本。PW-01 必須重查並保留延遲結果。

twmd README 部分描述落後於原始碼／能力 API。不能只根據 README 推定即時功能不存在或已啟用。PanWatch 舊的「零有效上櫃標的」與「分鐘即時功能尚未實作」描述應在相關卡驗證後更新，保留歷史查核日期。

## 接入方式

資料路徑：

```text
twmd persisted query API
  → packages/marketdata 的 provider／typed reads
  → PanWatch research service（範圍限制、快取、部分失敗）
  → 個股 API、AI tools、TradingAgents、選股
  → 前端研究內容
```

1. 延伸現有 TwmdClient 與 MarketData，不新增直讀 twmd SQLite 的路徑。套件維持零宿主 DB／web 依賴。
2. 估值、法人、融資沿用相容的既有介面；月營收、持股分布、分點、公告、財報及基準保留各自結構。不要把所有欄位塞進 Fundamentals。
3. research service 按區塊返回 data／status／reason／來源資訊。來源資訊至少能表達 canonical ID、資料日／期別、可取得的發布／首次觀察／採集時間、source、units、coverage 與 revision。
4. 一個區塊失敗不能抹去其他成功資料；整份 research 不具有單一的「最新日期」。
5. provider 選擇明確：官方為原生 TW 路徑；FinMind 可保留既有／明確配置路徑。不可靜默混用不同來源或日期來填滿一列資料。
6. 快取按 provider、canonical ID、資料類別與查詢範圍區分，TTL 按資料頻率訂定；限流、併發與讀取上限沿用現有工具。未取得全市場批次契約前，掃描必須使用有上限的候選集合。

## 共通語意與邊界

- 保留 TWSE:／TPEX:；同碼跨市場不可混合，bare code 有歧義就要求 venue。
- 缺值保留 null；missing、empty、selected issuer absent、unsupported、stale、provider error 不能合併成零或「沒有事件」。
- dataset coverage 與單一標的 presence 分開；整體 AVAILABLE 也可能有缺月份。
- 法人是股數、融資可能是張／交易單位、營收有千元尺度、benchmark 是點數。精確數值在資料邊界用 Decimal 或來源字串保存，展示才格式化。
- 法人外資自營商子項不可重複加總；分點觀察不能證明交易者身分或未來方向；TDCC 是保管帳戶分布，不能直接等同實際股東人數。
- issuer profile latest-only；上櫃營收沒有已驗證的歷史採集路徑；財報目前限 TWSE industry 24 的合併報表。
- current／history 公告來源分開；除權息結果不是完整股利公告、支付日曆或公司行動流。
- 歷史查詢拿到的是目前留存／修訂內容，不自動等於當時已知資訊。發布日、首次觀察、採集時間不能相互代換。
- 新研究資料不改紙上交易價格門檻、資金配置或策略權重；新的下單條件和分鐘策略需另立任務。
- 查詢不觸發 upstream download、補資料或訂閱。採集／排程由 twmd 負責，瀏覽器不取得 control token。

## 執行與完成規則

狀態：ready、waiting、in_progress、review、blocked、completed。ready 表示依賴與契約可開始；completed 必須有該卡全部必要驗收證據。blocked 記錄具體外部條件及可恢復步驟，不以假資料取代。

### 固定的實作代理流程

每次執行一張 implementation task，原始工作階段（主代理）必須先讀取本總計劃、完整任務卡、`AGENTS.md`、相關程式與最新上游契約，再確認依賴已完成、任務狀態為 `ready` 並檢視 `git status`。建立本計劃時已有未提交的 `src/modules/automation/agent_catalog.py` 和其他 untracked 檔，均不屬於本計劃，執行時要重新檢查並保護所有既有修改。

主代理在目前 checkout 將任務狀態更新為 `in_progress`、指定 implementation worker，並依 repository 的 [execute-task 技能](../../../.agents/skills/execute-task/SKILL.md)先提交這項協調狀態；不建立或切換 worktree。然後只能派出一個實作子代理，參數固定為 `model: "gpt-6-luna"`、`reasoning_effort: "xhigh"`。子代理不得繼承或沿用原始工作階段的模型設定。若該模型或 effort 無法使用、代理無法派出，主代理要將任務與卡片恢復 `ready`、提交恢復後的狀態並報告原因；不可靜默換模型、略過子代理，或由主代理接手實作。

主代理在派工時提供 task ID、checkout、完整卡片與依賴、允許修改的檔案範圍、共用工作區注意事項、驗收條件、預期測試和交接格式。子代理擁有該卡的實作檔案，負責讀取程式、完成實作、執行卡片指定驗證，只能編輯自己卡片的「進度與交接」內容並回報 `review` 或具體 blocker。子代理不可修改總計劃、任務狀態／owner、其他卡片、執行協調提交或建立最終提交。主代理在等待及審查期間不與子代理同時編輯其實作檔案。

子代理交件後，主代理是該任務的最終負責人：逐項對照驗收條件獨立檢查完整 diff、呼叫端、測試與交接證據；子代理回報的測試不視為審查通過。發現問題時由主代理親自修正，新增必要回歸測試，並在所有修正後重新執行相關驗證。主代理負責整理最終進度與限制、將任務及符合依賴的下一卡更新為 `completed`／`ready`，審查完整最終 diff 後建立唯一的最終任務提交，確認提交只含本任務範圍。存在未解決重大問題、失敗驗證或 blocker 時不得標記完成或提交成已驗收成果；要留下可恢復的 review／blocked checkpoint 並向使用者說明。

最終回覆要報告 task ID、worker 模型與 effort、主代理審查／修正結果、實際驗證、限制、最終 commit，以及唯一下一張 ready 卡。完成一張後即停止；不可自行開始下一張。

依 AGENTS.md 在 `codex/` 分支實作，以 Conventional Commits 中文標題和 PR 交付，預設 squash merge。每卡更新自己的進度／handoff 與本表狀態，確認依賴後才把下一卡標為 ready。記錄實際執行的測試、結果、限制與提交／PR；不把 local prototype 寫成 deployed。

每卡最少跑相關 backend tests 與 `git diff --check`。新增驗證檔名由執行卡決定；卡內寫的是測試情境，不宣稱檔案已存在。涉及前端時，在 frontend 目錄執行：

```sh
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

第一批整合及跨模組變更完成時，執行：

```sh
.venv/bin/python -m pytest -q tests packages/marketdata/tests
git diff --check
```

一般回歸測試用固定時間與離線 fixtures。唯讀 live smoke check 記錄時間、服務、selectors、HTTP status、延遲與 safe evidence；不可把盤後結果寫成盤中驗收。現有失敗先獨立確認再記錄。

本計劃沒有授權部署、修改 twmd 排程、廣泛歷史下載或 control mutations。需上游操作的卡先產出具體範圍與 runbook，依使用者該次指令執行；未獲授權時完成可做的契約／程式／唯讀驗證，並誠實保留運行驗收狀態。

## 參考

- [現有台股支援](../../taiwan-market-support.md)
- [原先 twmd follow-up](../../twmd-taiwan-follow-up.md)
- [PanWatch twmd provider](../../../packages/marketdata/src/marketdata/vendors/twmd.py)
- [宿主 provider routing](../../../src/platform/marketdata/marketdata_client.py)
- [TradingAgents 財務路徑](../../../src/modules/automation/tradingagents/agent.py)
- [上游 expansion deployment acceptance](/Users/garytu/works/tw-market-data-main/docs/data-sources-deployment-acceptance.md)
- [上游實作狀態與即時任務](/Users/garytu/works/tw-market-data-main/docs/plan/README.md)

上游絕對路徑是本機查閱入口，不是 runtime dependency；PW-01 將需要的版本與契約摘要留存在本 repository。
