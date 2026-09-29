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
  timestamp: string
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
  timeframe: string
  price_kind: string
  adjustment_mode: string
  coverage_complete: boolean
  returned_count: number
  klines: IntradayBar[]
  summary: Record<string, unknown> | null
  live_collection: boolean
}

export const klinesApi = {
  intraday: (symbol: string, timeframe: '1m' | '5m', signal?: AbortSignal) =>
    fetchAPI<IntradayResponse>(`/klines/${encodeURIComponent(symbol)}/intraday?market=TW&timeframe=${timeframe}&limit=270`, { signal }),
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
