// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://localhost"}
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { recommendationsApi, stocksApi } = vi.hoisted(() => ({
  recommendationsApi: {
    listStrategySignals: vi.fn(), listEntryCandidates: vi.fn(),
    getStrategyStats: vi.fn(), listStrategyCatalog: vi.fn(),
  },
  stocksApi: { list: vi.fn() },
}))
vi.mock('@panwatch/api', () => ({ recommendationsApi, stocksApi }))
vi.mock('@/lib/utils', async () => {
  const React = await import('react')
  return { useLocalStorage: (_key: string, fallback: unknown) => React.useState(fallback) }
})
vi.mock('@panwatch/biz-ui/components/stock-insight-modal', () => ({ default: () => null }))
vi.mock('@/components/FactorWeightsPanel', () => ({ default: () => null }))
vi.mock('@/components/SignalScoreShareCard', () => ({ default: () => null }))
import OpportunitiesPage from './Opportunities'

beforeEach(() => {
  vi.clearAllMocks()
  stocksApi.list.mockResolvedValue([])
  recommendationsApi.getStrategyStats.mockResolvedValue(null)
  recommendationsApi.listStrategyCatalog.mockResolvedValue({ items: [] })
  recommendationsApi.listStrategySignals.mockResolvedValue({ snapshot_date: '2026-10-10', count: 0, items: [] })
})
afterEach(cleanup)

describe('Taiwan opportunity groups', () => {
  it('queries ordinary stocks separately and switches to the ETF group', async () => {
    render(<OpportunitiesPage />)
    await waitFor(() => expect(recommendationsApi.listStrategySignals).toHaveBeenCalledWith(expect.objectContaining({ market: 'TW', instrument_group: 'ORDINARY' })))
    fireEvent.click(screen.getByRole('button', { name: '台股 ETF' }))
    await waitFor(() => expect(recommendationsApi.listStrategySignals).toHaveBeenLastCalledWith(expect.objectContaining({ instrument_group: 'ETF' })))
  })

  it('keeps the selected group and ETF label when falling back to a candidate snapshot', async () => {
    recommendationsApi.listStrategySignals.mockRejectedValue(new Error('超時'))
    recommendationsApi.listEntryCandidates.mockResolvedValue({
      snapshot_date: '2026-10-10', count: 1,
      items: [{ id: 1, stock_symbol: 'TWSE:00631L', stock_market: 'TW', stock_name: '測試 ETF', status: 'active', score: 81, action: 'buy', action_label: '建倉', instrument_group: 'ETF', instrument_id: 'TWSE:00631L', instrument_authority: { security_type: 'ETF' }, scan_run_refs: [{ scan_run_id: 73, revision: 1, manifest_hash: 'sealed', source_mode: 'turnover' }] }],
    })
    render(<OpportunitiesPage />)
    fireEvent.click(screen.getByRole('button', { name: '台股 ETF' }))
    await waitFor(() => expect(recommendationsApi.listEntryCandidates).toHaveBeenLastCalledWith(expect.objectContaining({
      market: 'TW', instrument_group: 'ETF', min_score: 0,
    })))
    expect(await screen.findByText('ETF 機會')).toBeTruthy()
    expect(screen.getByText('候選原始分 81')).toBeTruthy()
    expect(screen.getByText(/排名分門檻在此降級模式下不可用/)).toBeTruthy()
  })
})


it('sorts instruments by raw ranking ahead of source pool and rounded display ties', async () => {
  const row = (id: number, name: string, ranking: number, source: string) => ({
    id, stock_symbol: `TWSE:${2300 + id}`, instrument_id: `TWSE:${2300 + id}`,
    stock_market: 'TW', stock_name: name, instrument_group: 'EQUITY',
    status: 'active', action: 'buy', action_label: '建倉', strategy_code: 'test',
    score: 99, rank_score: 82, ranking_value: ranking,
    ranker_version: 'ranker-v2', score_version: 'ranker-v2', source_pool: source,
  })
  recommendationsApi.listStrategySignals.mockResolvedValue({ items: [
    row(1, '較弱市場股', 130, 'market_scan'), row(2, '較強關注股', 130.00000001, 'watchlist'),
  ] })
  render(<OpportunitiesPage />)
  const stronger = await screen.findByText('較強關注股')
  const weaker = screen.getByText('較弱市場股')
  expect(stronger.compareDocumentPosition(weaker) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('shows missing v2 rank without substituting candidate score or inventing a cap count', async () => {
  recommendationsApi.listStrategySignals.mockResolvedValue({ items: [{
    id: 1, stock_symbol: 'TWSE:2330', stock_market: 'TW', stock_name: '缺失排名股',
    instrument_group: 'EQUITY', status: 'active', action: 'watch', action_label: '觀望',
    strategy_code: 'test', score: 99, rank_score: null, ranking_value: null,
    ranker_version: 'ranker-v2', score_version: 'ranker-v2', source_pool: 'watchlist',
  }] })
  render(<OpportunitiesPage />)
  expect(await screen.findByText('排名分 v2 --')).toBeTruthy()
  expect(screen.queryByText(/台股普通股上限/)).toBeNull()
})
