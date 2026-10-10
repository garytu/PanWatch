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

export type ResearchBlockName =
  | 'valuation'
  | 'institutional_flows'
  | 'company_profile'
  | 'monthly_revenues'
  | 'margin_short_sale'
  | 'shareholder_distribution'
  | 'broker_flow'
  | 'financial_statements'
  | 'corporate_actions'
  | 'benchmark_comparison'

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
    fiscal_year: number
    fiscal_quarter: number
    statement: string | null
  }
  requested_blocks?: ResearchBlockName[]
  blocks: Partial<{
    valuation: ResearchDataBlock<{ instrument_id: string; observations: Array<Record<string, unknown>> }>
    institutional_flows: ResearchDataBlock<{ instrument_id: string; native_unit: string; observations: Array<Record<string, unknown>> }>
    company_profile: ResearchDataBlock<Record<string, any>>
    monthly_revenues: ResearchDataBlock<Record<string, any>>
    margin_short_sale: ResearchDataBlock<Record<string, any>>
    shareholder_distribution: ResearchDataBlock<Record<string, any>>
    broker_flow: ResearchDataBlock<BrokerFlowResearchData>
    financial_statements: ResearchDataBlock<FinancialStatementsResearchData>
    corporate_actions: ResearchDataBlock<CorporateActionsResearchData>
    benchmark_comparison: ResearchDataBlock<BenchmarkComparisonResearchData>
  }>
  limitations: {
    financial_statements: {
      status: 'limited_scope' | string
      message: string
    }
  }
}

export interface BenchmarkComparisonResearchData {
  instrument_id: string
  benchmark_id: 'TAIEX' | 'TPEX'
  venue: 'TWSE' | 'TPEX'
  requested_range: { start_date: string; end_date: string; inclusive: true }
  price_basis: 'raw_price'
  stock_series: {
    instrument_id: string
    provider: string | null
    timeframe: string
    price_kind: string
    adjustment_mode: string
    unit: 'TWD/share'
    returned_count: number | null
    partial: boolean | null
    selection: string
  }
  benchmark_series: {
    benchmark_id: 'TAIEX' | 'TPEX'
    venue: 'TWSE' | 'TPEX'
    provider: string | null
    source_alias: string | null
    source_contract: string | null
    source_url: string | null
    timeframe: 'day'
    price_kind: 'benchmark_index'
    adjustment_mode: 'raw_price_index'
    unit: 'index_points'
    returned_count: number | null
    partial: boolean | null
    truncated: boolean | null
    coverage_window: Record<string, unknown> | null
  }
  common_observation_dates: string[]
  observations: Array<{
    trade_date: string
    stock_close: string
    stock_unit: 'TWD/share'
    stock_price_kind: 'eod'
    stock_adjustment_mode: 'raw'
    stock_coverage?: {
      dataset: string | null
      partition_key: string
      status: string
      record_count: number | null
      acquired_at: string | null
      checksum: string | null
    }
    benchmark_close: string
    benchmark_unit: 'index_points'
    benchmark_basis: 'raw_price_index'
    benchmark_revision: number
    benchmark_capture_id: string
    benchmark_captured_at: string
    benchmark_source_contract: string
    benchmark_source_alias: string
    benchmark_source_url?: string
    benchmark_request_scope: string
    benchmark_payload_sha256: string
  }>
  comparison: null | {
    requested_start_date: string
    requested_end_date: string
    calculation_start_date: string
    calculation_end_date: string
    observation_count: number
    basis: 'raw_price_return'
    stock_return_pct: string
    benchmark_return_pct: string
    relative_return_percentage_points: string
    relative_return_definition: string
  }
}

export interface CorporateActionResearchResult {
  instrument_id: string
  block: ResearchDataBlock<CorporateActionsResearchData>
}

export interface CorporateActionResearchPart<T> {
  status: string
  reason: string
  http_status: number | null
  data: T[]
  endpoint: string
  selectors: Record<string, string>
  dataset_coverage: 'unknown'
  product_history_floor: string
}

