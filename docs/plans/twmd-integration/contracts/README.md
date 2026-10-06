# twmd query contracts for PW-01

Captured API evidence lives in [`2026-10-06.json`](../../../../packages/marketdata/tests/fixtures/twmd/captured/2026-10-06.json). It contains bounded, read-only HTTP GET observations from `http://127.0.0.1:8000`, including the request URL, status, latency, selected response fields, and the successful company-profile bodies. A supplemental [2026-10-07 bars capability projection](../../../../packages/marketdata/tests/fixtures/twmd/captured/bars-capabilities-2026-10-07.json) records `live_collection_supported=true`, `live_collection_configured=false`, and `live_collection=false` (HTTP 200, 16.522 ms, 00:10:54 Taipei). Support, configuration, and verified live delivery are separate capabilities. The synthetic edge cases in [`offline_edge_cases.json`](../../../../packages/marketdata/tests/fixtures/twmd/synthetic/offline_edge_cases.json) are separately labelled and are not live observations.

## Version and observation boundary

The inspected upstream `tw-market-data` checkout was clean at commit `3acd67ffd98bbcf1713f9484d8a7b77871db6ade`. The deployed query API's observed OpenAPI document reported title `Taiwan Market Data Query API` and version `0.1.0`; it did not expose a commit identifier. The checkout commit therefore describes the source reviewed for this contract, not a verified deployed build identity. At 2026-10-06 23:52 Taipei, the readiness response reported `ready`, `data_ready=true`, readable storage, and TWSE/TPEX daily-price partitions through 2026-10-06. These observations apply to this service and date only.

The GET evidence was collected on 2026-10-06 (Asia/Taipei). Regular endpoint reads returned HTTP 200 in 10.875–150.597 ms, except readiness at 9,403.8 ms. Invalid current-day upper bounds and a future revenue month returned HTTP 400. The company-profile endpoint timed out twice per venue with a 10-second request bound. A later bounded read returned complete HTTP 200 responses for `TWSE:2330` in 15,518.640 ms and `TPEX:5347` in 15,595.329 ms. Both profiles were present and qualified. The extended reads establish response shape, not a latency guarantee; PanWatch's current `TWMD_TIMEOUT_SEC` default is 5 seconds, so this endpoint cannot yet be treated as reliably reachable through the existing client default.

The reviewed source entry points were `src/twmd/query/api.py`, `src/twmd/query/service.py`, and the upstream `company-profiles-api.md`, `monthly-revenues-api.md`, `institutional-flows-api.md`, `tpex-flow-valuation-api.md`, and `financial-statements-api.md`. The deployment acceptance document describes the earlier MD-22 release; it is historical evidence, while the dated HTTP fixtures describe the service observed here. The source confirms the endpoint-specific differences below rather than assuming that all datasets share one response or date policy.

## Canonical identity and security type

Use the complete instrument ID as the identity key. `TWSE:2330` and `TPEX:5347` are separate examples; never join or cache on the numeric code alone. The live catalog returned these active records:

| Canonical ID | Venue | Security type | Active | Handling |
| --- | --- | --- | --- | --- |
| `TWSE:2330` | `TWSE` | `EQUITY` | `true` | Equity |
| `TPEX:5347` | `TPEX` | `EQUITY` | `true` | Equity |
| `TWSE:00878` | `TWSE` | `ETF` | `true` | ETF; keep distinct from an issuer equity |
| `TPEX:006201` | `TPEX` | `ETF` | `true` | ETF; revenue API returns `qualification=unsupported_etf` |
| `TPEX:700019` | `TPEX` | `WARRANT` | `true` | Exclude from the equity/ETF issuer workflow |

The `instruments` response, not a symbol pattern or market suffix, determines venue, activity, and security type. Bare symbols that resolve to multiple canonical IDs require an explicit venue. In particular, a broad TPEx catalog must not be interpreted as a list of ordinary shares.

## Endpoint matrix

