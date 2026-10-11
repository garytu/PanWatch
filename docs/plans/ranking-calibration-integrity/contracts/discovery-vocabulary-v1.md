# Discovery coverage / instrument authority 契約 v1

狀態：V1.3 設計契約，待實作與驗證。修訂：2026-10-10。依現 `TaiwanDiscoveryPool` metadata 延伸 audit，不新增逐股 quote requests、不擴大 scan universe。

## 1. Run identity 與 stages

新增 `discovery_runs`（run-level manifest）與 `discovery_exclusions`（bounded stage ledger）。`scan_run_id`、capture id/hash、開始／完成 UTC timestamp、requested market/venue/universe、catalog source/as-of/hash、scan limits、provider raw status、request/batch counts、selection config version 必須可回讀。Candidate、immutable decision、Dashboard/API/AI response 連到該 run；不同 run 的 counters 不可拼接。

現 `scanned_instrument_count` 來自 requested ids count，包含 failed requests，不等於成功取得有效價格。保留 legacy 欄位並註明其 attempted 語義；新欄位依 stage 命名。Universe/request/ranking 數量以 catalog 去重後的 canonical instrument ids 為單位；無合法 identity 的 catalog rows 另計 invalid_catalog_record_count，不假造 canonical id。原 response row count = canonical unique count + duplicate row count + invalid catalog record count。

| stage | mutually exclusive counts / equation |
| --- | --- |
| Catalog | `catalog_unique_count = eligible_catalog_count + catalog_excluded_count`；excluded 含 inactive、unsupported/unknown type；invalid identity 在原 response row accounting 另計 |
| Universe bound | `eligible_catalog_count = requested_universe_count + not_requested_due_to_limit_count` |
| Dispatch | `requested_universe_count = attempted_instrument_count + unattempted_instrument_count` |
| Price validation | `attempted_instrument_count = valid_price_count + invalid_price_count + missing_or_failed_price_count + pending_price_count` |
| Ranking | `valid_price_count = ranking_eligible_count + ranking_excluded_count`（例如缺 change/turnover） |
| Pool selection | `ranking_eligible_count = pool_selected_count + ranked_not_selected_count` |
| Candidate selection | `pool_selected_count = candidate_selected_count + candidate_excluded_count + candidate_not_selected_count` |
| Portfolio projection | instrument-level selected/constrained/duplicate counts 另報，不能回写 pool selected count |

每個 stage 的 id 集合須可核對，reason counters 的 sum 只取該 stage 的 primary reason，additional diagnostic reasons 不重複加總。Universe 太大未選到、未發出 request、request 失敗、invalid/stale price、排名未選、策略排除是不同原因。pre-scan exclusions 不放「scanned = selected + excluded」等式。

Run 最終 capture 後未完成 futures 不可改原 counters；記 pending 並另建 completion revision（若需要），immutable decision 綁已使用的 revision。失敗／重試 request attempts 另計，不能把同 instrument 重試當多個 universe units。

## 2. scan_coverage_state

名稱不用 research 的 `coverage_status`；API schema 含 `scan_coverage_state` ∈ COMPLETE / PARTIAL / MISSING / UNKNOWN，外加 `coverage_scope`（FULL_ELIGIBLE_CATALOG / BOUNDED_REQUESTED_UNIVERSE）與 selected/unrequested counts。

- COMPLETE：catalog identity/eligibility 完整、requested universe 全部完成且價格有效、無 pending/failure/unattempted。即使 COMPLETE，若 due-to-limit > 0 也只能說 bounded universe complete；不能說全市場完整。合法的 pre-scan type exclusions 和正常 top-N selection 不構成 price coverage 缺漏。
- PARTIAL：有可用結果，但 requested ids 有 failed/missing/invalid/stale/pending/unattempted 任一項，或 catalog 已知不完整。batch 都 attempted 不等於 COMPLETE。
- MISSING：catalog／source disabled 或無任何可用價格；保留 exact reason（包括 catalog_error、catalog_unavailable_or_empty、quote_source_disabled、no_current_eligible_prices）。
- UNKNOWN：無法重建 authority/counters 的 legacy path；不能用 defaults 填 COMPLETE。

