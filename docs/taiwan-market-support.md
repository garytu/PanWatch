# Taiwan market support

## Planned data expansion

The [PanWatch × twmd integration plan](plans/twmd-integration/README.md) records
the 2026-10-06 service observations and 14 tasks for official valuations, investor
flows, revenue, research/AI, discovery, chip data, announcements, financial
statements, benchmarks, corporate-action annotations, historical research and
live data. Each task includes dependencies, acceptance and validation requirements.
Execute one selected task at a time; this plan is not a delivered-feature claim.
The dated verification sections below remain historical evidence.

## Current integration

PanWatch uses twmd's native API for Taiwan quotes and price history. Listed and OTC
instruments retain their `TWSE:` / `TPEX:` identity throughout the application. Live
execution requires a fresh quote and a confirmed trading session. Stored closing
prices and minute bars remain available for research.

## Configuration and local startup

Set these values in PanWatch's `.env` or process environment:

```dotenv
TW_DATA_PROVIDER=twmd
TW_FUNDAMENTALS_PROVIDER=twmd
TW_CAPITAL_FLOW_PROVIDER=twmd
TWMD_BASE_URL=http://127.0.0.1:8000
TWMD_API_TOKEN=
TWMD_TIMEOUT_SEC=5
TWMD_PROFILE_TIMEOUT_SEC=20
TWMD_CONTROL_BASE_URL=http://127.0.0.1:9200
TWMD_CONTROL_AGENT_TOKEN=<agent token from twmd control service>
PANWATCH_PORT=8001
TW_PAPER_LOT_SIZE=1000
TW_COMMISSION_RATE=0.001425
TW_MIN_COMMISSION=20
FINMIND_API_TOKEN=
```

Environment variables override `.env`. The PanWatch port retains its existing
default of 8000; use 8001 when twmd already occupies 8000.

`TWMD_PROFILE_TIMEOUT_SEC` sets a separate bounded timeout for the slow latest-only
company-profile read. Its default is 20 seconds, based on the captured 15.5-second
responses; it makes one request and reports a timeout as an error. Other twmd reads
continue to use `TWMD_TIMEOUT_SEC`.

For local development:

```sh
PANWATCH_PORT=8001 make dev-api
PANWATCH_API_TARGET=http://127.0.0.1:8001 make dev-web
```

The frontend runs on 5183. `PANWATCH_API_TARGET` can also be set in
`frontend/.env.local`. In Docker, use a twmd address reachable from the PanWatch
container, such as a Compose service name; `127.0.0.1` refers to that container.
The read-only twmd query API and authenticated control API use separate addresses.
Keep the control agent token in PanWatch's private `.env`; it is never sent to the browser.
Rebuild the frontend and restart PanWatch after deploying these changes.

Charts use Playwright Chromium. Install the browser matching the project's
Playwright version, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to an installed Chromium
executable. Taiwan chart exports render twmd OHLCV locally and include the historical
date, currency, volume unit and adjustment mode.

`TW_DATA_PROVIDER=external` selects the older `/quotes?symbols=...` protocol and
FinMind daily history. Native twmd integration is the default.

`TW_FUNDAMENTALS_PROVIDER` and `TW_CAPITAL_FLOW_PROVIDER` each accept `twmd`
(default) or `finmind`, independently of the price provider. Each route selects
one source; an official query error or missing partition does not trigger a
FinMind fallback. Official reads default to a 30-calendar-day window ending on
the previous Taipei date, as required by the completed-date API policy. Generic
official PE is returned as `pe_ratio`, with `pe_ttm`/`pe_static` left null. Source
valuation dates and exact original values remain in `valuation_evidence`;
financial `report_date` stays separate. Flow evidence preserves each date's
coverage, selected presence, native share categories and source receipts. Neither
provider claims a five-trading-day sum without proven complete session coverage.

The stock research panel and `GET /api/research/taiwan` use the shared, read-only
TWMD research service for four independent blocks: official valuation,
institutional share counts, company profile and monthly revenue. The dedicated
`get_taiwan_stock_research` assistant tool uses this same service regardless of
the legacy provider selectors; `get_stock_fundamentals` and `get_capital_flow`
continue to respect their individual `TW_*_PROVIDER` setting. TradingAgents
receives the same structured research payload when Taiwan is enabled. Requests
use canonical `TWSE:` / `TPEX:` identities, completed Taipei dates, bounded
date/month windows, isolated block errors, a 25-second aggregate deadline,
bounded transport attempts and server-side cache/concurrency limits. Provider
credentials remain on the server. The current service does not integrate complete
financial statements: monthly revenue and quotes are not income, balance-sheet,
or cash-flow statements.