| Dataset | Read endpoint and selectors | Date bounds | Response values and units | Coverage, presence, provenance, and revisions |
| --- | --- | --- | --- | --- |
| Instrument identity | `GET /api/v1/instruments/{canonical_id}`; optional catalog filters `venue`, `security_type`, `is_active` | No date selector | `instrument_id`, `symbol`, `venue`, `security_type`, `is_active` and catalog metadata are typed JSON strings/booleans/dates | A selected instrument record is not a dataset-wide membership guarantee. Keep `TWSE:` and `TPEX:` on every key. |
| TWSE valuation | `GET /api/v1/valuations?instrument_id=TWSE:2330&start=YYYY-MM-DD&end=YYYY-MM-DD` | Paired inclusive dates, `start <= end`. The inspected TWSE path has no TPEx-style 2024 floor, 366-day cap, or current-Taipei-day cutoff; do not apply those limits by analogy. | List of `ValuationResponse`. `close_price`, `pe_ratio`, `pb_ratio`, and `dividend_yield_pct` serialize as exact decimal strings or `null`; prices are TWD, ratios are multiples, yield is percent. | No coverage header, capture ID, or revision is returned on this branch. `[]` cannot distinguish absent selected issuer from missing dataset coverage. The live Oct 2 and Oct 5 responses both contained rows. |
| TPEx valuation | Same route with `instrument_id=TPEX:<four digits>` | Paired inclusive dates; product floor 2024-01-01; at most 366 calendar days; `end < current Asia/Taipei date`. | List of `TpexValuationResponse`; decimal values are strings or null. The source's `close_price` is null; dividend currency can be `unspecified`. | `X-TWMD-Schema-Ready` and aggregate `X-TWMD-Coverage` headers describe schema and range-level available/missing counts. A returned row contains source contract/URL, capture ID, receipt, payload hash, and semantic `revision`. The range-level `selected` header collapses dates; retain per-date query scope when interpreting it. |
| TWSE institutional flow | `GET /api/v1/institutional-flows?instrument_id=TWSE:2330&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` | Paired inclusive dates; floor 2024-01-01; at most 366 calendar days; `end_date < current Asia/Taipei date`. | `InstitutionalFlowsResponse`; share fields are JSON integers or null, in native `shares`. All foreign non-dealer, foreign dealer, trust, dealer, and publisher-total fields remain separate. | One `coverage` entry per calendar date. `coverage.record_count` describes the whole-market report; `data` contains only the selected issuer. `acquired_at` is coverage receipt evidence; `first_observed_at` is row observation evidence, even when their current values coincide. TWSE can report `EMPTY` only for its recognized official no-data response; `MISSING` means no validated partition. Rows have no semantic revision field. |
| TPEx institutional flow | Same route with `instrument_id=TPEX:...` | Same completed-date bounds as TWSE institutional flow. | `TpexInstitutionalFlowsResponse`; seven buy/sell/net triples and total are JSON integers in shares. Preserve combined and component fields; foreign-dealer values are not added twice. | Per-date coverage includes `status`, market `record_count`, `selected_instrument_presence`, capture/source evidence, receipt, hash, and row revision. An `AVAILABLE` full report with selected presence `absent` is not zero flow. `MISSING` coverage has no receipt or capture. |
| Company profile | `GET /api/v1/company-profiles?instrument_id=TWSE:...` or `TPEX:...` | Exactly one canonical ID. Latest-only; `as_of`, `report_date`, duplicate, or extra selectors return 400. | One `CompanyProfilesResponse`. Capital and par-value amounts are exact strings; issued/private/preferred share counts are integers or null; units are explicit. The live profile has `report_date`, source contract, receipt and semantic revision. | `schema_ready` and `coverage_status` describe the optional schema and latest whole snapshot. `latest_snapshot_presence` is independently `present`, `absent`, or `missing`. Qualification independently distinguishes `qualified_issuer`, `unsupported_etf`, `unsupported_warrant`, and unresolved issuers. A complete snapshot is not a guarantee that a particular issuer appears. |
| Monthly revenue | `GET /api/v1/monthly-revenues?instrument_id=TWSE:...&start_month=YYYY-MM&end_month=YYYY-MM` or `TPEX:...` | Exactly one of each selector; duplicates or extra selectors return 400. Required paired inclusive months; floor 2024-01; at most 120 months; future Taipei months are rejected. Current Taipei month is queryable if retained. | One `months` entry per requested month. Publisher amount and percentage fields are strings or null; amounts are interpreted as thousand TWD by source notes (the unit is an inference), and publisher percentages are not recomputed. | `coverage_status=AVAILABLE` means at least one retained source report is in range. Each month independently reports `present`, `not_in_captured_report`, or `missing`; a missing month is not zero. Coverage separates MOPS/TWSE and TPEx captures. Rows preserve source, report period, acquisition date, UTC receipt, content hash, and semantic revision. The response's `served_at` is a separate UTC serving time. TPEx only has latest-snapshot acquisition; a query range does not create historical coverage. |

