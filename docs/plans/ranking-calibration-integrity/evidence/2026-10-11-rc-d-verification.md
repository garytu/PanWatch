# RC-D 本地審查與驗證（2026-10-11）

Checkout: `codex/rc-d-point-in-time-outcomes`，assignment commit `e66a6a0`；前置 PR-0A identity `57c12c1` 與 RC-B `34ef157`。Implementation worker: `rc_d_worker`（GPT-6 Luna/max）。Coordinator 獨立審查、補修與驗證。本文記錄本地 fixture/build 結果，不是正式資料庫 migration、部署或 calibration activation 證據。

## 交付與審查結果

- 新 `strategy_outcomes_v2` 和 append-only `outcome_evaluation_attempts`，按 immutable decision / population / session horizon / evaluator 唯一；舊 rows 不改寫。終態 stock facts 受 DB trigger 保護，benchmark 可獨立補齊；explicit evaluator revision 與舊版本共存。
- signal-forward、candidate-forward 和 simulated execution 分開。Forward 用次 session close 和其後 h sessions close；execution 用三 session limit window、開盤 invalidation、overnight gap-first exit、dual-touch ambiguity 和 h-th session expiry。每日 bar 只提供 session precision；不編造 intraday timestamp。
- Required sessions 來自覆蓋完整區間的 calendar；校驗精確 daily dates、OHLC、provider/contract、raw price basis、content/capture identity、final marker 與 receipt/source clocks。缺失資料落 insufficiency，尚未到期落 pending；1h/6h/24h retry，同 row 觀察最長 30 calendar days。終態與其他 revision 不占用 due retry 的批次額度。
- Forward net 是明示 fixed-notional dated bps；execution net 是具名一個 configured TW lot 的兩腿 cash-flow PnL，保存 quantity/lot/notional 與各腿成本。Entry、exit 各自使用 dated instrument authority；沒有 authority 不猜分類或費率，gross 存續、net NULL。
- TWSE/TPEX 對應精確 session 的 TAIEX/TPEX raw price index。Benchmark 缺日為 unavailable，可重試，不改 stock facts/hashes；過期、future/pre-close capture、錯誤來源、非有限數值或不可比 basis 不能產生 relative return。
- Candidate/strategy stats、dashboard 和 evaluation API 保留 legacy 欄位並加上明示 basis；v2 按 market/population/horizon/evaluator/score version 分區，分列 raw rows、distinct units、decision dates 與 gross/net/benchmark/relative 分母。UI 的舊 3 日勝率標為舊口徑。Legacy backtest next-open/stop-first 與 calendar helper 行為不改，export metadata 明示不同 policy。
- Manual/scheduled maintenance 只有成功提交 v2 評估後才呼叫後續 rebalance/calibration；failure、missing commit、running lock 均跳過。既有 containment tests 證明 FROZEN 與 pin 持續生效，沒有註冊合格 v2 calibration evaluator 或自動解凍。

獨立審查修正包括 expiry session、entry/exit 狀態分離、source/final marker 丟失、live fetch clock、bounded retry deadline、revision/horizon queue fairness、forward/execution 成本分母、benchmark provenance/價格 basis、API compatibility 與統計單位。Coordinator 補測真實 calendar primitive、local closure hash、populated pre-129 upgrade/ORM parity、兩個 scheduler 入口的 commit/failure 順序，以及實際 candidate refresh 的封存生命週期。

實際 fixture 證明：每日 candidate score/entry plan 被修改後，同日空 refresh 刪掉 projection；舊 capture/hash/score/entry plan 仍保持原值。Evaluator 隨後直接讀 archive，產生 archived primary horizon 的合法 gross=0，且 decision PIT status 保持 `UNVERIFIABLE`。缺少決策時來源證據不能因後驗價格完整而被回填成 verified。

## 可重跑的隔離驗證

`tests/run_isolated_pytest.py` 在 pytest collection 前將全域 engine 和 SessionLocal 綁到 disposable SQLite，啟用 foreign keys/WAL，並建立已設定認證的無效登入 marker，滿足舊 protected-route tests 的 fixture 假設。測試自行建立的 in-memory/file DB 仍維持獨立。正式 `data/panwatch.db` 沒有執行新 migration 或 outcome writes。