## Implemented behavior

| Area | Taiwan behavior |
| --- | --- |
| Identity and search | `TWSE:2330`, `TPEX:6488`, `2330.TW`, `6488.TWO`, and ETF codes retain venue. Bare codes resolve against the twmd catalog; ambiguous codes require an explicit venue. |
| Quotes | Native `/api/v1/quotes`, batches of at most 100 unique IDs; price kind, original timestamp, trade date, reference basis, health, freshness and units reach APIs and analysis. Missing values stay null. |
| Closing prices | An EOD fallback is explicitly labeled and has no fabricated observation timestamp. It cannot authorize a trade or live price alert. |
| Daily history | Native `/api/v1/bars?timeframe=day`; provider and raw adjustment mode reach technical summaries, daily/weekly/monthly charts and backtest input. |
| Minute history | `/api/klines/{symbol}/intraday?market=TW&timeframe=1m` or `5m`; date ranges are bounded. Missing, no-trade and pending slots retain their status and null prices. Charts break at those slots. Indicators require complete coverage. |
| Research and automation | Taiwan quote and technical collection uses its own route even when a mainland-only data source is disabled. Assistant and report contexts carry dates, units and price kind. Live monitoring rejects stale Taiwan quotes. |
| Discovery and strategies | Taiwan is available in opportunity filters, candidate generation, strategy recalibration and factor calibration. Discovery ranks current EOD snapshots from active cash securities. |
| Paper trading | Taiwan allocation is configurable and defaults to 0. Entries, exits and manual closes require a confirmed session and unexpired live quote. Regular lots default to 1,000 shares; `TW_PAPER_LOT_SIZE=1` selects an odd-lot quantity assumption. |
| Costs | Stock sell tax 0.3%; ETF sell tax 0.1%; bond ETF exemption through 2026. Commission and minimum commission are configurable broker assumptions. Taiwan has no mainland transfer fee. |
| Calendar | Validated TWSE annual schedule cached on disk, including settlement-only closures. Unknown/out-of-year coverage stops Taiwan trading-session jobs. `TW_EXTRA_CLOSED_DATES` adds emergency closure dates. |
| Financial and chip data | Official twmd valuation and institutional flows are the defaults; FinMind is an explicit alternate for those two routes and continues to provide news, dividends and margin. Institutional flows use integer shares, preserve native categories/evidence, and leave cash and unproven five-day totals null; margin quantities use lots. |
| Readiness and subscriptions | `/api/quotes/taiwan/status` reports collection health, durable requested subscriptions, confirmed subscriptions, calendar status and active cash-instrument counts by venue. The watchlist can explicitly request or cancel a subscription through PanWatch's authenticated proxy to twmd control port 9200. Requested subscriptions may remain pending until the collector confirms them. |

