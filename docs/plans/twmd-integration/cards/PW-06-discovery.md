# PW-06：官方估值、營收與法人選股

狀態：completed。依賴：PW-05。Owner：pw06_discovery_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

讓使用者以明確條件找出台股候選，並看懂每檔入選／排除原因。

## 範圍

- 在現有 discovery 加入選擇性的 PE／PB／yield、營收 YoY、法人買賣超條件。
- 第一版使用有上限的候選集合及 cache；沒有批次 upstream 契約前不對全 catalog 發 N×M 請求。
- canonical active cash universe 篩除 warrants／ETN／不支援種類，保留 venue。
- 返回匹配原因、使用資料日、missing／stale 排除原因與掃描範圍。
- 原有價格排序與預設策略不變；新條件由使用者明確選擇。

## 驗收條件

- 相同資料固定條件結果 deterministic，沒有 eligible 候選不 fallback 到停用市場。
- 缺值不當成 PE=0／營收=0／法人中性；不同時期不可合成假的同日 snapshot。
- 「法人連買 N 日」只有完整已確認交易日序列才可啟用；第一版可先做單日條件。
- 候選數、併發、timeout、cache 命中及 partial scan 可觀察。
- UI／AI discovery tools 均帶資料日期及條件解釋。

## 驗證

discovery routing、candidate identity、filter 邊界、missing/stale、排除非 cash instruments、有限請求數和穩定排序；frontend 條件與結果測試。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/market/api/discovery.py、packages/marketdata/src/marketdata/client.py 的 hot_stocks、assistant discovery tools；現有 frontend 機會入口。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不直接新增策略權重、自動買賣訊號或宣稱回測收益；因子與歷史有效性留到 PW-13。

## 進度與交接

2026-10-07 implementation worker handoff：回報 `review`，等待主代理獨立審查；未變更本卡狀態／owner、總計劃或其他任務卡，未建立 commit／push。

已實作：

- `packages/marketdata/src/marketdata/client.py`、`packages/marketdata/src/marketdata/types.py`：建立 canonical venue-aware TW 價格候選池；只保留 active `EQUITY`／`ETF`，用 turnover／change 降冪及 canonical ID tie-break。價格 snapshot 回應逐批核對 ID、拒絕外來／重複 ID，記錄 catalog、選取／請求／回傳列、失敗、cache 與 partial scan。官方篩選候選固定為 canonical 順序前 2,000 個可用 universe ID，再取成交額前 20；catalog 無 upstream 分頁契約，因此截斷偏差在 scope 明示。既有 `hot_stocks` 保留價格排序介面，現排除 warrants、ETN、preferred 等非支援種類。
- `src/modules/research/taiwan_discovery.py`、`src/modules/market/api/discovery.py`：新增 POST `/discovery/stocks/screen`，GET `/discovery/stocks` 維持舊 list 格式。PE／PB／殖利率／最新月營收 YoY／單日法人股數可選條件使用已配置 TWMD source，最多兩個 screen request 併行、全域 bounded factor executor、30 秒 aggregate deadline（價格階段至多 15 秒）、300 秒讀取 cache；不建立無限制 TWMD fallback。每日資料 freshness 依日曆日 7 天，營收由月末計算 90 天；缺值不補零、營收不回退補期、不同資料日分欄列示。ETF issuer revenue 明確列 unsupported。來源證據保留 contract／URL、接收／取得／首次觀察／報告／發布欄位、capture／revision／hash、單位、coverage／presence；不可得欄位保留 null。殖利率保留股利參考年度與來源解讀，不推定為目前年化殖利率。
- `src/modules/assistant/tools.py`、`src/modules/assistant/tool_descriptors.py`：新增延遲暴露的唯讀官方台股篩選工具，摘要與完整 data 都含條件、資料範圍、候選 scope、日期、請求／cache／partial 計數及逐條說明。
- `frontend/packages/api/src/discovery.ts`、`frontend/src/components/DiscoveryPanel.tsx`：新增選擇性篩選表單、匹配／排除理由、資料日／來源 contract、候選與掃描 scope、請求／cache／partial 資訊；序號控制會丟棄過期請求結果。`frontend/src/components/DiscoveryPanel.test.tsx` 驗證表單提交與結果呈現。
- 回歸測試更新 `packages/marketdata/tests/test_twmd.py`、`tests/test_discovery_routing.py`、`tests/test_assistant_tools.py`，新增 `tests/test_taiwan_discovery.py`。

驗收／驗證：

- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：1193 passed、3 skipped、14 warnings（16.70 秒）。包含篩選門檻、月份月底 freshness 邊界、最新有值期別不回填、missing／stale／ETF unsupported、來源證據、expired deadline、穩定排序、外來 snapshot ID 排除、universe 上限、停用 source 不 fallback、TW snapshot 不 fallback 與 API／AI 工具路由。
- `cd frontend && node node_modules/vitest/vitest.mjs run`：21 files、52 tests passed。
- `cd frontend && node node_modules/typescript/bin/tsc -b`：passed。
- `cd frontend && node node_modules/vite/bin/vite.js build`：passed；Vite 提醒本機 caniuse-lite 資料較舊。
- `git diff --check`：passed。

邊界／恢復：未連線 live TWMD 服務做運行驗收；測試使用 typed read／HTTP mocks。法人只篩最新有效單日，不推算連買日數。全市場候選排序偏差受固定 canonical 前 2,000 筆限制，回傳 partial scope。沒有觸發 SQLite 讀寫、上游下載、訂閱控制、排程、部署或策略權重變更。父代理可直接檢視目前 working tree；既有 `src/modules/automation/agent_catalog.py` 與 untracked 個人檔案保持原樣。


2026-10-07 Coordinator acceptance：completed。協調指派提交 `273aa86`；worker 為 pw06_discovery_worker（gpt-6-luna／xhigh，依本卡／總計劃固定值）。主代理獨立檢查完整 diff、typed read 契約、API／AI／UI 呼叫端和所有新增測試，再親自修正：

- UI 點選候選改傳完整 canonical ID，避免同碼跨 venue 失去身分；營收文案改為最新留存月份，明示來源未提供發布時間。partial／排除理由改為中文，忙碌／來源失敗不顯示成已完整篩選的零匹配；展示明確日曆日 freshness、總期限與併發限制。
- 修正 concurrency／price error 分支未提供 pool 引數而拋例外的 helper；錯誤回應使用同一 request clock 並保留 partial checkpoint。期限在每次 transport 前重新計算，未完成的底層讀取持續占用 screen permit。
- service／API／AI 一致驗證門檻，拒絕 boolean／非整數股數與超界值；AI 不忽略未知或連買日數條件。TPEx 非四碼估值標的先回報 unsupported，避免無效 upstream request。AI 摘要區分 logical read 與真正 HTTP attempt；營收 evidence 保留 upstream served_at 與逐月 presence。
- 回歸涵蓋忙碌／timeout checkpoint、來源部分失敗保留其他候選、20 個 candidate read 上限、同碼跨 venue cache 隔離、cache hit 重估月末 age、typed cache 與 transport 次數區別、逾時後 request permit 保留、API／AI 條件拒絕，以及 UI canonical 開啟與舊請求結果丟棄。

最終主代理驗證（全部修正後）：

- `.venv/bin/python -m pytest -q tests/test_taiwan_discovery.py tests/test_discovery_routing.py tests/test_assistant_tools.py packages/marketdata/tests/test_twmd.py`：**61 passed**。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1204 passed、3 skipped、14 warnings，17.77 秒**。
- frontend `node node_modules/vitest/vitest.mjs run src/components/DiscoveryPanel.test.tsx`：**3 passed**；完整 `node node_modules/vitest/vitest.mjs run`：**21 files／54 tests passed**。
- frontend `node node_modules/typescript/bin/tsc -b`、`node node_modules/vite/bin/vite.js build`：通過。
- `git diff --check`：通過；最終暫存範圍及新增檔案另以 `git diff --cached --check` 驗證。既有 backend deprecation、frontend React Router／act／duplicate-key 與 Browserslist warnings 保留。新增測試初次使用 Array.at 超出既有 TypeScript target，已改為相容索引，最終型別檢查通過。

最終 Handoff：透過既有 [PR #1](https://github.com/garytu/PanWatch/pull/1) 交付，未合併或部署。價格階段按 canonical ID 前 2,000 個 active EQUITY／ETF 取樣，再取成交額前 20 檔研究；固定範圍偏差、7 日曆日／月末起 90 日 freshness 規則及部分讀取結果均明示。此卡以離線 fixtures／HTTP mocks 驗證，沒有新增 live 運行驗收、上游採集、排程、訂閱、control mutation 或策略權重。PW-05 的持續更新前提仍存在。預設下一張 **PW-07 ready**，PW-13 仍等待 PW-11／PW-12；本次不啟動下一張。既有 automation 修改及個人未追蹤檔案保留且排除於提交。
