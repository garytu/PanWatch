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
    await waitFor(() => expect(recommendationsApi.listEntryCandidates).toHaveBeenLastCalledWith(expect.objectContaining({ market: 'TW', instrument_group: 'ETF' })))
    expect(await screen.findByText('ETF 機會')).toBeTruthy()
  })
})
