import { fetchAPI } from './client'

export interface KlineSummaryRequestItem {
  symbol: string
  market: string
}

export interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: Record<string, unknown>
}

export interface IntradayBar {
  instrument_id?: string
  trade_date?: string
  timeframe?: '1m' | '5m' | string
  timestamp: string
  interval_start?: string
  interval_end?: string
  provider_timestamp?: string | null
  open: number | null
  close: number | null
  high: number | null
  low: number | null
  volume: number | null
  status: string
  finalized: boolean
}

export interface IntradayResponse {
  instrument_id: string
  timeframe: '1m' | '5m' | string
  start_date?: string
  end_date?: string
  display_coverage_complete?: boolean
  price_kind: string
  adjustment_mode: string
  coverage_complete: boolean
  returned_count: number
  total_count?: number
  truncated?: boolean
  availability?: string
  bars?: IntradayBar[]
  klines: IntradayBar[]
  summary: Record<string, unknown> | null
  live_collection: boolean
  date_selection?: {
    mode: 'auto' | 'selected' | 'previous' | string
    requested_date?: string | null
    selected_date?: string | null
    reason?: string
    calendar_status?: string
    calendar?: { status?: string; is_trading_day?: boolean | null }
    as_of_date?: string
    as_of_calendar?: { status?: string; is_trading_day?: boolean | null }
    coverage_status?: string | null
    coverage_calendar_status?: string
    display_coverage_complete?: boolean
    bar_request_count?: number
    bar_request_budget?: number
  }
  coverage?: Array<Record<string, unknown>>
}

export interface IntradayDateOptions {
  mode: 'auto' | 'selected' | 'previous'
  date?: string
}

export const klinesApi = {
  intraday: (symbol: string, timeframe: '1m' | '5m', signal?: AbortSignal, dateOptions?: IntradayDateOptions) => {
    const params = new URLSearchParams({
      market: 'TW',
      timeframe,
      limit: timeframe === '1m' ? '270' : '54',
    })
    if (dateOptions) {
      params.set('date_mode', dateOptions.mode)
      if (dateOptions.date && dateOptions.mode === 'selected') {
        params.set('start_date', dateOptions.date)
        params.set('end_date', dateOptions.date)
      } else if (dateOptions.date && dateOptions.mode === 'previous') {
        params.set('trade_date', dateOptions.date)
      }
    }
    return fetchAPI<IntradayResponse>(
      `/klines/${encodeURIComponent(symbol)}/intraday?${params.toString()}`,
      { signal },
    )
  },
  summaryBatch: (
    items: KlineSummaryRequestItem[],
    signal?: AbortSignal,
  ) => fetchAPI<KlineSummaryResponse[]>('/klines/summary/batch', {
    method: 'POST',
    body: JSON.stringify({ items }),
    signal,
    timeoutMs: 60_000,
  }),
}