The cost assumptions follow the [TWSE securities guide](https://www.twse.com.tw/en/about/company/guide.html)
and [ETF trading rules](https://www.twse.com.tw/en/products/securities/etf/overview/rules.html).
The configured minimum commission is a simulation assumption, rather than an
exchange requirement. The swing simulator does not apply stock day-trading tax
relief. Margin quantities follow [FinMind's chip-data contract](https://finmind.github.io/tutor/TaiwanMarket/Chip/).

On 2026-10-01, the rebuilt twmd control and collector services reported
`TWSE:2330`, `TWSE:2303`, and `TWSE:4164` in both the durable requested set and
the collector-confirmed set. PanWatch's status endpoint reported a connected
collector. The watchlist also contained `TWSE:4958`, which had not been requested;
the UI offers an explicit subscribe action for it. This check ran after the
regular session and does not establish fresh trading-session delivery.

## Verification on 2026-09-29

Read-only checks against the service on port 8000 returned:

| Instrument | Closing price (TWD) | Daily bars | 1-minute bars | 5-minute bars |
| --- | ---: | ---: | ---: | ---: |
| TWSE:2330 | 2475 | 120 | 270 | 54 |
| TWSE:2303 | 153.5 | 120 | 270 | 54 |
| TWSE:4164 | 26.9 | 120 | 270 | 54 |

All three price dates were 2026-09-29. Daily history reported `raw`; minute history
reported `provider_reported`, complete coverage and `live_collection=false`.
The checks ran after the trading session, so quotes were EOD fallbacks with
`timestamp=null` and `usable_for_trading=false`. This verifies historical delivery
and the trading gate, not live-session arrival or a latency guarantee.

The TWSE calendar covered 2026. ETF search returned `TWSE:00878`. The upstream
catalog had 1,353 active TWSE equities/ETFs/preferred shares and **zero active TPEX
instruments**; TPEX rows were inactive `OTHER` placeholders. TPEX search/discovery
requires upstream catalog completion. The current PanWatch database contained no
Taiwan watchlist entries; twmd's three subscriptions are a separate configuration.

Automated verification:

```sh
.venv/bin/python -m pytest -q tests packages/marketdata/tests
cd frontend
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

Results: 1,038 backend tests passed (3 skipped), 46 frontend tests passed;
TypeScript and production build passed. Taiwan chart export was rendered and
visually checked with an installed Chromium. `git diff --check` passed.

## Remaining service requirements and model boundaries

- The 2026-10-06 catalog checks returned active TPEx equities, ETFs and warrants.
  Filter by venue, activity and security type before search/discovery; the
  2026-09-29 zero-active-TPEx result above remains a historical observation.
- Validate live arrivals, quote expiry and recovery during a trading session.
  PanWatch does not silently subscribe every instrument discovered by its scanner.
  The twmd control service must be reachable with an agent token to edit subscriptions.
- The 2026-10-07 minute capability check reported live collection supported,
  unconfigured and disabled. Configure and validate trading-session arrivals,
  finalization and recovery before claiming a streaming chart or minute strategy;
  stored historical bars do not prove live delivery.
- FinMind availability, token entitlement and rate limits determine non-price
  coverage. Those datasets were checked through mocked contracts, not an exhaustive
  live fundamental/news audit.
- Price history is raw. Corporate-action adjustments, dividend cash flows, price
  limits, queue priority, liquidity constraints, actual broker fills and detailed
  odd-lot matching are not modeled in paper trading/backtests. Cross-market paper
  totals retain the existing nominal-cash convention and are not an FX ledger.
- Taiwan market indexes, industry-sector benchmarks, warrants, derivatives, TDRs,
  REITs and ETNs do not have a complete workflow in this change. Supported automated
  cash-security workflows cover active equities, ETFs and preferred shares.
- TWSE's annual calendar does not establish unscheduled typhoon closures. Supply
  emergency closures with `TW_EXTRA_CLOSED_DATES` when needed.


## Taiwan-only deployment

The active-market lists in `src/platform/marketdata/models.py` and
`frontend/src/lib/markets.ts` now contain only `TW`; the CN/HK/US entries are
commented out. Restore the entries in both files and restart PanWatch to enable
other markets again. Their providers, market definitions and saved records remain.

Selectors, stock search, opportunity discovery, market status and factor panels
show Taiwan. Empty Taiwan opportunity results do not fall back to other markets.
Automatic candidate scans, signal generation, factor calibration, agent contexts,
price alerts and paper trading are limited to enabled markets. The quote adapter
returns no quotes for disabled markets, and disabled market indexes are not fetched.
Existing portfolio records and historical reporting remain available; this is not
an account-data migration.

Disabled paper allocations are treated as zero without automatically transferring
money to Taiwan or modifying saved account allocations. An account with Taiwan at
0% will continue to have no Taiwan buying budget until configured explicitly.
Saving the Taiwan-only allocation form writes the effective allocation (other
markets zero). Historical positions are retained, and manual closes in disabled
markets are blocked while this policy is active.

The regression suite explicitly enables all supported markets for existing tests;
`tests/test_enabled_markets.py` exercises the Taiwan-only deployment separately.

## twmd research contract observation (2026-10-06)

PW-01 records a read-only query audit and offline contract examples in the
[integration contract](plans/twmd-integration/contracts/README.md). These dated
API observations define source coverage and field meaning; they do not indicate
that PanWatch research screens or providers have started using these datasets.

The local query API reported OpenAPI version `0.1.0` and readiness through the
2026-10-06 TWSE and TPEx daily-price partitions. Its response did not expose a
build commit, so the separately inspected tw-market-data source commit cannot
be treated as the deployed version. The catalog query returned active
`TWSE:2330` and `TPEX:5347` equities, `TWSE:00878` and `TPEX:006201` ETFs, and
`TPEX:700019` as a warrant. Venue and security type must remain part of a
Taiwan instrument's identity and eligibility.

On 2026-10-06, both venue flow endpoints returned 2026-10-02 samples and
`MISSING` coverage for 2026-10-05. TPEx valuation returned a 2026-10-02 sample
and no retained 2026-10-05 row; the TWSE valuation query returned rows on both
dates. Both venues' 2026-07..08 revenue responses had July missing and August
present. The active TPEx ETF revenue query returned
`qualification=unsupported_etf`. These outcomes distinguish dataset coverage
from one issuer's presence and retain missing values as null rather than zero.

Latest company profiles were returned for `TWSE:2330` and `TPEX:5347`, with
`latest_snapshot_presence=present` and `qualification=qualified_issuer`. Each
read took more than 15 seconds after two 10-second attempts timed out. This
does not establish a latency guarantee; the current PanWatch client timeout
defaults to 5 seconds. Profile, valuation, flow, and revenue contracts remain
research integration prerequisites documented in PW-01, not implemented
PanWatch features. The existing market-data HTTP helper returns parsed bodies
only and maps exhausted request errors to `None`; it cannot currently preserve
TPEx valuation coverage headers or distinguish those errors from empty-shaped
results. PW-02 records this transport prerequisite.

At 2026-10-07 00:10:54 Taipei, a supplemental read-only
`/api/v1/bars/capabilities` request returned HTTP 200 in 16.522 ms with
`live_collection_supported=true`, `live_collection_configured=false`, and
`live_collection=false`. The sanitized capability fixture retains only these
public flags. This updates the current service requirements while retaining
the earlier dated observations.


## Profile and revenue integration (2026-10-07)

PW-03 adds `MarketData.company_profile`, `monthly_revenues` and
`adjacent_monthly_revenues`. Profiles remain latest-only. Revenue ranges are
inclusive, start at 2024-01, contain at most 120 months, and allow the current
Taipei month when retained. The adjacent read takes an explicit month and its
predecessor; it does not guess a latest filing month. Queries do not acquire data.

The backend research adapter returns separate data/status/reason/evidence blocks.
It preserves profile qualification and capital/share/par semantics, month presence,
retained rows, source percentage strings, notes, receipt, revision and unit inference.
Monthly revenue is not quarterly financial-statement revenue. A missing month,
omitted issuer, unsupported ETF or provider error is not a zero. Publication time
remains unknown. The five-minute cache isolates service, credentials, venue-local
issuer, dataset and range, returns defensive copies, and excludes failed reads.

A bounded typed-client smoke at 02:38 Taipei returned both company profiles within
the separate 20-second timeout (14.02/10.40 seconds), July missing and August present
for both venue revenue ranges, and unsupported/no rows for TPEX:006201. All five
GETs returned 200; this is local read-only evidence, not a deployment or future
latency guarantee. The final backend suite passed 1151 tests with 3 skipped.
The research page and AI/TradingAgents wiring are now implemented by PW-04 below.


## Shared stock research and AI integration (2026-10-07)

PW-04 connects the stock insight overview, protected research API, official
assistant tools, TradingAgents and research context to the same four-block service.
The valuation card labels the provider dividend reference year (ROC years are
displayed as Gregorian years) beside the official yield. Missing years stay
explicitly unknown. The original percentage is preserved, with an explanation
that its reference-year basis differs from a latest-quarter annualized estimate.
No quarterly dividend is inferred or multiplied by four.
Latest-only profiles and retained monthly disclosures keep their own periods;
partial data and unsupported ETF blocks do not erase available institutional data.
Complete Taiwan financial statements remain unavailable. Named assistant tool
budgets are 28 seconds, the aggregate wall deadline is 25 seconds including the
catalog, and TradingAgents allows this source 30 seconds independently of its
other collection sources. Underlying reads retain their concurrency permits until
completion, including after callers time out. The cache holds at most 256 entries
with dataset TTLs: catalog/valuation/flows 5 minutes, profile 6 hours, revenue
30 minutes. Failed reads are not cached. Credentials and unrelated HTTP headers
are excluded from public evidence.

At 06:43–06:44 Taipei, a bounded local read-only smoke used date 2026-10-02 and
months 2026-07..08 for TWSE:2330, TPEX:5347 and ETF TPEX:006201. All 12 GETs
returned HTTP 200. Stock aggregates took 10.618/12.048 seconds with available
valuation/flows/profile and partial revenue (July missing, August present).
The ETF aggregate took 12.061 seconds: flows were available, profile/revenue
unsupported, and the incompatible non-four-digit TPEx valuation selector was
preflighted without a request. Profile GETs took 9.811–11.952 seconds. The formal
stock/ETF panel was visually checked against these real serialized payloads in
an isolated preview, which was removed afterward. These checks do not establish
future latency, full market coverage, deployment or intraday freshness.

Final verification: backend 1177 passed/3 skipped; agent runtime 42 passed;
frontend 20 files/50 tests passed; TypeScript and production build passed;
working and staged whitespace checks passed. The next default task is PW-05 for
update responsibility, age/coverage hints and first-batch operational acceptance.
No collection, schedules, deployment, control mutations or paid LLM run were
executed for PW-04.

## Research freshness and read-only acceptance (2026-10-07)

PW-05 adds per-block freshness and coverage to valuation, flow, company-profile,
and monthly-revenue research. It keeps the selected data period, publisher
report date, publication time only when supplied, matching source receipt,
upstream API `served_at`, and PanWatch's request evaluation clock separate. A
single UTC request clock drives default bounds and age evaluation; cached
results are re-aged on every return. Monthly period age is measured from the
last day of the data month. Frequency hints describe daily, latest-snapshot, or
monthly data without asserting a publisher release deadline. No publication
time, publisher SLA, or exchange-calendar result is inferred. Missing source
rows remain missing and are never converted to zero.

The 2026-10-07 shared research smoke used 2026-10-02..05 and revenue months
2026-07..08:

| Instrument | Valuation | Institutional flows | Profile | Revenue |
| --- | --- | --- | --- | --- |
| `TWSE:2330` | Available through 2026-10-05 | Partial; 2026-10-02 row, later selected dates missing | Available; report 2026-10-03 | Partial; August present, July missing |
| `TPEX:5347` | Partial; 2026-10-02 row, 2026-10-05 missing | Partial; 2026-10-02 row, later selected dates missing | Available; report 2026-10-04 | Partial; August present, July missing |
| `TWSE:00878` | No selected rows; selection coverage unknown | Partial | Unsupported issuer type | Unsupported issuer type |
| `TPEX:006201` | Unsupported valuation selector | Partial | Unsupported issuer type | Unsupported issuer type |

Date-complete flow responses also reported `MISSING` for 2026-10-03 and
2026-10-04. This does not establish that either date was an exchange session.
The 2026-10-02 flow row is not shown as 2026-10-05 data. For ETF revenue,
July was `missing` and August was `not_in_captured_report`; these are distinct
states. Shared research requests took about 10.6–11.7 seconds overall;
the direct profile endpoint reads took about 13.4–13.6 seconds, so the separate
20-second profile timeout remains bounded and necessary. These measurements do
not establish a latency guarantee.

The query API reported OpenAPI 0.1.0, health OK, and ready daily-price
partitions through 2026-10-06 for both venues. Its readiness response concerns
daily prices, not research products. Authenticated read-only control GETs
reported 24 job definitions and 11 schedules, eight enabled. Only the existing
TWSE valuation schedule covered the requested research datasets; flows,
profiles, latest-revenue feeds, the MOPS archive, and TPEx valuation had no
recurring schedule. The manual-only adapters and precise publisher bounds are
in the [PW-05 refresh and recovery runbook](plans/twmd-integration/runbooks/PW-05-refresh.md).
Sanitized endpoint evidence is linked from that runbook. Continuous refresh
requires upstream adapter and schedule review; adding a schedule alone cannot
make all manual-only adapters accept scheduled runs.

The assistant's research discovery metadata classifies these retained reads as
static, with period-specific evidence; it does not promise near-real-time source
updates. The UI separates calendar-day age from monthly age measured at month-end,
and identifies the latest row within the selected scope. The coordinator's actual
shared-service results at 08:16 Taipei are retained in
[the aggregate evidence](plans/twmd-integration/evidence/PW-05-shared-service-2026-10-07.json).

Final coordinator verification: backend 1180 passed / 3 skipped; frontend
20 files / 51 tests passed; TypeScript, production build and whitespace check
passed. PW-05 is completed; PW-06 is the default next ready card. This is code
and bounded read-only acceptance, with no deployment or continuous-refresh claim.
