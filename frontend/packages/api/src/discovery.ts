import { fetchAPI } from './client'

type QueryValue = string | number | boolean | null | undefined

function withQuery(path: string, params: Record<string, QueryValue>): string {
  const q = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null) return
    const sv = String(v).trim()
    if (!sv) return
    q.set(k, sv)
  })
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export interface HotStockItem {
  symbol: string
  market: string
  name: string
  price: number | null
  change_pct: number | null
  turnover: number | null
  volume?: number | null
  price_kind?: string | null
  trade_date?: string | null
  freshness?: { status?: string | null; [key: string]: unknown } | null
  provider?: string | null
  adjustment_mode?: string | null
  change_basis?: string | null
  units?: Record<string, string> | null
  availability?: { status?: string | null; [key: string]: unknown } | string | null
}

export interface HotBoardItem {
  code: string
  name: string
  change_pct: number | null
  turnover: number | null
  constituent_provenance?: Array<Pick<HotStockItem,
    'symbol' | 'market' | 'price_kind' | 'trade_date' | 'freshness' | 'provider' |
    'adjustment_mode' | 'change_basis' | 'units' | 'availability'>>
}

export interface TaiwanOfficialDiscoveryRequest {
  pe_max?: number
  pb_max?: number
  dividend_yield_min_pct?: number
  revenue_yoy_min_pct?: number
  institutional_net_min_shares?: number
  limit?: number
}

export interface TaiwanOfficialDiscoveryConditionResult {
  condition: string
  label: string
  operator: '<=' | '>='
  threshold: string
  unit: string
  value: string | number | null
  data_date: string | null
  passed: boolean
  reason: string | null
  explanation: string
}

export interface TaiwanOfficialDiscoveryCandidate {
  instrument_id: string
  symbol: string
  venue: 'TWSE' | 'TPEX'
  market: 'TW'
  security_type: 'EQUITY' | 'ETF' | 'unknown'
  name: string
  matched: boolean
  price: { turnover: number | null; change_pct: number | null; trade_date: string | null }
  values: Record<string, string | number | null>
  data_dates: {
    valuation: string | null
    monthly_revenue: string | null
    institutional_flows: string | null
  }
  condition_results: Record<string, TaiwanOfficialDiscoveryConditionResult>
  data_evidence?: Record<string, {
    source_contract: string | null
    source_url: string | null
    source_received_at_utc: string | null
    acquired_at: string | null
    first_observed_at: string | null
    publication_time: string | null
    source_served_at?: string | null
    report_date: string | null
    capture_id: string | null
    revision: number | null
    units: Record<string, string>
    coverage: unknown
    presence: string
    payload_sha256: string | null
    error: string | null
    dividend_reference_year?: number | null
    financial_reference_year?: number | null
    financial_reference_quarter?: number | null
    dividend_yield_interpretation?: string
  }>
  explanations: string[]
  exclusion_reasons: string[]
}

export interface TaiwanOfficialDiscoveryResponse {
  market: 'TW'
  provider: 'twmd_official'
  selectors: Record<string, string>
  conditions: Record<string, { label: string; unit: string; dataset: string; operator: string; threshold: string }>
  scope: {
    universe: string
    catalog_count?: number
    eligible_catalog_count?: number
    price_universe_cap?: number
    price_universe_selected_count?: number
    price_universe_scanned?: number
    price_pool_candidates?: number
    candidate_limit: number
    candidates_selected: number
    candidates_examined?: number
    candidates_evaluated?: number
    candidates_timed_out?: number
    matched_count: number
    excluded_count: number
    partial_scan: boolean
    partial_reasons: string[]
    price_data_dates?: string[]
    research_data_dates?: Record<string, string[]>
    request_counts: {
      total: number
      cache_hits: number
      failed: number
      catalog?: number
      price_snapshots?: number
      valuation?: number
      monthly_revenue?: number
      institutional_flows?: number
      total_http_attempts: number
      http_attempts: Record<string, number>
    }
    freshness_rules?: Record<string, string>
    institutional_flow_scope?: string
    scan_status?: string
    limits?: Record<string, number>
  }
  matches: TaiwanOfficialDiscoveryCandidate[]
  excluded: TaiwanOfficialDiscoveryCandidate[]
}

export const discoveryApi = {
  listHotStocks: (params?: {
    market?: 'CN' | 'HK' | 'US' | 'TW'
    mode?: 'turnover' | 'gainers' | 'for_you'
    limit?: number
  }) =>
    fetchAPI<HotStockItem[]>(
      withQuery('/discovery/stocks', {
        market: params?.market,
        mode: params?.mode,
        limit: params?.limit,
      })
    ),

  listHotBoards: (params?: {
    market?: 'CN' | 'HK' | 'US' | 'TW'
    mode?: 'gainers' | 'turnover' | 'hot'
    limit?: number
  }) =>
    fetchAPI<HotBoardItem[]>(
      withQuery('/discovery/boards', {
        market: params?.market,
        mode: params?.mode,
        limit: params?.limit,
      })
    ),

  listBoardStocks: (
    boardCode: string,
    params?: {
      mode?: 'gainers' | 'turnover' | 'hot'
      limit?: number
    }
  ) =>
    fetchAPI<HotStockItem[]>(
      withQuery(`/discovery/boards/${encodeURIComponent(boardCode)}/stocks`, {
        mode: params?.mode,
        limit: params?.limit,
      })
    ),

  screenTaiwanOfficialStocks: (params: TaiwanOfficialDiscoveryRequest) =>
    fetchAPI<TaiwanOfficialDiscoveryResponse>('/discovery/stocks/screen', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 35_000,
    }),
}
