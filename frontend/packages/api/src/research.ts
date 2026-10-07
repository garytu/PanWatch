import { fetchAPI } from './client'

export interface ResearchDataBlock<T = Record<string, unknown>> {
  data: T | null
  status: string
  reason: string
  evidence: Record<string, unknown>
}

export interface ResearchFreshness {
  frequency: string
  data_period: string | null
  report_date: string | null
  publication_time: string | null
  source_received_at_utc: string | null
  source_receipt_age_seconds: number | null
  data_period_age_days: number | null
  first_observed_at: string | null
  source_served_at: string | null
  evaluated_at_utc: string
  publisher_sla: null | string
  age_status: string
  frequency_hint: string
  coverage: Record<string, unknown>
  latest_snapshot?: {
    report_date: string | null
    source_received_at_utc: string | null
    source_receipt_age_seconds: number | null
    data_period_age_days: number | null
  }
}

export interface TaiwanResearchPayload {
  instrument_id: string
  instrument: {
    venue: 'TWSE' | 'TPEX'
    symbol: string
    security_type: string
    is_active: boolean
    name: string | null
  } | null
  selectors: {
    start_date: string
    end_date: string
    start_month: string
    end_month: string
  }
  blocks: {
    valuation: ResearchDataBlock<{ instrument_id: string; observations: Array<Record<string, unknown>> }>
    institutional_flows: ResearchDataBlock<{ instrument_id: string; native_unit: string; observations: Array<Record<string, unknown>> }>
    company_profile: ResearchDataBlock<Record<string, any>>
    monthly_revenues: ResearchDataBlock<Record<string, any>>
    margin_short_sale: ResearchDataBlock<Record<string, any>>
    shareholder_distribution: ResearchDataBlock<Record<string, any>>
    broker_flow: ResearchDataBlock<BrokerFlowResearchData>
  }
  limitations: {
    financial_statements: {
      status: 'not_integrated' | string
      data: null
      message: string
    }
  }
}

export interface BrokerFlowResearchData {
  instrument_id: string
  quantity_range: {
    start_date?: string
    end_date?: string
    max_calendar_days?: number
    status: string
    reason: string
    data?: BrokerFlowQuantityRow[]
  }
  quantity_observations: BrokerFlowQuantityRow[]
  quantity_groups: BrokerFlowQuantityGroup[]
  coverage_range: {
    start_date?: string
    end_date?: string
    max_calendar_days?: number
    status: string
    reason: string
    data?: BrokerFlowCoverageRow[]
  }
  coverage_observations: BrokerFlowCoverageRow[]
  coverage_status_counts_by_provider: Record<string, Record<string, number>>
  price_levels: {
    trade_date: string | null
    status: string
    reason: string
    observations: BrokerFlowPriceLevelRow[]
    revision_consistency_with_same_date_quantities: string
    no_rows_interpretation: string | null
  }
  revision_consistency_warnings?: string[]
}

export interface BrokerFlowQuantityRow {
  provider: 'capital' | 'twse'
  dataset: 'broker_flow'
  instrument_id: string
  symbol: string
  trade_date: string
  source_branch_key: string
  branch_code: string
  branch_name: string
  native_unit: 'lots' | 'shares'
  precision_shares: number
  buy_native: number
  sell_native: number
  net_native: number
  buy_vwap: string | null
  sell_vwap: string | null
  revision_id: string | null
}

export interface BrokerFlowCoverageRow {
  provider: 'capital' | 'twse'
  dataset: 'broker_flow'
  instrument_id: string
  trade_date: string
  status: 'AVAILABLE' | 'EMPTY' | 'FAILED' | 'CLOSED' | 'MISSING'
  record_count: number
  revision_id: string | null
  failure_reason: string | null
}

export interface BrokerFlowQuantityGroup {
  provider: 'capital' | 'twse'
  native_unit: 'lots' | 'shares'
  precision_shares: number
  top_buy: Array<Record<string, unknown>>
  top_sell: Array<Record<string, unknown>>
  top_n: number
  top_n_buy_native: number
  top_n_sell_native: number
  top_n_buy_concentration_pct: string | null
  top_n_sell_concentration_pct: string | null
  observed_buy_denominator_native: number
  observed_sell_denominator_native: number
  denominator_definition: string
  coverage_complete_for_source_dates: boolean
  coverage_status_counts: Record<string, number>
  coverage_missing_dates: string[]
  coverage_reconciliation_dates?: string[]
  revision_ids: string[]
}

export interface BrokerFlowPriceLevelRow {
  provider: 'twse'
  dataset: 'broker_flow'
  instrument_id: string
  symbol: string
  trade_date: string
  source_branch_key: string
  branch_code: string
  branch_name: string
  price: string
  buy_native: number
  sell_native: number
  native_unit: 'shares'
  precision_shares: 1
  revision_id: string
}

export interface TaiwanResearchParams {
  start_date?: string
  end_date?: string
  start_month?: string
  end_month?: string
}

function withQuery(path: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value)
  }
  const suffix = query.toString()
  return suffix ? `${path}?${suffix}` : path
}

export const researchApi = {
  taiwan: (instrumentId: string, params: TaiwanResearchParams = {}) =>
    fetchAPI<TaiwanResearchPayload>(
      withQuery('/research/taiwan', { instrument_id: instrumentId, ...params }),
      { timeoutMs: 30_000 },
    ),
}
