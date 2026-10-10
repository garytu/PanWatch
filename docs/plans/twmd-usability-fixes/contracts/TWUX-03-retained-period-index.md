# TWUX-03：財報留存期別索引需求

狀態：歷史需求草案，已由 [twmd 正式 v1 契約](/Users/garytu/works/tw-market-data-main/docs/plan/contracts/financial-statement-periods.md) 與 [部署驗收](/Users/garytu/works/tw-market-data-main/docs/financial-period-index-deployment-acceptance.md) 取代。2026-10-10 已部署 `/api/v1/financial-statement-periods`，PanWatch 接入完成；以下保留原始需求供追溯，不代表目前能力缺口。

## 已核對能力與缺口

目前可用的 `/api/v1/financial-statements` 是明確期別讀取：必填 canonical `instrument_id`、`fiscal_year`、`fiscal_quarter` 與 `report_scope`。它不能列出某家公司所有已留存期別，也不能據此預設「最新已留存」。後續 no-report discovery 與已留存報表 authority 分開；期別清單不能以最新發現狀態取代留存事實。

2026-10-10 的唯讀 client check 僅查 TWSE:2330 2024Q4：回傳 394/394 筆 facts，狀態 available，耗時 12.266 秒。這只證明該明確期別可以讀取，不證明 2026Q3 不存在，也不證明它是最新已留存期別。PanWatch 不應為推測期別逐季探測。

## 建議契約

先確認是否採用有界唯讀期別索引，再共同決定 API 名稱與路徑。查詢必須至少接受以下 selectors：

| Selector | 要求 |
|---|---|
| `instrument_id` | 必填單一 canonical issuer，例如 `TWSE:2330`；拒絕代碼範圍、全市場查詢與多標的查詢。 |
| `venue` | 必填或由 canonical ID 驗證；第一階段只支援 `TWSE`。 |
| `source` | 必填或固定為已驗證的 MOPS 財報來源及其 source contract。 |
| `industry_code` | 支援範圍需明確回傳；第一階段只涵蓋已驗證的 industry `24`。 |
| `report_scope` | 必填；第一階段只支援 `consolidated`。 |
| `statement` | 可選 `balance_sheet`、`comprehensive_income` 或 `cash_flows`；若指定，回覆必須指出該表的期別可讀狀態。未指定時，索引列出合併報表層級的留存狀態。 |
| `limit`／`cursor` | 單一 issuer 的期別列需按年度／季度倒序；每頁最多 40 個期別，可用 opaque cursor 繼續，不接受任意 offset 或無界查詢。 |

第一階段只列 2024 年起、已結束季度的單一 TWSE 普通股 industry-24 合併報表。TPEx、ETF、其他證券類別、其他產業、個別報表及來源未支援範圍應回明確 unsupported／not applicable；不得因索引請求而逐期呼叫財報讀取端點。

每個期別項目至少要提供：

- `fiscal_year`、`fiscal_quarter`、`report_scope`、`statement` 覆蓋（如有指定）。
- 該 issuer／期別的 presence 與讀取狀態：`present_readable`、`present_unreadable`、`missing`、`unsupported` 或 `unknown`。只有完整且可信的索引查詢可把 `missing` 當作該 issuer／期別不存在留存報表的證據。
- 留存報表 authority：目前有效的 capture、semantic revision、source contract、原始接收時間與最近合格 observation 時間。後續 no-report discovery 必須與既有留存報表分開，不能覆寫或刪除它。
- 索引覆蓋狀態：`complete`、`partial` 或 `unknown`；分頁游標、是否還有期別，以及查詢服務時間。查詢服務時間不是來源接收時間或報表發布時間。
- 可安全判定時的 `latest_retained_period`，並說明 selectors 與索引完整性。不得以最新發布、最新發現、資料集存在或本機當前季度推導「最新已留存」。

索引只讀現有 retained metadata／authority，不觸發 MOPS 採集、重試、補抓、訂閱或報表解析。上游需以 `(instrument_id, report_scope, fiscal_year, fiscal_quarter)` 有界定位；單次最多回 40 筆／256 KiB，服務讀取預算目標 2 秒。逾時、損壞或不完整索引應回 typed error／coverage，而非空清單；若 2 秒目標無法達成，先量測並共同調整，不可由 PanWatch 拉長 timeout 代替索引。

## PanWatch 接入條件

接入前需有正式 API／OpenAPI selector 與回應 schema、版本相容規則和錯誤語意，並以離線 fixtures 與上游 API 測試證明：多個已留存期別、後次 no-report discovery、修訂 authority、部分／未知索引、標的缺少、來源不支援、損壞資料與逾時均有不同且穩定的結果。測試需證明 index 讀取不建立採集任務、不發出 MOPS HTTP request，並以單一 issuer、倒序排序、頁面上限及 cursor 驗證查詢範圍。

PanWatch 只在完整、符合所有 selectors 的回覆中預設最新 `present_readable` 期別；部分或未知索引時保留手動年／季選擇並標示可用期間未知。使用者明選缺少或逾時期別時保持原選擇，不回退到其他季度。部署驗收須重新檢查執行中服務 OpenAPI／版本確有索引能力，再使用有多期 retained fixtures 的結果驗證預設；單一 2024Q4 smoke 不足以解除此條件。

## 恢復條件

原恢復條件已達成：上游正式索引已部署；PanWatch typed client／UI 接入、回歸測試與線上唯讀檢查已完成。線上當時只有單一 retained 期別，多期與分頁情境由上游隔離 HTTP 驗收及 PanWatch fixtures 證明；這項資料量邊界記錄於卡片 Handoff。
