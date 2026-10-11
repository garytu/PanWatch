# PW-13 bounded historical research runbook

This runbook describes an offline, deterministic evaluator for the optional PW-06
conditions. It does not query TWMD, scan the active catalog, update research
caches, or alter production factor weights/signals. Keep each input snapshot
immutable and retain its input and evaluation hashes with the report.

## Reproduce the retained-data inventory

From the repository root, run:

```sh
.venv/bin/python scripts/research/pw13_historical_research.py \
  --output docs/plans/twmd-integration/evidence/PW-13-offline-inventory-2026-10-08.json
```

This reads only the checked-in PW-05, PW-10, PW-11 and PW-12 safe evidence,
the retained TWMD response capture, and captured TWSE:2330 2024Q4
financial-report raw fixture and metadata. It records source file SHA-256 values, fixed
instrument selectors, daily and monthly windows, latest-only profiles, stock
and venue-specific benchmark coverage, receipt and revision fields,
company-action coverage, the exact factors present in the immutable capture,
and a canonical input hash. Re-running the command with the same files produces
the same JSON and hashes. Do not edit the generated evidence by hand; update the
source evidence and rerun the command.

The current inventory is bounded to four selected instruments. The PW-05
valuation/flow query covers calendar bounds 2026-10-02..05. Its source evidence
has one selected flow date for TWSE:2330, TPEX:5347, TWSE:00878 and TPEX:006201;
the `MISSING` calendar selectors do not establish exchange sessions. For the
two ordinary-equity revenue rows, July 2026 is missing and August is present.
The two ETF revenue rows are unsupported or absent from the captured report.
The latest profile snapshot has no historical-universe query.

PW-11 retained 24 TAIEX and 4 TPEx benchmark observations in the selected
2026-09-01..10-06 comparison window. The separate home spark contained 20 TAIEX
and 5 TPEX observations through 2026-10-07. TWSE:2330 and TPEX:5347 stock
responses were latest-1,000 partial reads; the requested comparison ranges
contained 24 and 23 stock observations, respectively. The comparisons shared
24 TAIEX dates and 4 TPEx dates. These are observed rows and receipts, not
verified complete trading calendars. Benchmark payloads retain source capture,
revision and hash; stock daily rows retain per-partition acquisition times.

The retained TWSE:2330 consolidated 2024Q4 report has 394 facts, a source
revision and capture identity, and separate document-first-observed and
semantic-revision-first-observed clocks. Both first observations and the
original receipt are in October 2026; publisher time is null. This report is
therefore excluded from any 2024 decision-time feature. Current `received_at`
does not backdate knowledge. TPEx multi-year revenue history is not established
by the reviewed contract, and the PW-12 company-action result lists have unknown
coverage. The detailed machine-readable findings are in
[`PW-13-offline-inventory-2026-10-08.json`](../evidence/PW-13-offline-inventory-2026-10-08.json).

The bounded October 6 TWMD capture retains actual PE, PB, dividend-yield,
single-day institutional-flow and monthly revenue YoY rows for selected
instruments. Rows without their own receipt/version metadata are bound to the
immutable local capture time and SHA-256. Flow `first_observed_at` remains
descriptive row-observation metadata; the captured payload receipt and hash
bind the exact retained content. Revenue observations carry their source receipt
and revision evidence.

The report applies a fixed availability audit at 2026-10-02 17:00, 2026-10-05
17:00 and 2026-10-07 09:00 Taipei time for the four fixed selectors (12
instrument/decision rows). The first two decisions are after the session close;
the October 7 decision is before the close. This grid audits only what the
retained snapshot could establish at those times. For example, October 2 values
received on October 4 are explicitly unavailable as of October 2. At the
October 7 decision the captured latest TPEX valuation and flow rows are null,
so they remain unavailable rather than falling back to an older value; captured
revenue can remain available. The audit reports each factor's selected period,
availability clock, version identity and exclusion reason.

