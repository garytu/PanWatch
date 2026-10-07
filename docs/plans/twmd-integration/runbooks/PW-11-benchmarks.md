# PW-11 official benchmark acquisition and recovery

This procedure covers only persisted daily index observations consumed by the
PanWatch home page and Taiwan research comparison. Query reads do not contact
the publishers. Do not enable a recurring schedule as part of this procedure.

## Source scope

| Benchmark | Upstream job | Publisher selector | Collection behavior |
| --- | --- | --- | --- |
| TAIEX | `twse-benchmark-daily` | Inclusive `start_date` and `end_date` | TWSE `MI_5MINS_HIST` is requested by calendar month. Keep each manual scope within one month where possible; verify every returned date and capture receipt. A bounded multi-month request is acceptable only when the approved range is explicit. |
| TPEX | `tpex-benchmark-latest` | No historical date selector | TPEx returns its current retained daily index payload only. Repeated approved latest captures accumulate only the dates actually published and retained. This job cannot backfill historical dates. |

The inspected control plane had both job definitions enabled and their existing
recurring schedules disabled. Job `enabled` permits an operator-triggered run;
it does not enable the recurring schedule. Do not add or enable a schedule without a separate
review of publisher release timing and acquisition behavior.

## Bounded manual acquisition

First use authenticated read-only GETs to
`/api/v1/control/jobs?limit=200`, `/api/v1/control/schedules?limit=200`, and
`/api/v1/control/runs?limit=50`. Confirm the exact job, current run state, and
the operator-selected missing interval. A manual run requires explicit
authorization for that scope. Submit it to
`POST /api/v1/control/jobs/{job_id}/runs` with `{"scope":{...}}` and a unique
`Idempotency-Key`; use the control-plane's current authentication and request
schema.

For example, a single completed TWSE month uses
`{"start_date":"2026-09-01","end_date":"2026-09-30"}`. The dates are
inclusive. For an interval crossing months, keep the requested inclusive
endpoints explicit and split into month-sized runs when repairing individual
gaps. Do not request dates beyond the source's verified range or widen a repair
scope to unrelated history.

The TPEX latest-only job uses an empty scope: `{"scope":{}}`. Do not pass
historical date bounds to it and do not fill dates that were not present in a
retained TPEx payload with interpolated, copied, zero, or otherwise fabricated
values. A missing calendar date alone does not establish a collection failure
or a market holiday.

Wait for the run to finish and retain its run ID, attempt state, requested
scope, start/finish times, partition counts, and safe progress summary. Only
`SUCCEEDED` permits the subsequent read check. If retrying, use the documented
control action for that run and preserve the original run identity; if source
bytes or their capture receipt are unavailable, request a new authorized
capture instead of assigning new bytes to an old identity.

## Read-only acceptance

After a successful run, query `GET /api/v1/benchmarks` and the appropriate
`GET /api/v1/benchmarks/{benchmark_id}/bars`. For TAIEX, request the same
inclusive bounded dates. For TPEX, omit date bounds and inspect the actual
retained dates. Confirm all of the following before treating the read as useful:

- HTTP status is successful and the benchmark ID, venue, provider, source
  alias, index-point unit, and raw price-index basis match the intended source.
- Returned bars have publisher trade dates, positive decimal OHLC values, and
  valid OHLC ordering. Compare returned dates with the request, coverage, and
  gaps; do not infer an exchange calendar from `MISSING` dates.
- Retain `partial`, `truncated`, the selected range, `served_at`, coverage
  counts, source URL, capture ID, revision, request scope, payload digest, and
  captured time. A recent HTTP response or a non-empty list alone is not proof
  that the requested range is complete.
- TAIEX provenance may contain a query-scoped monthly publisher URL, such as
  `?date=20260901&response=json`; preserve that receipt URL with the base
  publisher endpoint. TPEx's source receipt is latest-only and does not create
  historical knowledge of older dates.

PanWatch treats valid partial or truncated bars as observations while keeping
those flags visible. Research returns use only actual common stock/index dates;
they exclude dividends and are not total returns. A zero-bar benchmark remains
unavailable and does not produce a synthetic sparkline or comparison.

## Cadence and limits

No recurring acquisition is enabled by PW-11. TAIEX historical collection is
bounded and monthly; TPEx accumulation is limited to each actual latest-only
capture. Any future schedule needs its own source-release review and operator
decision. No query-side action triggers acquisition, schedule changes,
subscriptions, deployment, or direct database access.
