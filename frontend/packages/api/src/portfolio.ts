import { fetchAPI } from './client'

export interface PortfolioDiagnostics {
  position_count: number
  total_market_value: number
  hhi: number
  max_weight: number
  by_market: Record<string, number>
  by_strategy: Record<string, number>
  total_unrealized_pnl: number
  alerts: string[]
}

export interface BenchmarkCurvePoint {
  date: string
  portfolio: number
  benchmark: number
}

export interface PortfolioBenchmark {
  empty?: boolean
  reason?: string
  portfolio_return?: number
  benchmark_return?: number
  excess_return?: number
  information_ratio?: number
  relative_drawdown?: number
  days?: number
  benchmark_code?: string
  benchmark_label?: string
  curve?: BenchmarkCurvePoint[]
}

export const portfolioApi = {
  /** 真實持倉組合診斷(集中度/分佈/風險提示)。 */
  diagnostics: () => fetchAPI<PortfolioDiagnostics>('/portfolio/diagnostics'),

  /** 組合 vs 基準(超額/資訊比率/相對回檔 + 歸一化曲線)。 */
  benchmark: (params?: { days?: number; benchmark?: string }) =>
    fetchAPI<PortfolioBenchmark>(
      `/portfolio/benchmark?days=${params?.days ?? 60}&benchmark=${encodeURIComponent(params?.benchmark ?? '000300')}`,
      { timeoutMs: 60000 },
    ),

  /** 個股對組合收益的貢獻(誰拖累/貢獻)。 */
  attribution: (days = 60) =>
    fetchAPI<{ items: AttributionItem[] }>(`/portfolio/attribution?days=${days}`, { timeoutMs: 60000 }),

  /** 組合 AI 體檢(敘述結論 + 調倉建議)。 */
  aiReview: () => fetchAPI<PortfolioAiReview>('/portfolio/ai-review', { method: 'POST', timeoutMs: 60000 }),
}

export interface AttributionItem {
  symbol: string
  name: string
  market: string
  return_pct: number
  weight_pct: number
  contribution_pct: number
}

export interface PortfolioAiReview {
  empty?: boolean
  reason?: string
  content?: string
  top?: AttributionItem[]
  worst?: AttributionItem[]
}