## Shared status and evidence rules

Represent each requested block with its own data, status, reason, and evidence. The following vocabulary preserves the distinctions already made by the API:

| Status | Meaning | Example reason |
| --- | --- | --- |
| `available` | A validated selected row or usable selected snapshot exists | `selected_record_present` |
| `partial` | The request has some available periods and at least one missing or omitted period | `some_months_missing` |
| `missing` | The selected period has no retained dataset partition/report | `coverage_missing` |
| `absent` | The report is available, but it omits the selected issuer | `issuer_absent_from_available_report` |
| `empty` | A source explicitly reports a recognized authoritative empty result | `source_report_explicitly_no_data` |
| `unsupported` | A preflight identifies a selected security type outside the dataset's subject area | `unsupported_etf`, `unsupported_warrant` |
| `stale` | A value exists but fails the caller's explicit freshness rule | `freshness_window_exceeded` |
| `error` | The query failed, timed out, or returned an invalid service response | `http_503`, `timeout`, `invalid_response` |
| `unknown` | The API result does not provide enough evidence to classify coverage or selected presence | `coverage_not_returned`, `selected_presence_unreported` |

PW-01 establishes this research-layer contract for PW-04; no generic framework is added and existing endpoints retain their native statuses. Preserve API-native statuses and reasons in evidence. A block should carry a concrete evidence envelope with explicit nulls for information the endpoint does not provide. For example, this synthetic research-layer shape keeps the API selector and source period separate from receipt and serving times:

```json
{
  "data": [{"data_month": "2026-08-01", "monthly_revenue": "514805337"}],
  "status": "partial",
  "reason": "some_requested_months_missing",
  "evidence": {
    "instrument_id": "TWSE:2330",
    "endpoint": "/api/v1/monthly-revenues",
    "selectors": {"start_month": "2026-07", "end_month": "2026-08"},
    "source_contract": "mops_t21_sii_monthly_revenue/v1",
    "period": {"data_months": ["2026-07-01", "2026-08-01"]},
    "source_report_date": "2026-10-04",
    "publication_time": null,
    "source_received_at_utc": "2026-10-04T13:09:22.189587Z",
    "served_at": "2026-10-06T15:52:17.462128Z",
    "units": {"revenue": "TWD thousands (inferred)"},
    "dataset_coverage": "AVAILABLE",
    "selected_instrument_presence": "present",
    "capture_id": "synthetic-monthly-capture",
    "revision": 2,
    "payload_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  }
}
```

For a range with multiple receipts, revisions or source contracts, preserve provenance on each returned row and coverage entry; the one-capture envelope above is only an example. A block-level scalar must be null or explicitly marked mixed when values differ. Keep per-period presence, acquisition date, and first-observed time where provided; none can be inferred from a block-wide receipt. Profile absence can still supply retained data, with its older report/receipt and newer snapshot evidence both visible.

