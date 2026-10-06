# PW-03：公司資料與月營收接入

狀態：in_progress。依賴：PW-01。Owner：pw03_profile_revenue_worker（gpt-6-luna／xhigh）。

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

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
