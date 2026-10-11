# PW-03：公司資料與月營收接入

狀態：completed。依賴：PW-01。Owner：pw03_profile_revenue_worker（gpt-6-luna／xhigh）。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

提供結構化公司資訊與月營收序列，讓後續研究頁與 AI 能引用成長數據。

## 範圍

- 新增 company_profiles 與 monthly_revenues 有界 typed reads。
- 保留 issuer qualification、industry、issued-share／capital 原始語意；profile 不改 instrument membership。
- 月營收保留每月 presence、來源百分比、備註、revision、實際資料月份與 receipt。
- 提供相鄰月份與固定月範圍查詢，遵守 upstream bounds；缺月份留空。
- 建立後端區塊返回格式，讓 research service 能合併，而非塞進單一 Fundamentals 日期。

## 驗收條件

- TWSE／TPEX 的 2026-08 正例及 2026-07 missing 保留；整體 AVAILABLE 不掩蓋缺月。
- 千元尺度只在顯示／明確轉換時處理，單位推定來源保留。
- YoY／MoM／累計 YoY 不互換、不重算成不同口徑；月營收不當作季度財報營收。
- TPEX ETF unsupported、無 profile、null 數字與來源錯誤不生成假的公司基本面。
- latest-only profile 不接受歷史 as-of 假設；修訂內容／receipt 能追溯。

## 驗證

兩個 venue 的 profile／營收映射、範圍與 duplicate selectors、missing month、零值、signed value、malformed/error、cache scope 隔離。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

TwmdClient、MarketData、typed models；新增檔案位置遵循 packages/marketdata 既有慣例；宿主 research 模組。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不建立上櫃歷史營收採集能力，不推定公告精確時間；不自動補整市場歷史。

## 進度與交接

Final checkpoint：**completed**，2026-10-07 Asia/Taipei。Worker：gpt-6-luna／xhigh。主代理已獨立審查、修正並重新驗證；全部必要驗收通過。worker 未提交 commit，也未變更卡片狀態／owner。

本次實作提供 `TwmdClient`／`MarketData` 的 `company_profile`、`monthly_revenues` 與指定月份加前月的 `adjacent_monthly_revenues` typed reads。profile 保留最新 snapshot coverage 與可能仍存在的舊 profile，且不接受 as-of；營收逐月保留 present／not-in-report／missing、retained row、精確來源數字字串、百分比、notes、report／acquisition／receipt、revision、capture 與 hashes。讀取遵守 2024-01 起點、最多 120 個月及 Taipei 當月 inclusive；不把營收映射到 Fundamentals。cache 依 TWMD base URL、credential scope、canonical issuer、dataset 及完整範圍區隔，回傳 defensive copies，僅快取驗證成功的 reads。研究 adapter 回傳獨立的 `data`／`status`／`reason`／`evidence` blocks；HTTP、transport、timeout 與 invalid response 不會變成空資料。

修改檔案：

- `packages/marketdata/src/marketdata/types.py`、`client.py`、`__init__.py`、`errors.py`、`vendors/twmd.py`
- `packages/marketdata/tests/test_twmd_profile_revenue.py`
- `src/platform/marketdata/marketdata_client.py`、`src/platform/runtime/config.py`
- `src/modules/research/twmd_profile_revenue.py`、`tests/test_twmd_profile_revenue_blocks.py`、`tests/test_taiwan_integration.py`
- `.env.example`、`docs/taiwan-market-support.md`

驗收涵蓋 TWSE:2330／TPEX:5347 的 2026-08 row 與 2026-07 missing、指定月份加前月和跨年計算、range floor／長度／未來日期、profile latest-only／retained absence、TPEX ETF unsupported、數字 zero／signed／null、malformed response、HTTP error、profile timeout 設定及 cache scope／copy 隔離。精確測試結果：

- `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_profile_revenue.py tests/test_twmd_profile_revenue_blocks.py tests/test_taiwan_integration.py` — **56 passed**。
- `.venv/bin/python -m pytest -q packages/marketdata/tests tests/test_taiwan_integration.py tests/test_twmd_profile_revenue_blocks.py` — **322 passed**。
- `git diff --check` — **passed**。

