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
  }
  limitations: {
    financial_statements: {
      status: 'not_integrated' | string
      data: null
      message: string
    }
  }
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
