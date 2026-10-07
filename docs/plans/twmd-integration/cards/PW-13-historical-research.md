# PW-13：歷史資料與新增研究條件驗證

狀態：ready。依賴：PW-05、PW-06、PW-11、PW-12。Owner：unassigned。

執行前讀取[總計劃](../README.md)與 repository AGENTS.md；共通資料語意、測試與完成規則均適用。

本卡必須遵守總計劃的「固定的實作代理流程」：implementation 一律派給獨立的 `gpt-6-luna`／`xhigh` 子代理，不沿用原工作階段模型；原工作階段負責獨立審查、修正、重跑驗證並提交完成。工作分配及 review／commit 權責以[execute-task 技能](../../../../.agents/skills/execute-task/SKILL.md)為底稿，模型與 effort 固定值以本計劃為準。

## 目標

評估新增選股條件是否有可用歷史證據，避免回測使用未來公布或後來修訂的資訊。

## 範圍

- 盤點各資料集完整交易日／月份、source receipt、first observation 與 revision；把缺口和外部補資料需求列清。
- 為 PW-06 條件建立 bounded offline research dataset 與 deterministic evaluation，先記錄日期、scope、eligibility，再計算結果。
- 定義可用時間門檻：沒有 verified publisher time／revision-as-of 時，保守用實際 observation evidence 或只做當期描述研究。
- 對無法還原歷史已知版本的資料，禁止宣稱 point-in-time 回測；需要時從現在開始留存 research snapshots。
- 與對應 benchmark 比較，記錄樣本量、缺口、成本與 raw-price 公司行動限制；只有合格資料才提出後续 factor task。

## 驗收條件

- 2026-10 才採集的 2024 財報不得在無更早 evidence 下用於 2024 決策。
- 目前 revision 的 received_at 不能证明更早內容可知；profile latest-only 不做歷史 universe。
- train/eval 時間分離，不以全期資料調參後宣稱 out-of-sample。
- 缺上櫃歷史營收／基準時對該範圍排除並說明，不補造資料。
- 實際可用樣本太少時交付不足證據結論，不能稱條件已驗證有效。

## 驗證

固定時間 available-at 門檻、修訂、missing windows、future leakage、venue benchmark、dataset 可重現hash與現有backtest/factor回歸。

執行相關 backend tests 和 `git diff --check`；涉及前端執行總計劃列出的測試、TypeScript 與 build。記錄實際命令及結果。

## 實作入口

src/modules/strategy/backtest/、factor_eval.py、factor_calibration.py；research context/store；上游 revision／receipt 契約。

路徑是開始查閱的位置；先搜尋現有實作，按實際 repository 結構限定修改範圍。

## 邊界與外部前提

不自動新增／調整 production factor weights 或交易訊號。歷史補齊、上游 as-of API 和完整 total-return 模型屬另行工作。

## 進度與交接

2026-10-08：PW-05／PW-06／PW-11／PW-12 均 completed，依賴已滿足；本卡 ready、Owner unassigned，尚未開始，等待使用者另行指定。樣本不足仍須如實交付不足證據結論。執行時更新：目前狀態、修改檔案、已通過／未通過的驗收、實際測試、外部限制、提交／PR 和可恢復步驟。未完成的上游或運行驗收必須明列。