export interface ExRightDividendResult {
  effective_date: string
  instrument_id: string
  symbol: string
  observed_name: string
  action_kind: 'ex_right' | 'ex_dividend' | 'ex_right_dividend'
  prior_close: string
  reference_price: string
  rights_dividend_value: string
  limit_up_price: string
  limit_down_price: string
  opening_auction_basis: string
  dividend_adjusted_reference_price: string
  provider: string
  currency: string
}

export interface CapitalReductionResult {
  recovery_date: string
  instrument_id: string
  symbol: string
  observed_name: string
  reduction_reason: 'loss_offset' | 'return_of_capital'
  pre_suspension_close: string
  recovery_reference_price: string
  limit_up_price: string
  limit_down_price: string
  opening_auction_basis: string
  ex_right_reference_price: string | null
  provider: string
  currency: string
}

export interface CorporateActionsResearchData {
  instrument_id: string
  ex_right_dividend: CorporateActionResearchPart<ExRightDividendResult>
  capital_reduction: CorporateActionResearchPart<CapitalReductionResult>
  known_event_dates: Array<{ date: string; kind: string; dataset: 'TWT49U' | 'TWTAUU' }>
  price_interpretation: {
    prior_close_and_reference_price_are_not_cash_dividend_amounts: true
    rights_dividend_value_is_combined_adjustment_not_cash_dividend: true
    announcement_time: null
    payment_time: null
    raw_daily_bars_are_not_adjusted_by_these_annotations: true
  }
}

export interface FinancialStatementsResearchData {
  instrument_id: string
  fiscal_year: number
  fiscal_quarter: number
  report_scope: 'consolidated'
  statement: FinancialStatementName | null
  qualification: {
    status: 'qualified' | 'pending' | 'unsupported'
    reason: string
    industry_code: string | null
    catalog_evidence: Record<string, unknown> | null
    profile_evidence: Record<string, unknown> | null
  }
  coverage: {
    status: 'AVAILABLE' | 'MISSING'
    reason: string
    latest_discovery_presence: 'present' | 'not_advertised' | 'missing'
    capture_id: string | null
    original_received_at_utc: string | null
  }
  report: FinancialStatementReport | null
  facts: FinancialStatementFact[]
  total_fact_count: number
  returned_fact_count: number
  truncated: boolean
}

export type FinancialStatementName = 'balance_sheet' | 'comprehensive_income' | 'cash_flows'

export type FinancialPeriodPresence = 'present_readable' | 'present_unreadable' | 'missing' | 'unsupported' | 'unknown'

export interface FinancialPeriodAuthority {
  capture_id: string
  document_id: string
  semantic_revision_id: string
  revision_number: number
  source_contract: string
  parser_contract: string
  original_received_at_utc: string
  document_first_observed_at_utc: string
  semantic_revision_first_observed_at_utc: string
  latest_observed_at_utc: string
}

export interface FinancialPeriodEntry {
  fiscal_year: number
  fiscal_quarter: number
  report_scope: 'consolidated' | 'individual'
  presence: FinancialPeriodPresence
  reason: string
  statement_coverage: Partial<Record<FinancialStatementName, { presence: FinancialPeriodPresence; fact_count: number }>>
  authority: FinancialPeriodAuthority | null
}

export interface FinancialStatementPeriodsIndex {
  contract_version: 'twmd.financial-statement-periods/v1'
  instrument_id: string
  venue: 'TWSE' | 'TPEX'
  source: 'mops_financial_statements'
  report_scope: 'consolidated' | 'individual'
  statement: FinancialStatementName | null
  limit: number
  supported_scope: {
    venues: string[]
    source: string
    source_contract: string
    industry_codes: string[]
    security_types: string[]
    report_scopes: string[]
    statements: FinancialStatementName[]
  }
  window_start: [number, number]
  window_end: [number, number] | null
  qualification: {
    status: 'qualified' | 'pending' | 'unsupported'
    reason: string
    industry_code: string | null
    catalog: Record<string, unknown> | null
    profile: Record<string, unknown> | null
  }
  coverage: { status: 'complete' | 'partial' | 'unknown'; reason: string }
  periods: FinancialPeriodEntry[]
  next_cursor: string | null
  has_more: boolean
  latest_retained_period: FinancialPeriodEntry | null
  latest_readable_period: FinancialPeriodEntry | null
  served_at_utc: string
  endpoint: '/api/v1/financial-statement-periods'
}