The captured HTTP fixtures contain complete API responses; constructed research envelopes and edge responses are stored separately and explicitly labelled synthetic. In particular:

- `schema_ready=false` with HTTP 200 and `MISSING` is an optional schema that is wholly absent. A partially present or corrupt schema is an HTTP 503 error, not empty coverage.
- `AVAILABLE` dataset coverage and selected-issuer presence are independent. The TWSE flow sample has 18,610 market rows and one selected row; the TPEx report has 910 market rows and an explicit selected-presence flag. A future selected-absence case can still have `AVAILABLE` dataset coverage.
- `absent` is a selected-issuer result over available market coverage; it is not `empty`, `missing`, or zero. A company profile absent from the newest complete snapshot can still return the previously retained issuer profile.
- `empty` applies only to the recognized TWSE institutional-flow no-data response. TPEx has no proven authoritative empty report; an uncaptured report is `MISSING`.
- An empty TWSE valuation list has no coverage field or header. Its coverage status and selected presence are `unknown`; do not turn that response into the source-authoritative `empty` state.
- Unsupported classification belongs in a catalog-aware research preflight. The live monthly-revenue endpoint accepts active `TPEX:006201` and returns `qualification=unsupported_etf`; profile and revenue endpoints document security qualification. A six-digit TPEX warrant does not fit the valuation endpoint's four-digit issuer selector and would be a selector error, not an upstream `unsupported_warrant` result. Preserve HTTP 400 as an error when the API is called directly.
- A month-range revenue result can have `coverage_status=AVAILABLE` and a missing month. In the captured July–August 2026 range, July is `missing` while August is `present` for both selected issuers.
- A valid HTTP 200 with `data: []` or `months[].row: null` is an API state. HTTP 400/503 and timeouts are failures. Do not catch a transport exception and relabel it as an empty successful response.
- Keep a research-level `stale` status separate from API coverage. A retained response can be complete and still exceed a consumer's freshness window; the freshness rule and its clock must be explicit evidence.
- Keep `trade_date`, `data_month`, profile `report_date`, acquisition date, source receipt (`received_at_utc`/`acquired_at`), first row observation (`first_observed_at`), API `served_at`, and publisher publication time as separate fields. Publication time is unprovided for monthly revenue; the report date is not a publication timestamp. Absence of `served_at` on an endpoint means unknown, not request time.
- TWSE total is foreign non-dealer + trust + dealer reported net; TPEx total is foreign excluding dealer + trust + combined dealer net. Combined foreign, foreign-dealer, dealer-own and dealer-hedging fields are not extra amounts to add to those totals. Preserve source nulls; do not manufacture a total or a five-trading-day sum from incomplete calendar-date coverage.
- Keep exact decimal strings and integer share counts at the source boundary. `Fundamentals` has float fields and can provide compatibility values for fields whose source meaning matches (for example P/B and a verified yield); the API's generic `pe_ratio` must not be assumed to mean `pe_ttm`. Do not discard exact source strings from evidence. `CapitalFlow` is the compatibility type for the existing flow surface (`flow_kind="institutional_shares"`, `unit="shares"`, `trade_date` and net-share fields); do not reinterpret large-order cash fields as institutional flow. Preserve venue-specific component fields outside that narrow compatibility mapping. Monthly revenue and profile have no safe one-row mapping into `Fundamentals`; PW-03 should use period-aware profile/revenue types instead of treating issuer revenue as a dated quarterly fact.

## Fixed date context

Any future-date or completed-period check in PanWatch must use one captured `today_taipei` value from `Asia/Taipei` for the entire request. Do not use UTC's calendar date or a moving wall clock between fields. This applies to both flow branches, TPEx valuation, and revenue's future-month check. Company profiles have no caller-supplied date. The current TWSE valuation path has no matching end-date rule in the inspected source, so its date policy remains endpoint-specific; a TWSE valuation query ending on the current date was not captured. The Oct 6 requests show HTTP 400 for a flow end date and TPEx valuation end date equal to 2026-10-06. A future revenue month also returned HTTP 400.