This fixed grid is `inventory_only`: conditions are empty, thresholds were not
preregistered, no signal-selection or factor-performance evaluation is claimed,
and no outcomes are attached to the checked-evidence snapshot. Therefore the
report says `evaluation_performed: false`, `insufficient_evidence`, zero
eligible outcome samples, and `efficacy_claim_allowed: false`. The lack of
matched forward outcome bars in this snapshot is separate from the upstream
inventory counts above. This is a finding about checked-in evidence only, not a
claim that the upstream service has no such data. The detailed machine-readable
findings are in
[`PW-13-offline-inventory-2026-10-08.json`](../evidence/PW-13-offline-inventory-2026-10-08.json).

## Evaluate a separately retained snapshot

The reusable pure-Python entry point is
`src/modules/research/taiwan_historical_research.py`. Its accepted JSON format
is `panwatch.pw13.snapshot.v1`; the small example at
[`synthetic_snapshot.json`](../../../../tests/fixtures/pw13/synthetic_snapshot.json)
is explicitly synthetic and exists only to exercise the evaluator:

```sh
.venv/bin/python scripts/research/pw13_historical_research.py \
  --input path/to/immutable-pw13-snapshot.json \
  --output path/to/pw13-evaluation.json
```

Before evaluating, the snapshot must bind:

- A fixed array of canonical `TWSE:<code>` / `TPEX:<code>` IDs, the universe
  basis, and a decision window. A current issuer profile cannot define a
  historical universe.
  `scope.universe_kind` defaults to `fixed_selectors`; `latest_profile` excludes
  every historical decision even if a quality flag claims otherwise.
  `point_in_time_snapshot` requires separately verified universe evidence.
- A nonempty `conditions` object using exactly the PW-06 keys:
  `pe_max`, `pb_max`, `dividend_yield_min_pct`, `revenue_yoy_min_pct`, and/or
  `institutional_net_min_shares`. Ratios and percentages are exact decimal
  strings; net flow is an integer share count. Operators match PW-06: PE/PB
  use `<=`; yield, revenue YoY and single-day institutional net shares use
  `>=`. Threshold ranges match `TaiwanDiscoveryService._validate_conditions`.
- Each signal's timezone-aware `decision_at_utc`, an ordered train/evaluation
  date split, a positive observation-sequence outcome horizon, sample floors,
  and an explicit cost policy. Set `thresholds_preregistered: true` only when
  the conditions and split were fixed before results were calculated.
  The quality gate also requires `preregistered_at_utc` strictly before midnight
  Taipei on the evaluation start date; a boolean alone cannot establish this.
- Factor observations with the exact content value (including explicit nulls),
  canonical ID, PW-06 condition, data period, source contract and version
  identity (`revision`, `capture_id` or payload/content hash). Use
  `semantic_revision_first_observed_at_utc` for a retained financial-report
  revision. A document-first observation is not a semantic-revision clock.
  Otherwise provide an immutable `local_snapshot_at_utc`, or mark an
  observation timestamp as `observation_time_semantics: exact_content_version`.
  A receipt is an availability clock only when
  `receipt_binds_content_version: true`; observed time and receipt are both
  honored, and the later clock wins. A verified publisher time may delay
  availability; report/data period alone never establishes availability.
- Stock bars tied to their canonical instrument and venue, and benchmark bars
  with `venue` plus the exact benchmark identity (`TWSE` → `TAIEX`,
  `TPEX` → `TPEX`). Every used bar needs its source contract/dataset, an
  immutable content identity and a timezone-aware receipt/capture/snapshot
  time. Preserve per-date missing windows and whether an exchange calendar was
  actually verified.
  Stock bars require `unit: TWD/share` and `adjustment_mode: raw`; benchmark
  bars require `unit: index_points` and `basis: raw_price_index`. Different
  price bases cannot be silently compared.
- `quality` declarations for historical-universe coverage, exchange calendar
  verification and company-action completeness. These are explicit evidence
  gates; setting them true requires supporting captured evidence.

