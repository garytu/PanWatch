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

export const insightApi = {
  quote: <T>(symbol: string, market: string) =>
    fetchAPI<T>(`/quotes/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`),

  klineSummary: <T>(symbol: string, market: string) =>
    fetchAPI<T>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(market)}`),

  klines: <T>(symbol: string, params: { market: string; days?: number; interval?: string }) =>
    fetchAPI<T>(
      withQuery(`/klines/${encodeURIComponent(symbol)}`, {
        market: params.market,
        days: params.days,
        interval: params.interval,
      })
    ),

  suggestions: <T>(
    symbol: string,
    params: { market?: string; limit?: number; include_expired?: boolean }
  ) =>
    fetchAPI<T>(
      withQuery(`/suggestions/${encodeURIComponent(symbol)}`, {
        market: params.market,
        limit: params.limit,
        include_expired: params.include_expired,
      })
    ),

  news: <T>(params: Record<string, QueryValue>) => fetchAPI<T>(withQuery('/news', params)),

  history: <T>(params: Record<string, QueryValue>) => fetchAPI<T>(withQuery('/history', params)),

  portfolioSummary: <T>(params?: { include_quotes?: boolean }) =>
    fetchAPI<T>(
      withQuery('/portfolio/summary', {
        include_quotes: params?.include_quotes,
      })
    ),

  addPositionEval: (params: AddPositionEvalParams) =>
    fetchAPI<AddPositionEvalResult>('/insights/add-position-eval', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 60000, // AI 評估較慢,放寬超時
    }),

  announcementEval: (params: {
    symbol: string
    market: string
    model_id?: number
    venue?: 'TWSE' | 'TPEX'
    start_date?: string
    end_date?: string
    source?: 'current' | 'history' | 'both'
  }) =>
    fetchAPI<AnnouncementEvalResult>('/insights/announcement-eval', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 40000,
    }),

  materialInformation: (params: {
    instrument_id: string
    start_date: string
    end_date: string
    source: 'current' | 'history'
    limit?: number
  }) =>
    fetchAPI<MaterialInformationQueryResult>(
      withQuery('/research/taiwan/material-information', params),
      { timeoutMs: 30_000 },
    ),
}

export interface MaterialInformationEvent {
  instrument_id: string
  symbol: string
  announcement_date: string
  announced_at: string
  company_name: string
  subject: string
  clause: string
  fact_date: string
  detail: string
  content_hash: string
  revision: number
  revision_count: number | null
  source_event_id: string | null
  provider_key: string | null
  first_observed_at_utc: string
  event_first_observed_at_utc: string
  latest_observed_at_utc: string
  capture_id: string
  acquisition_date: string
  payload_sha256: string
  report_date: string | null
  detail_subject_raw: string | null
  speaker_name: string | null
  speaker_title: string | null
  speaker_phone: string | null
  source_generated_at: string | null
  row_fingerprint?: string | null
  source_reference: {
    label: string
    method: 'POST'
    url: string
    selectors: Record<string, string>
    is_navigable_permalink: false
  } | null
}

export interface MaterialInformationBlock {
  data: { instrument_id: string; source_family: 'current' | 'history'; events: MaterialInformationEvent[] } | null
  status: string
  reason: string
  evidence: Record<string, any>
}

export interface MaterialInformationQueryResult {
  instrument_id: string
  source_family: 'current' | 'history'
  block: MaterialInformationBlock
}

export interface AnnouncementToneItem {
  title: string
  time: string
  tone: string // 利好 / 利空 / 中性
  summary: string
  source_family?: 'current' | 'history' | string
  source_identity?: string | null
  content_hash?: string | null
  revision?: number | null
  source_reference?: MaterialInformationEvent['source_reference']
  evidence?: Record<string, any>
  original_text?: {
    clause: string
    detail: string
  }
}

export interface AnnouncementEvalResult {
  symbol: string
  market: string
  items: AnnouncementToneItem[]
  instrument_id?: string
  date_filter?: { start_date: string; end_date: string }
  source_statuses?: Record<string, MaterialInformationBlock>
}

export interface AddPositionEvalParams {
  symbol: string
  market: string
  current_quantity: number
  current_cost: number
  add_quantity: number
  add_price: number
  model_id?: number
}

export interface AddPositionEvalResult {
  symbol: string
  market: string
  action: string // 加碼 / 建倉
  new_cost: number
  dilute_abs: number
  dilute_pct: number
  total_quantity: number
  total_invested: number
  verdict: string // 適合 / 謹慎 / 不適合 / 未知
  content: string // markdown 結論
}