## Type reuse and downstream prerequisites

PW-01 sets these prerequisites for later work; it does not implement provider routing.

PanWatch's current [`TwmdClient.get`](../../../../packages/marketdata/src/marketdata/vendors/twmd.py)
calls [`market_get`](../../../../packages/marketdata/src/marketdata/http.py) with
`parse="json"`. `market_get` returns only the parsed body, catches HTTP/network
exceptions, and returns `None`; this client therefore cannot expose response
status or the TPEx valuation coverage headers. Existing call sites commonly
turn `None` into `{}` or `[]`, which can make a transport failure look like an
empty dataset. PW-02 must introduce a narrow response/error path before these
reads are wired into research. PW-03 must also preserve errors and choose an explicit bounded profile timeout/cache policy; the current 5-second default was below both successful response latencies. The contract sample does not imply that this
transport behavior is already fixed.

| Task | Contract prerequisite |
| --- | --- |
| PW-02 valuation and flows | Map valuation values into compatible `Fundamentals` fields and existing flow surfaces into `CapitalFlow`; retain exact strings/integers and native venue evidence. Preserve TPEx response headers and distinguish transport errors from empty responses. |
| PW-03 profile and revenue | Build separate issuer-profile and month-period types. Respect latest-only profile scope, revenue source differences, exact numeric strings, month coverage, and unknown publication time. |
| PW-04 research and AI | Return per-block data/status/reason/evidence; never assign one latest date to the whole research result. |
| PW-05 freshness acceptance | Compare period date, original receipt/acquisition, and serving time separately; measure the profile latency behavior before selecting a client timeout. |
| PW-06 discovery | Filter the active catalog by venue and security type. Include `EQUITY` and explicitly considered `ETF` candidates; exclude warrants from issuer/equity ranking. |
| PW-07 margin and shareholders | Define each source's unit and coverage separately; flow contracts here do not establish margin-lot or TDCC shareholder coverage. |
| PW-08 broker flow | Establish branch coverage, price/quantity availability, and provenance separately; daily institutional flow is not branch trading identity. |
| PW-09 material information | Keep current and historical announcement sources and publication/receipt times separate. |
| PW-10 financial statements | Honor the source's supported issuer, consolidated scope, fiscal period, and statement coverage; do not infer completeness from profile or revenue coverage. |
| PW-11 benchmarks | Verify each benchmark's own source, units, daily coverage, and selected series before using market-wide context. The plan baseline had missing TAIEX/TPEX bars and unconfigured TAIEX live quotes; acquisition and independent acceptance remain prerequisites. |
| PW-12 corporate actions | Treat realized ex-right results as event annotations, not a complete dividend calendar or adjustment stream. |
| PW-13 historical research | Preserve as-of versus currently retained evidence. Receipt, publication and report dates must not be substituted for one another. |
| PW-14 live data | Establish trading-session arrival, age and recovery evidence separately; historical EOD samples do not prove live delivery. The supplemental capability sample reports support but unconfigured/disabled collection; enablement and trading-session acceptance remain separate upstream work. |

## Reproducing the bounded audit

Run `.venv/bin/python -m pytest -q packages/marketdata/tests/test_twmd_contracts.py` to validate the retained fixtures without contacting any service. The tests resolve files relative to their own path and use frozen Taipei clock witnesses, including an instant whose UTC calendar date differs. These witnesses specify upstream bounds; PW-02/PW-03 must test their actual provider validators when implemented. For a new smoke audit, use only the exact GET URLs saved in the captured fixture against the configured query API, record observation time, status, latency and safe headers, and retain new dated evidence rather than overwriting the old sample. Profile attempts used an initial 10-second bound and one retry; the independent successful reads used a 60-second upper bound. Never trigger upstream acquisition or consult the live SQLite file to fill a missing result.
