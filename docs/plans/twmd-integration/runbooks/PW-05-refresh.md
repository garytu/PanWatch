# PW-05 source refresh and recovery runbook

This runbook describes the upstream `tw-market-data` ingestion jobs and their
read-only PanWatch consumers. Query endpoints read retained local SQLite data;
they never fetch publisher data. Freshness ages describe the selected row's
period and original receipt. They do not establish a publisher SLA or a market
calendar.

## Venue, frequency, source release, and bounds

| Data | Venue and source | Expected source scope | Control-plane state observed 2026-10-07 |
| --- | --- | --- | --- |
| Valuation | TWSE adapter `twse_valuation`; TPEx adapter `tpex_valuation` | One completed-date report per venue. TWSE currently has a daily schedule at 18:30 Asia/Taipei. TPEx is operationally manual-only: no schedule was present. TPEx's report accepts paired inclusive dates from 2024-01-01, up to 366 calendar days, and rejects current/future Taipei dates. Use a one-day range for a correction; widen only to the smallest verified gap. | TWSE control job ID `twse-valuation` and schedule ID `twse-valuation-daily` present/enabled. TPEx job present with no schedule. |
| Institutional flows | TWSE `twse_institutional_flow` T86; TPEx `tpex_institutional_flow` Daily/EW | One completed-date report per venue, integer share units. TWSE accepts paired inclusive date bounds from 2024-01-01 through yesterday in Taipei, capped at 366 calendar days; TPEx has the same product floor and cap. The TPEx EW report includes ETFs and excludes warrants and bull/bear securities. | Both jobs present; no flow schedules present. Manual-only operational cadence. |
| Company profile | TWSE latest listed-company snapshot; TPEx latest OTC-company snapshot | Latest whole-market snapshot only. No historical date selector or historical profile archive. A retained issuer profile keeps its original receipt; the latest whole-snapshot receipt is separate evidence. | Both jobs present; no schedules present. TPEx adapter rejects scheduled runs. |
| Latest monthly revenue | TWSE latest feed; TPEx latest-only report | Latest retained report only. The report's `data_month`, publisher `report_date`, and local UTC receipt are distinct. TPEx has no archive selector and rejects scheduled runs. | Both jobs present; no schedules present. Manual-only operational cadence. |
| MOPS monthly-revenue archive | TWSE issuers | Paired inclusive `start_month`/`end_month`; at most 12 months per run, from 2024-01 through a completed month. Use only the month range missing from local coverage. | `mops-monthly-revenue` job present; no schedule present. |

Use these observed control job IDs with the manual-run endpoint:

| Product | TWSE job ID | TPEx job ID | Example scope after operator approval |
| --- | --- | --- | --- |
| Valuation | `twse-valuation` | `tpex-valuation` | `{"start_date":"2026-10-05","end_date":"2026-10-05"}` |
| Flows | `twse-institutional-flow` | `tpex-institutional-flow` | `{"start_date":"2026-10-05","end_date":"2026-10-05"}` |
| Profile | `twse-company-profile` | `tpex-company-profile` | `{}` |
| Latest revenue | `twse-monthly-revenue-latest` | `tpex-monthly-revenue-latest` | `{}` |
| Revenue archive | `mops-monthly-revenue` | Unsupported | `{"start_month":"2026-07","end_month":"2026-07"}` |

These are finite recovery examples, not executed acquisitions. Recheck source
release and coverage before choosing a scope; a July TPEx archive cannot be
filled by the TWSE archive job or a latest-only TPEx request.

Publisher release timing is not an ingestion SLA. Wait for the relevant source
report to be published, then submit the smallest supported manual scope. The
local read API cannot determine whether an uncollected date is a market holiday,
a delayed source report, or an ingestion gap. TPEx dated captures describe what
the publisher returns now for the requested date; they are not historical
knowledge-time snapshots. An older retained issuer row stays available when a
newer revenue report omits that issuer. An omitted row is not zero.

## Manual acquisition, retry, and bounded backfill

First inspect the live operator API with authenticated GETs to
`/api/v1/control/jobs?limit=200`, `/api/v1/control/schedules?limit=200`, and
`/api/v1/control/runs?limit=50`. Confirm the exact job, adapter scope, and
whether a run is already active. A job's `enabled` flag only permits runs; it
does not mean that a recurring schedule exists. Do not add schedules for
latest-only TPEx jobs or date adapters until their source and schedule behavior
has been reviewed. A schedule endpoint change by itself cannot make the
TPEx profile or TPEx latest-revenue adapter accept scheduled runs.

Create a manual run only after an operator has selected and approved its scope.
The documented request is an authenticated POST to
`/api/v1/control/jobs/{job_id}/runs` with `{"scope":{...}}`; examples of
scope keys are `start_date`/`end_date` for dated reports and
`start_month`/`end_month` for MOPS. Profile and latest-revenue runs accept an
empty scope. Use the upstream control-plane runbook for local authentication
and the service's current request schema. Supply a distinct `Idempotency-Key`
for each approved action. To retry an existing run, the documented action is
POST `/api/v1/control/runs/{run_id}/actions` with `{"kind":"retry_run"}`.
This document records the procedure;
no control-plane writes were made for PW-05.

- For a flow or valuation gap, run one date first. Expand only after confirming
  the smallest missing interval and staying within the 366-calendar-day and
  completed-date bounds. Do not treat calendar dates with `MISSING` coverage as
  confirmed exchange sessions or closures.