export interface TaiwanFinancialPeriodIndexResult {
  instrument_id: string
  endpoint: '/api/v1/financial-statement-periods'
  index_status: 'available' | 'partial' | 'unknown' | 'unsupported' | 'error'
  reason: string
  selectors: {
    instrument_id: string
    report_scope: 'consolidated' | 'individual'
    statement: FinancialStatementName | null
    limit: number
    cursor?: string
    venue?: 'TWSE' | 'TPEX'
    source?: 'mops_financial_statements'
  }
  index: FinancialStatementPeriodsIndex | null
  error: { code: string; http_status: number | null } | null
}

export interface FinancialStatementReport {
  document_id: string
  capture_id: string
  semantic_revision_id: string
  member_filename: string
  source_url: string
  raw_sha256: string
  source_contract: string
  parser_contract: string
  original_received_at_utc: string
  document_first_observed_at_utc: string
  semantic_revision_first_observed_at_utc: string
  latest_observed_at_utc: string
  published_at_utc: null
  amendment_status: 'unknown'
}

export interface FinancialStatementFact {
  statement: FinancialStatementName
  occurrence_ordinal: number
  concept_qname: string
  context: {
    source_id: string
    entity_identifier: string
    entity_scheme: string
    period: {
      kind: 'instant' | 'duration'
      instant: string | null
      start_date: string | null
      end_date: string | null
    }
    dimensions: Array<{ axis_qname: string; member_qname: string }>
  }
  unit: { source_id: string; numerator: string[]; denominator: string[] }
  value: string | null
  is_nil: boolean
  lexical_value: string
  format_qname: string | null
  scale: number | null
  sign: string | null
  decimals: string | null
  precision: string | null
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
  fiscal_year?: number
  fiscal_quarter?: number
  statement?: FinancialStatementName
  blocks?: ResearchBlockName[]
}

export interface TaiwanFinancialPeriodsParams {
  report_scope?: 'consolidated' | 'individual'
  statement?: FinancialStatementName
  limit?: number
  cursor?: string
}

function withQuery(path: string, params: Record<string, string | number | string[] | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, rawValue] of Object.entries(params)) {
    if (Array.isArray(rawValue)) {
      for (const value of rawValue) query.append(key, value)
      continue
    }
    const value = rawValue as string | number | undefined
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  }
  const suffix = query.toString()
  return suffix ? `${path}?${suffix}` : path
}

export const researchApi = {
  taiwan: (instrumentId: string, params: TaiwanResearchParams = {}, options?: { signal?: AbortSignal }) =>
    fetchAPI<TaiwanResearchPayload>(
      withQuery('/research/taiwan', { instrument_id: instrumentId, ...params }),
      { timeoutMs: 30_000, ...options },
    ),
  financialPeriods: (
    instrumentId: string,
    params: TaiwanFinancialPeriodsParams = {},
    options?: { signal?: AbortSignal },
  ) => fetchAPI<TaiwanFinancialPeriodIndexResult>(
    withQuery('/research/taiwan/financial-periods', {
      instrument_id: instrumentId,
      report_scope: params.report_scope || 'consolidated',
      statement: params.statement,
      limit: params.limit ?? 40,
      cursor: params.cursor,
    }),
    { timeoutMs: 10_000, ...options },
  ),
  corporateActions: (instrumentId: string, startDate: string, endDate: string) =>
    fetchAPI<CorporateActionResearchResult>(
      withQuery('/research/taiwan/corporate-actions', {
        instrument_id: instrumentId,
        start_date: startDate,
        end_date: endDate,
      }),
      { timeoutMs: 30_000 },
    ),
}