原 pool status 與上述新增 reason/counters 均保存；不只靠 available/partial 字串做 mapping。UI/AI 顯示 scope、requested/valid/selected、unrequested/unattempted/pending、price dates、failed requests；「可用候選為零」不等同「完整市場無機會」。

## 3. Instrument 與 cost authority

現 catalog 已有 canonical `instrument_id`、`venue`、`security_type`、`is_active`，但確認存在 **不等於** 存在 ETF subtype／tax category。現 pool 的 `security_type_by_instrument_id` 只涵蓋 selected items；B 任務須在原 catalog loop 保存 audit metadata，不能事後對每股重新查詢。

凍結並一路傳遞 catalog source/version/hash/as-of、instrument id、venue、security_type、is_active、authority status。股票與 ETF 分開 risk/outcome group；`TWSE:00631L` 必須按 ETF 顯示與分組，不能把 name/suffix 當 EQUITY。是否 leveraged/inverse 要有可驗證 subtype；無 subtype 標 UNKNOWN，不宣稱已識別槓桿程度。初始 TW 普通股票 opportunity universe 只接 EQUITY，ETF 留在另列的 ETF opportunities，不佔普通股票 cap；這是 post-scan strategy eligibility 改變，原 discovery request/排序仍不變。

Cost source：`cost_model_for_market` 已以 security_type 判股票／ETF基本類別；`endswith("B")` 是 **bond-ETF exemption heuristic**，不能只以 ETF enum 取代。實作需記 `tax_category`、classification authority、`effective_from`／`effective_to` 与 `cost_policy_version`：

1. 若同一 captured catalog 有可驗證 tax/subtype field，adapter 明示 mapping 与 evidence。
2. 若沒有，上游 contract 驗證列為 PR-0B 子任務；用可版本化、具 authority/effective dates 的本地 classification snapshot 作 adapter input，不因本計畫新增逐股 quote call。不得編造上游欄名或依名稱判分類。
3. 無權威 tax category 時保留 UNKNOWN；gross 可計，net unavailable `cost_authority_missing`。不能默默套股票稅率或宣稱 bond exempt，也不能擋住其餘 EQUITY metadata/coverage 交付。
4. 費率與豁免日期須於實作時核對當時官方依據，保存 source 與 dated policy，entry/exit 各按其 session date 決定。此計畫不宣布任何現行稅法已驗證。

初始 scope 不增加 ETF subtype 研究或真實交易執行；缺權威分類只縮小可用 net 母體。catalog 最新 is_active 不能 retrospective 當歷史 active membership，PIT decision 必須使用當時已捕捉的 authority。

## 4. API compatibility 與持久化

保留現 `hot_stocks()` list caller；新增 structured result 方法／internal wrapper（items + manifest + ledger + catalog authority），由 TW strategy path 使用，不能把 list return 全域改型破壞其他市場。collector 明確傳 structured result；UI/API 增量欄位，舊 caller 不強迫解讀新 schema。

manifest、bounded ledger、當次 candidate linkage 在同一 logical run transaction 完成；原始行情 fetch 不放長 DB transaction。只儲存本次既有 catalog/scan 範圍的 bounded ids/reasons，不保存無上限任務 log。run/ledger 保留 180 天；被 immutable decision 引用的 manifest revision/hash 與必要分類證據 archive 保留，不 cascade delete。read API 可按 scan_run_id，Dashboard/AI response 附當前 decision 的 run revision。

驗收必含：inactive/unknown type pre-scan、universe cap、batch failure、missing returned id、stale/malformed price、pending future、successful empty candidate selection、full requested completion with bounded scope、ranked top-N 與 candidate exclusion；每 stage 集合與 counts 可 reconciliation，request 數量／deadline／ordering 與原 bound 相同。