- For MOPS revenue, request no more than 12 bounded months in a run. For the
  TWSE and TPEx latest feeds, omit date bounds. For TPEx revenue and profiles,
  do not request a historical range.
- Retry the same control run through the documented run retry action when the
  original run identity and complete source bundle remain available. Warm
  retries/replay preserve original bytes and receipt. If a reserved source
  bundle is missing or incomplete, use a new run/capture identity; do not fetch
  new bytes under an old identity. Checkpointed dated runs resume only from the
  persisted checkpoint behavior documented by the adapter; do not invent a
  checkpoint by moving the start date.
- Stop backfill on invalid bounds, malformed/wrong-date reports, or a source
  error. These outcomes are not authoritative EMPTY coverage. Resolve the
  source issue before retrying; never fill a gap with zero-valued records.

## Success evidence and release gate

For every run retain its submitted scope, run and attempt state, start/finish
times, checkpoint partitions/counts, and bounded progress summary from the
control API. Confirm `SUCCEEDED` and that each checkpoint corresponds to the
requested venue/date or revenue month. Then use the local query API to verify
source-specific coverage, selected-row presence, report/data period, and
original receipt. Use `/api/v1/valuations`, `/api/v1/institutional-flows`,
`/api/v1/company-profiles`, and `/api/v1/monthly-revenues`; for broader
partition records, use `/api/v1/coverage` with a bounded dataset and date/month
selector. Preserve upstream `served_at` as API service evidence separately
from report date, capture receipt, and PanWatch's `evaluated_at_utc`.

Do not release a freshness claim based only on a successful HTTP status or a
recent API response. Require the selected data period and matching source
receipt. For a latest snapshot, check both the selected issuer row and the
latest whole-snapshot presence. For monthly revenue, verify every requested
month separately; `missing` and `not_in_captured_report` have different
meanings. For flows, whole-market `AVAILABLE` coverage with no selected issuer
row does not mean zero flow. Leave age known but SLA unknown until a publisher
SLA is documented and verified.

## Read-only acceptance snapshot

The bounded PW-05 GET audit on 2026-10-07 used query OpenAPI 0.1.0 and control
schema v2. Authenticated control GETs returned HTTP 200 and showed 24 job
definitions, 11 schedules (8 enabled), and the latest 50 runs. The query
service reported ready daily-price partitions through 2026-10-06 for both
venues; that readiness endpoint covers daily prices, not these research
products. The query process did not expose a build commit.

At 2026-10-07 00:14–00:18 UTC, individual bounded product GETs used
`TWSE:2330`, `TPEX:5347`, `TWSE:00878`, and `TPEX:006201`; dates
2026-10-02..2026-10-05; and revenue months 2026-07..2026-08. Both flow APIs
reported 2026-10-02 available and 2026-10-05 missing. The date-complete flow
responses also label 2026-10-03 and 2026-10-04 `MISSING`; this evidence does
not classify those dates as open sessions or closures. TWSE:2330 valuation
returned both 2026-10-02 and 2026-10-05; TPEX:5347 returned 2026-10-02 only.
TWSE:00878 had no valuation rows, and the six-digit TPEX:006201 valuation
selector returned HTTP 400. TPEx valuation selection is documented for
four-digit IDs.

Revenue month evidence showed July `missing` and August `present` for
TWSE:2330 and TPEX:5347. The August retained receipts were from MOPS/TWSE for
TWSE:2330 and TPEx for TPEX:5347. For both ETF selectors, July was `missing`
and August was `not_in_captured_report`; the issuer qualification was
unsupported. Profile reads took 13.4–13.6 seconds each. The TWSE:2330 profile
report date was 2026-10-03; TPEX:5347's report date was 2026-10-04. Both were
received on 2026-10-04 UTC. Profile latest-snapshot evidence is separate from
whether a selected issuer profile exists.

Sanitized endpoint request/status/latency and selected receipt evidence is in
[`PW-05-live-smoke-2026-10-07.json`](../evidence/PW-05-live-smoke-2026-10-07.json)
and its client-projection corrections are in
[`PW-05-live-smoke-corrections-2026-10-07.json`](../evidence/PW-05-live-smoke-corrections-2026-10-07.json).
These files contain no raw publisher responses, source URLs, credentials,
credential material, or revenue amounts. The coordinator also verified the
actual shared research service at 00:16:07–00:16:52 UTC (08:16 Taipei). Four
aggregates took 10.638–11.738 seconds each and preserved the selected periods,
matching receipts, missing months, ETF qualification and per-block freshness.
The TPEx ETF aggregate preflighted the unsupported valuation selector without
issuing its rejected upstream GET. Results are in
[`PW-05-shared-service-2026-10-07.json`](../evidence/PW-05-shared-service-2026-10-07.json).

## Continuous-update prerequisites not met

The live audit observed only the TWSE valuation schedule among the requested
research products. Flows, both company-profile feeds, both latest-revenue feeds,
the MOPS archive, and TPEx valuation had no recurring schedule. Source release
cadence and publisher latency guarantees are undocumented. Before continuous
updates can be claimed, upstream must review each source's release behavior,
adapter's scheduled-run support, venue-specific bounds, retries, and recovery;
then choose schedule cadence and verify a first scheduled run plus query
coverage. In particular, manual-only adapters may need an upstream adapter
change in addition to any schedule definition. PW-05 performed no ingestion,
schedule mutation, subscription mutation, deployment, or direct SQLite read.