Profile timeout 由 `TWMD_PROFILE_TIMEOUT_SEC` 設定，預設 20 秒、單次請求且不重試；上游成功樣本約 15.5 秒，這是有界設定，不保證未來 latency。一般 TWMD 端點仍使用 `TWMD_TIMEOUT_SEC`。Worker 交接時未執行 live smoke、未呼叫 ingest/control/DB，亦未跑全 repository suite；主代理的 live read-only smoke、完整 diff 審查與最終 backend 回歸結果見下節。工作區原有 `src/modules/automation/agent_catalog.py` 修改及列出的 untracked 檔均保留，沒有納入本卡。


### 主代理獨立審查與最終驗證

- 逐項審查全部程式／測試 diff、宿主 routing 和設定、研究 adapter、上游 API／service／models；來源 checkout 仍為 `3acd67ffd98bbcf1713f9484d8a7b77871db6ade`。正式 reads 經 query API，不依賴上游 Python 套件或 SQLite。
- 新增回歸測試先重現 **7 個 failures**：profile envelope／snapshot／row 與營收 coverage／row 可接受另一 venue 的來源契約，以及兩個資料類別的 cache 可繞過不同憑證的 401。主代理加入 venue-local source／contract 驗證，並把 credential scope 納入 cache key；重跑全部通過。
- 補上相同數字 issuer 在 TWSE／TPEX 的 cache 隔離、負營收金額、空 notes、三種 publisher 百分比獨立保留、missing partition 禁止附帶假 row、extra／duplicate month selectors，以及 120 個月 inclusive 邊界。null／zero／signed 的 lexical scale 不轉換；缺月不補零；retained row 加新版報告 absence 仍可追溯。
- 修正後 `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_profile_revenue.py tests/test_twmd_profile_revenue_blocks.py tests/test_taiwan_integration.py`：**70 passed**。之後加入同一範圍測試的 120 月成功 assertions，已含於下列最終全量。
- `.venv/bin/python -m pytest -q tests packages/marketdata/tests`：**1151 passed、3 skipped、14 warnings**（17.38 秒）。實作前基線為 **1117 passed、3 skipped、14 warnings**（17.51 秒）。既有 warnings 未增加；無前端變更，本卡未重跑前端測試／build。
- `git diff --check` 與最終 `git diff --cached --check`：passed；檢查新檔完整 staged diff，僅納入本卡範圍與下一卡 ready 狀態。工作區原有 automation 修改與未追蹤工具檔保持原樣。

2026-10-07 02:38:27–02:38:52 Taipei，以實際 `TwmdClient → MarketData → TwmdProfileRevenueResearch` 執行本機 `http://127.0.0.1:8000` 有界唯讀 GET，五項均 HTTP 200，typed／block assertions 通過：

| selector | 端點 | latency | 安全證據 |
| --- | --- | --- | --- |
| TWSE:2330 | company-profiles | 14,021.416 ms | available；report 2026-10-03；revision 1；receipt 2026-10-04T13:07:03.960501Z |
| TPEX:5347 | company-profiles | 10,397.965 ms | available；report 2026-10-04；revision 1；receipt 2026-10-04T13:07:07.049244Z |
| TWSE:2330，2026-07..08 | monthly-revenues | 131.418 ms | partial；July missing／August present；原值 514805337；revision 1；receipt 2026-10-04T13:09:22.189587Z |
| TPEX:5347，2026-07..08 | monthly-revenues | 63.669 ms | partial；July missing／August present；原值 5092599；revision 1；receipt 2026-10-04T13:09:19.479749Z |
| TPEX:006201，2026-07..08 | monthly-revenues | 21.809 ms | unsupported_etf；兩月均無 row |

Profile 使用 20 秒、零 retry；營收使用 5 秒及既有 retry policy。兩 venue 的營收千 TWD inference 原文和未知 publication time 保留。此 smoke 是夜間唯讀觀察，未驗證部署 commit、全市場 coverage、未來 latency、歷史 as-of、盤中到達或上櫃歷史採集能力；沒有執行 ingest／control／排程／訂閱／部署，也未寫 live DB。完整研究聚合、前端與 AI／TradingAgents 接線仍屬 PW-04。

協調指派 commit：`a9c527f`。最終任務提交標題：`feat(marketdata): 接入公司資料與月營收區塊`；交付至既有 [PR #1](https://github.com/garytu/PanWatch/pull/1)，不自動合併。PW-04 已確認依賴並更新為唯一下一張 ready 卡；本次未開始 PW-04。
