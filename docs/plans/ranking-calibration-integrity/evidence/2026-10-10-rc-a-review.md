# RC-A 審查與修正 — 2026-10-10

審查基準：`RC-A-calibration-integrity.md` 與 ranking/outcome V1.3 契約。審查開始時分支為 `codex/rc-a-calibration-integrity`、HEAD 為 `7142e4c`。以下為本地修正與實際驗證，尚未提交或部署；不代表市場已符合恢復條件。

## 已修正

| 問題 | 修正與驗證 |
| --- | --- |
| 套用端以最強因子的 readiness 替代目標因子，並跳過 ALL 市場分層檢查 | 套用端重新使用目標專屬 `evaluate_readiness`；無效因子與單市場 ALL cohort 不再寫入。ALL 有不合格的參與市場時也拒絕 |
| 重查 mode/pin/config 可能沿用 ORM 快取 | 取得 SQLite 寫入 reservation 後 expire，再重查；另一連線改權重、pin 或 FROZEN 時均拒絕 |
| 任意 policy version 可進 application claim | 僅接受目前支援的 policy，拒絕 caller 自創版本；非有限權重與 target mismatch 亦拒絕 |
| 直接 service 發生例外後，caller 再 commit 可能留下權重、claim 或 history | service 自行 rollback；注入第二個 target 寫入故障後沒有半套批次。審計統計使用重新驗證的 cohort/readiness |
| 手動 factor API 繞過 FROZEN | 手動權重也走相同 service gate，回傳 mode/status/readiness。pin-only 與 auto 開關仍可獨立設定；拒絕的混合請求不部分套用 flags |
| v2 標籤可讓舊 evaluator 的 rows 取得資格 | production 的 session evaluator 支援集合保持空白，回報 `SESSION_EVALUATOR_UNAVAILABLE`。只有測試明示註冊 synthetic evaluator；RC-D adapter 尚未交付 |
| 缺失 available/receipt time 被補成 decision/capture time | 保留 NULL；aware timestamps 先轉 UTC 才存入 SQLite，未知 PIT 證據不冒充已知 |
| canonical 以資料庫插入順序與 hash 決定，未檢查 eligibility，strategy 可回讀 live catalog | 依 decision time、factor strategy-code tie-break 與 immutable id 選擇；要求明示 eligible；strategy 只讀 sealed catalog authority，不用現值補歷史 |
| capture-prefix 比對可重用其他 capture；catalog 或 entry plan 修訂未納入身分 | 限定原 capture 與其數字 revision；catalog authority、entry/stop/target、symbol、regime multiplier 納入封存內容與身分 |
| capture 失敗可能留下 header/items | savepoint 隔離 capture，SQLite 明示外層 BEGIN 避免 release 提前 commit；資料轉換與 commit 故障均不留下半套快照 |
| production capture 缺時間、可回讀設定與約束前 reference set | flush 後封存真實 signal ids，記錄 capture/decision time、實際權重 payload/hash、candidate ids、reference item ids/hash 與 entry plan；capture 移到 portfolio constraints 前。未知來源時間仍為 UNVERIFIABLE |
| pinned 因子的 proposal 可拒絕其他合格因子整批 | 已知 pinned/off 或 readiness 不合格因子不提 proposal；寫入交易仍重查 flags，並保留批次原子性 |
| ALL bootstrap 把同日不同市場當獨立抽樣 | 以 decision date 為 block，同日市場一起進 bootstrap |
| baseline 未保存 history 讀取快照 | 新具名 baseline 保存設定 payload、history snapshot/hash；既有 archive 不回寫。開始 planning 時初始化 enabled markets 與 ALL 的 persisted modes |

新增 integration 覆蓋：兩個 worker 競爭同一 application、重開連線後重試、跨連線 stale config/pin/mode、批次 crash、capture commit failure、缺 PIT available time、policy identity、canonical order、baseline history 與 pin sibling。

## 驗證

審查開始時相關回歸：93 passed。新增的第一組 9 個案例全部重現原實作缺陷。

最後執行任務 README 的 backend regression set，加上 calibration integrity integration：

```sh
.venv/bin/python -m pytest -q --disable-warnings tests/test_calibration_integrity.py tests/test_factor_calibration.py tests/test_factor_calibration_loop.py tests/test_factor_eval.py tests/test_factor_scoring_weights.py tests/test_factor_weights.py tests/test_factors_api.py tests/test_strategy_outcome_efficiency.py tests/test_strategy_signal_candidate_identity.py tests/test_backtest.py tests/test_paper_trading_position.py tests/test_paper_trading_allocation.py tests/test_discovery_routing.py tests/test_taiwan_discovery.py tests/test_pw13_evidence_inventory.py packages/marketdata/tests/test_twmd.py
git diff --check
```

結果：196 passed，12 個既有 Pydantic deprecation warnings；whitespace 檢查通過。新 migration/concurrency/crash 測試使用 temporary DB。未修改前端，本次未執行 frontend build。

## 尚未交付的恢復條件

RC-A 仍不能標示完整完成或允許 production restart。RC-D 的 immutable v2 outcome producer、實際 session/calendar/bar provenance 與其 adapter 尚未交付；不能將支援集合改成接受常數標籤來代替。

PR-3 還需要 time-ordered shadow replay、baseline/candidate 排名與 coverage/cost 診斷、分市場啟用流程，以及具名設定的 audited restoration。現有 bootstrap/floors 與 synthetic ACTIVE 測試不構成這些交付或績效驗證。既有缺少 provenance/history 的 archive 不做自動歷史補齊；其限制需由明示的新具名 rollout archive 記錄。
