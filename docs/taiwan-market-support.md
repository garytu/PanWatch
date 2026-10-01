# Taiwan market support

PanWatch uses twmd's native API for Taiwan quotes and price history. Listed and OTC
instruments retain their `TWSE:` / `TPEX:` identity throughout the application. Live
execution requires a fresh quote and a confirmed trading session. Stored closing
prices and minute bars remain available for research.

## Configuration and local startup

Set these values in PanWatch's `.env` or process environment:

```dotenv
TW_DATA_PROVIDER=twmd
TWMD_BASE_URL=http://127.0.0.1:8000
TWMD_API_TOKEN=
TWMD_TIMEOUT_SEC=5
TWMD_CONTROL_BASE_URL=http://127.0.0.1:9200
TWMD_CONTROL_AGENT_TOKEN=<agent token from twmd control service>
PANWATCH_PORT=8001
TW_PAPER_LOT_SIZE=1000
TW_COMMISSION_RATE=0.001425
TW_MIN_COMMISSION=20
FINMIND_API_TOKEN=
```

Environment variables override `.env`. The PanWatch port retains its existing
default of 8000; use 8001 when twmd already occupies 8000. For local development:

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
| Financial and chip data | FinMind remains the optional source for fundamentals, news, dividends, institutional flows and margin. Institutional flows use shares; margin quantities use lots and leave monetary fields null. |
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

- Complete the active TPEX catalog in twmd, preserving canonical venue, security
  type, name and delisting state. See [the twmd handoff](twmd-taiwan-follow-up.md).
- Validate live arrivals, quote expiry and recovery during a trading session.
  PanWatch does not silently subscribe every instrument discovered by its scanner.
  The twmd control service must be reachable with an agent token to edit subscriptions.
- The deployed minute API serves stored historical acquisitions. Continuous live
  minute collection still requires a twmd enhancement before a streaming chart or
  minute-driven live strategy can be claimed.
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
