# twmd follow-up for Taiwan coverage

Pass the following request to the session working in `tw-market-data-main`.

## Complete the active TPEX instrument catalog

PanWatch now consumes twmd's native quotes, instruments, price snapshots and bars.
Read-only verification on 2026-09-29 found no active TPEX instruments in
`GET /api/v1/instruments`: all 45,092 TPEX records were inactive `OTHER` placeholders.
For example, `TPEX:6488` had its symbol as its name, no reference metadata and
`is_active=false`. This prevents OTC search, discovery and security-aware paper
trading even though the wire contract supports `TPEX:` IDs.

Implement an authoritative TPEX reference-data ingestion path. Populate real names,
security types, active status, listing dates and available reference fields; retain
inactive and delisted records explicitly. Keep IDs canonical and do not infer TWSE
from numeric codes. Merge or replace placeholders without relabeling unresolved
rows as active. Make the refresh part of the supported ingest/deployment workflow,
with freshness/coverage visible to clients.

Acceptance checks:

1. `/api/v1/instruments?venue=TPEX&is_active=true` returns genuine currently traded
   equities and ETFs, with names and correct security types.
2. Use a current instrument from that catalog to verify instrument lookup, EOD
   price snapshots, daily bars and historical 1m/5m bars. Include one equity and
   one ETF where supported.
3. Subscribe a supported TPEX equity through the existing quote configuration and
   verify venue, share units, original timestamp and freshness during a session.
4. Duplicate bare codes across venues remain ambiguous; inactive placeholder rows
   do not override authoritative reference data. Do not fabricate missing prices.
5. Add ingestion/migration and API-contract tests, and document the refresh command
   and any upstream coverage limitations. Keep PanWatch's existing contract intact.

## Verify live readiness and extend minute collection

The current three TWSE subscriptions are `TWSE:2330,TWSE:2303,TWSE:4164`.
The after-hours API correctly reported no observed live ticks. Verify their live
arrival, expiry and reconnect behavior during a trading session; record actual
results without replacing observation times with request times.

Minute capabilities currently report `live_collection=false`. Stored history is
working (270 1m / 54 5m slots for 2026-09-29 for each subscribed instrument).
If continuous live minute collection is required, implement finalized 1m bars and
5m aggregation with explicit pending, missing and no-trade states. Preserve
historical versus live capability flags, Taipei session boundaries, original
source provenance and truthful coverage. Reconnect/backfill must avoid double
counting tick volume or turning silent intervals into observed zero-price bars.
Document ingestion/recovery commands and test session rollover, disconnects,
late arrivals and incomplete 5m buckets.

PanWatch treats historical minute bars as research data. It will only enable live
minute behavior after the upstream capability and completeness guarantees exist
and have been verified.
