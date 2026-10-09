# twmd 使用問題修正計劃

建立日期：2026-10-09（Asia/Taipei）。依據 [使用檢查報告](../../reviews/twmd-usability-2026-10-09.md) 的前五項問題整理。

目標：讓使用者理解資料日期，正常開啟股票詳情時能取得研究資料，並能選到已留存的歷史分 K 與財報。使用者於 2026-10-10 授權依 TWUX-01 → TWUX-02 → TWUX-03 → TWUX-04 → TWUX-05 執行；驗證結果以各卡 Handoff 記錄為準。

## 任務與順序

| ID | 優先級 | 任務 | 狀態 | 依賴 |
| --- | --- | --- | --- | --- |
| TWUX-01 | P1 | [行情日期、休市背景與 AI 上下文](cards/TWUX-01-market-context.md) | completed | 無 |
| TWUX-02 | P1 | [研究讀取排程與區塊重試](cards/TWUX-02-research-loading.md) | in_progress | 無 |
| TWUX-03 | P2 | [財報可用期間與獨立載入](cards/TWUX-03-financial-periods.md) | planned | TWUX-02 的區塊選擇契約；可用期需確認 twmd 契約 |
| TWUX-04 | P2 | [歷史分 K 日期選擇](cards/TWUX-04-intraday-dates.md) | ready | 無；沿用 TWUX-01 的日期呈現規則 |
| TWUX-05 | P2 | [熱門股榜保留行情來源資訊](cards/TWUX-05-discovery-provenance.md) | ready | TWUX-01 的共用行情標示 |

建議交付順序：**TWUX-01 → TWUX-02 → TWUX-04 → TWUX-05 → TWUX-03**。財報可用期的契約確認可提前進行；不需要等上游擴充，才開始其他四項修正。

TWUX-02 負責共用排程、選擇性區塊讀取與重試；TWUX-03 重用該契約，把財報拆成獨立載入。TWUX-01 定義資料日期與價格種類的共用呈現規則；TWUX-04、05 沿用，避免不同畫面各自解釋「最新」。

## PanWatch／twmd 分工

2026-10-09 已核對本機 twmd 原始碼及契約文件；這是 checkout 能力確認，不代表執行中的容器已部署同一版本。

| 任務 | PanWatch 責任 | twmd 責任 | 是否需兩邊修改 |
| --- | --- | --- | --- |
| TWUX-01 | 保留行情 metadata，修正畫面／AI／日報日期語意 | 既有報價已提供資料日、種類與時效 | 否，現有證據支持只改 PanWatch |
| TWUX-02 | 修正共用讀取額度、公平等待、合併請求與區塊重試 | 現階段不需改 query API | 否；若剩餘延遲在上游，另以量測定位 |
| TWUX-03 | 財報獨立載入、選期、typed client 與索引消費 | 新增已留存財報的有界期別索引；量測現有財報讀取效能 | 是；獨立載入可先由 PanWatch 交付 |
| TWUX-04 | 日期選擇，接既有 coverage 查詢找可用日 | `/bars` 已支援日期；`/bars/coverage` 已支援有界日期範圍 | 否，不需要為日期選擇新增上游 API |
| TWUX-05 | 保留 collector 已有欄位並呈現在榜單 | 既有行情 metadata 可沿用 | 否 |

twmd 現有 `/api/v1/financial-statements` 必填 fiscal_year／fiscal_quarter，只能讀明確季度，尚無對外的可用期清單。TWUX-03 的兩邊順序是：**共同確認索引契約 → twmd 提供唯讀索引及測試樣本 → PanWatch 接入與選期 → 聯合唯讀驗收**。契約名稱與路徑待設計，不把建議 endpoint 當成既有功能。

twmd 的 `get_financial_statement_state` 目前先呼叫 `_financial_state_all`，再取指定 scope。需量測這條路徑是否造成慢查詢；若確認是瓶頸，再規劃有界的指定期別讀取／索引，保留來源 authority、修訂與損壞資料的錯誤語意，不僅靠 PanWatch 拉長 timeout。

來源：[財報契約](/Users/garytu/works/tw-market-data-main/docs/financial-statements-api.md)、[財報讀取](/Users/garytu/works/tw-market-data-main/src/twmd/storage/sqlite.py:5077)、[分 K／coverage 契約](/Users/garytu/works/tw-market-data-main/docs/intraday-bars.md)。本次只補 PanWatch 計劃，沒有修改 twmd 程式。

## 共通邊界

- 休市本身不是故障。區分產製日期、行情日期、報表期間與下一交易日；日曆未知時明示未知。
- 保留 TWSE／TPEX canonical ID、provider、來源、單位及 coverage。缺少資料、未成交、不支援、逾時與過期不得合併成零或「沒有資料」。
- 過期盤中報價仍是過期盤中報價，不改標成官方收盤價；歷史資料不因此變成可交易行情。
- 沿用 TwmdClient／provider 與研究服務，不直接讀 twmd 資料庫，不靜默改用其他來源。
- 查詢、日期切換、重試不觸發採集、補資料或訂閱。保留讀取額度、總期限、查詢範圍與快取隔離。
- 不擴大條件選股候選池，不另做整頁研究版面重設，不變更模擬交易、策略或通知規則。檢查報告第六、七項另行規劃。
- 本計劃不變更原 [資料接入計劃](../twmd-integration/README.md) 的 PW 任務狀態，也不把這些修正視為 PW-14 盤中驗收完成。

## 實作與驗證方式

開始實作前，重讀指定卡片與 AGENTS.md、確認目前程式及上游契約、保護工作目錄既有變更。在 `codex/` 分支交付，提交與 PR 遵守專案約定。若指定使用 execute-task 流程，再依該技能執行；寫計劃本身不啟動派工。

每卡以固定時間、離線 fixtures 和可控制的慢速讀取驗證失敗情境；相關測試通過後再做有界唯讀實測。前端變更需執行相關 Vitest、TypeScript 與正式建置；後端變更需執行卡片列出的 pytest 回歸。建立／更新 PR 前執行 `git diff --check`。

前端驗證在 `frontend/`、專案固定 Node／pnpm 環境執行：

```sh
pnpm exec vitest run <該卡相關測試>
pnpm exec tsc -b
pnpm build
```

跨模組修正完成後，補跑：

```sh
.venv/bin/python -m pytest -q tests/test_taiwan_integration.py tests/test_taiwan_research_api.py tests/test_taiwan_research_service.py tests/test_taiwan_discovery.py tests/test_discovery_routing.py packages/marketdata/tests/test_twmd.py
```

若在隔離容器測試，先初始化測試需要的認證與資料庫。測試檔名若標示「新增」，代表待實作，不表示檔案已存在。

完成卡片時記錄實際修改、測試命令／結果、唯讀實測的服務版本與資料日期，以及剩餘限制。功能完成後的 Docker 重建、切換與盤中驗收另依當次指令執行；本次沒有重建或部署。
