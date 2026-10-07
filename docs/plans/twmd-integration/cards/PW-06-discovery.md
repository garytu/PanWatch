# PW-06：官方估值、營收與法人選股

狀態：ready。依賴：PW-05。Owner：unassigned。

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

尚未開始。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。

2026-10-07 協調查核：PW-05 已在總計劃與卡片 completed，故本卡 ready；未指派或開始實作。新鮮度與上游持續更新限制以 PW-05 evidence／runbook 為準。