For each decision, the evaluator selects the latest admissible period and then
the latest version actually available by that decision time. A latest null
value is excluded and does not fall back to an older favorable value. Conflicting
versions tied at the same period and availability time are ambiguous and
excluded. A later receipt or changed semantic revision cannot be backdated.
Bare-code, cross-venue and malformed identities are rejected.
PW-06 freshness limits also apply: daily values are at most seven calendar days
old; revenue values are at most 90 days old measured from month end. Validation
occurs after selecting the latest known version, so an invalid latest value
cannot revive an older favorable observation.

Entry uses the first retained stock close strictly after the decision date, so
the same-day close is never treated as executable after a later decision time.
Exit follows the configured number of steps in the retained observation
sequence. The exact entry and exit dates must each exist in that stock's venue
benchmark series; the evaluator reports and excludes either missing date rather
than choosing a future common intersection or a nearby benchmark row. If no
verified exchange calendar is supplied, the horizon is explicitly an observed
row sequence, not a guaranteed number of exchange sessions.
Known invalid stock dates in the forward window exclude the outcome rather
than shifting entry or exit past those dates. Multiple decisions yielding the
same instrument/entry/exit window count only once toward the sample floor;
overlapping windows still require independent statistical review.

The cost scenario accepts decimal `entry_cost_bps` and `exit_cost_bps` and
subtracts their sum from the stock gross percentage return. It is a sensitivity
calculation, not a Taiwan brokerage model: do not claim it covers minimum
commissions, tax, lot size, market impact, or execution. The evaluator never
imports the existing A-share default cost model or reads `date.today()`.

Training signals whose forward exit date is on or after `eval_start_date` are
purged. Evaluation signals whose exit is beyond `eval_end_date` are excluded.
The evaluator reports raw-price, after-scenario-cost stock return and the
same-date venue-benchmark return/difference, plus sample counts, exclusions,
quality gates and hashes. Hashes bind the full canonical input, scope,
thresholds, split, costs, outcome horizon and quality declarations. Array order
does not affect canonical hashes.

Even a sample that clears its declared floor is `sample_ready_for_review`, not
an efficacy claim. Synthetic input always remains `insufficient_evidence`.
Actual efficacy requires a separate review of adequate independent samples,
universe/calendar coverage, revision-as-of quality, costs, delisted instruments,
and company actions. Current raw price bars are not dividend-adjusted or total
return data.

## Retain a bounded offline snapshot

Export the default evidence dataset alongside its inventory report, or use
`--input` to retain a separately prepared snapshot:

```sh
.venv/bin/python scripts/research/pw13_historical_research.py \
  --export-snapshot /tmp/pw13-snapshot.json --output /tmp/pw13-inventory.json
.venv/bin/python scripts/research/pw13_historical_research.py \
  --input /tmp/pw13-snapshot.json --output /tmp/pw13-replay.json
```

An existing snapshot may be reused only when the serialized content is identical;
different content requires a new path. Report output cannot overwrite an input or
exported snapshot. Retain these files with their hashes; this command performs no
upstream collection and does not change the mutable production context store.
This is operator-managed retention, not a newly enabled snapshot schedule.

Input files are limited to 16 MiB, 20 canonical instruments and a decision window
of 366 inclusive calendar days. Arrays are capped at 40,000 factor observations,
7,320 signals, 20,000 stock bars and 2,000 benchmark bars. The horizon is an
integer from 1 to 250; each side's explicit scenario cost is 0–10,000 bps.

## Current external data required

To move beyond the current insufficient-evidence result, retain version-bound
factor snapshots for all selected conditions at fixed decision times, starting
with bounded TWSE and TPEX issuer scopes. Each snapshot needs per-value
availability and immutable version evidence. Keep an explicit supported
instrument list; do not infer historical eligibility from today's profile.
Retain source-reported missing partitions, actual venue sessions, matching
stock/benchmark dates and per-date receipts. Add a complete company-action
history and a Taiwan-specific documented cost policy before interpreting raw
price results as factor efficacy. No collection, backfill or upstream schedule
is authorized by this runbook.