第一輪空隔離庫 broad run 為 1,593 passed / 3 skipped / 4 failed：四項舊 API tests 預期密碼已設定，空庫則按照現有初始設定允許存取。加入隔離庫的 configured-auth fixture 後，受影響 15 tests 通過；未修改 runtime authentication。

Final broad backend command（repository root）：

```sh
.venv/bin/python tests/run_isolated_pytest.py -q tests packages/marketdata/tests
```

結果：**1,620 passed / 3 skipped / 14 warnings**（31.65s）。Warnings 為既有 Pydantic/ctypes deprecations。

Final relevant regression（full run 後再加入 benchmark future/pre-close/float-overflow assertions，執行此組）：

```sh
.venv/bin/python tests/run_isolated_pytest.py -q tests/test_point_in_time_outcomes.py tests/test_outcome_integration.py tests/test_discovery_coverage.py tests/test_calibration_integrity.py tests/test_factor_calibration.py tests/test_factor_calibration_loop.py tests/test_factor_eval.py tests/test_factor_scoring_weights.py tests/test_factor_weights.py tests/test_factors_api.py tests/test_strategy_outcome_efficiency.py tests/test_strategy_signal_candidate_identity.py tests/test_backtest.py tests/test_paper_trading_position.py tests/test_paper_trading_allocation.py tests/test_discovery_routing.py tests/test_taiwan_discovery.py tests/test_pw13_evidence_inventory.py packages/marketdata/tests/test_twmd.py
```

結果：**250 passed / 12 existing warnings**（11.22s）。涵蓋 session/final/source evidence、zero/missing/expiry/gap/ambiguity、persistent retry/deadline、revision coexistence、benchmark-only retry without stock fetch/hash mutation、dated costs、archive/projection deletion、schema upgrade、scheduler ordering 和 FROZEN containment。

Frontend final commands（`frontend/`）：

```sh
/Users/garytu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/typescript/bin/tsc -b
/Users/garytu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/vite/bin/vite.js build
/Users/garytu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/vitest/vitest.mjs run
```

結果：TypeScript 與 build 通過；**28 files / 127 tests passed**。使用 bundled Node 24.19.0 執行已安裝的工具；repository pin 為 Node 24.14.0 / pnpm 9.15.9。原 pnpm shim 嘗試解析 pinned package，因無法連線驗證 identity 而失敗，故未宣稱 pinned pnpm invocation 成功。未更新 dependencies/lockfiles。既有 React Router/key 與 Browserslist warnings 不阻擋驗證。

`git diff --check` 與 final staged `git diff --cached --check` 通過；新檔案也在 staged whitespace check 範圍內。僅提交本任務 paths；既有 `docs/taiwan-market-support.md` 及其他 unrelated untracked files 保留。

## 邊界與後續

- Stock 初始支援 raw/unadjusted price reference，不是 total shareholder return；缺 corporate-action source/version 的 adjusted series 為 `stock_adjustment_basis_unsupported`。HK/US weekday holiday calendars、TW/CN 未覆蓋或 fallback 區間保留不足資料；不退回 calendar-day calculation。
- Benchmark mapping 僅 TWSE/TPEX；不支援的 market/venue 明示 unavailable。TW execution 有 versioned configured-lot policy；其他 market 沒有 quantity authority 時 net 為 `invalid_explicit_cost_policy`。Unknown instrument/tax/quote provenance 不被推測成合格資料。
- Candidate decision captures 的 rank-time provenance 仍 UNVERIFIABLE；即使後驗價格完整，也不符合 calibration eligibility。沒有足量新 sessions、floor/confidence 或 shadow report，不能宣稱可自動調權。
- RC-D 已完成本地實作與驗證，RC-C 現為 ready 但尚未開始。RC-A PR-3 保持 gated；calibration 仍 FROZEN。No deployment、activation、真實成交改造或 upstream rollout。
