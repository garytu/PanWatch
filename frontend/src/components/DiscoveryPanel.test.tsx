// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://localhost"}
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { dashboardApi, discoveryApi } = vi.hoisted(() => ({
  dashboardApi: {
    watchlist: vi.fn(),
    portfolioSummary: vi.fn(),
  },
  discoveryApi: {
    listHotBoards: vi.fn(),
    listHotStocks: vi.fn(),
    screenTaiwanOfficialStocks: vi.fn(),
  },
}))

vi.mock('@panwatch/api', () => ({ dashboardApi, discoveryApi }))
vi.mock('@/lib/utils', async () => {
  const React = await import('react')
  return {
    useLocalStorage: (key: string, fallback: unknown) => React.useState(
      key.includes('discoverMarket') ? 'TW' : key.includes('discoverTab') ? 'stocks' : fallback,
    ),
  }
})
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}))

import DiscoveryPanel from './DiscoveryPanel'

function officialResponse() {
  return {
      market: 'TW',
      provider: 'twmd_official',
      selectors: {
        daily_start_date: '2026-09-07',
        daily_end_date: '2026-10-06',
        revenue_start_month: '2025-10',
        revenue_end_month: '2026-09',
      },
      conditions: {
        pe_max: { label: '本益比上限', unit: '倍', dataset: 'valuation', operator: '<=', threshold: '20' },
      },
      scope: {
        universe: 'active TWSE/TPEX EQUITY plus supported ETF',
        eligible_catalog_count: 1800,
        price_universe_selected_count: 1800,
        price_universe_scanned: 1800,
        candidate_limit: 20,
        candidates_selected: 1,
        candidates_examined: 1,
        matched_count: 1,
        excluded_count: 1,
        partial_scan: true,
        partial_reasons: ['candidate_cap_reached'],
        price_data_dates: ['2026-10-06'],
        request_counts: { total: 4, total_http_attempts: 3, cache_hits: 1, failed: 0, http_attempts: {} },
        institutional_flow_scope: 'one latest available day only',
      },
      matches: [{
        instrument_id: 'TWSE:2330', symbol: '2330', venue: 'TWSE', market: 'TW', security_type: 'EQUITY',
        name: '台積電', matched: true, price: { turnover: 100000000, change_pct: 1, trade_date: '2026-10-06' },
        values: { pe_ratio: '18' },
        data_dates: { valuation: '2026-10-06', monthly_revenue: null, institutional_flows: null },
        condition_results: {
          pe_max: { condition: 'pe_max', label: '本益比上限', operator: '<=', threshold: '20', unit: '倍', value: '18', data_date: '2026-10-06', passed: true, reason: 'matched', explanation: '本益比上限 18 倍（資料日 2026-10-06）符合 <= 20 倍。' },
        },
        data_evidence: { valuation: { source_contract: 'valuation-v1', dividend_reference_year: 2025 } },
        explanations: ['本益比上限 18 倍（資料日 2026-10-06）符合 <= 20 倍。'],
        exclusion_reasons: [],
      }],
      excluded: [{
        instrument_id: 'TPEX:6488', symbol: '6488', venue: 'TPEX', market: 'TW', security_type: 'EQUITY',
        name: '測試公司', matched: false, price: { turnover: 90000000, change_pct: 0, trade_date: '2026-10-06' },
        values: {}, data_dates: { valuation: null, monthly_revenue: null, institutional_flows: null },
        condition_results: {}, explanations: ['本益比上限資料缺漏或未覆蓋，條件未通過。'],
        exclusion_reasons: ['coverage_missing'],
      }],
  }
}

describe('Taiwan official discovery UI', () => {
  afterEach(cleanup)
  beforeEach(() => {
    dashboardApi.watchlist.mockResolvedValue([])
    dashboardApi.portfolioSummary.mockResolvedValue({ accounts: [] })
    discoveryApi.listHotStocks.mockResolvedValue([])
    discoveryApi.listHotBoards.mockResolvedValue([])
    discoveryApi.screenTaiwanOfficialStocks.mockReset()
  })

  it('submits an opt-in condition and presents official dates, scope, explanations, and exclusions', async () => {
    discoveryApi.screenTaiwanOfficialStocks.mockResolvedValue(officialResponse())

    const onOpenStock = vi.fn()
    render(<DiscoveryPanel monitorStocks={[]} onOpenStock={onOpenStock} />)
    fireEvent.click(screen.getByRole('button', { name: '設定條件' }))
    fireEvent.change(screen.getByLabelText('本益比上限'), { target: { value: '20' } })
    fireEvent.click(screen.getByRole('button', { name: '執行官方條件篩選' }))

    await waitFor(() => expect(discoveryApi.screenTaiwanOfficialStocks).toHaveBeenCalledWith({ pe_max: 20, limit: 20 }))
    expect(await screen.findByText(/價格資料日：2026-10-06/)).toBeTruthy()
    expect(screen.getByText(/官方資料條件選股/)).toBeTruthy()
    expect(screen.getByText(/資料日 2026-10-06/)).toBeTruthy()
    expect(screen.getByText(/部分掃描：僅篩選成交額前 20 檔候選/)).toBeTruthy()
    expect(screen.getByText(/股利參考年度：2025/)).toBeTruthy()
    fireEvent.click(screen.getByText(/查看排除項目與原因/))
    expect(await screen.findByText(/查詢期別缺少資料/)).toBeTruthy()
    const candidate = screen.getByRole('button', { name: /台積電/ })
    expect(within(candidate).getByText(/valuation-v1/)).toBeTruthy()
    fireEvent.click(candidate)
    expect(onOpenStock).toHaveBeenCalledWith('TWSE:2330', 'TW', '台積電', false)
    expect(screen.getByText(/來源未提供發布時間/)).toBeTruthy()
  })

it('shows a busy scan as incomplete instead of reporting that stocks failed the conditions', async () => {
  const response = officialResponse()
  discoveryApi.screenTaiwanOfficialStocks.mockResolvedValue({
    ...response, matches: [], excluded: [],
    scope: { ...response.scope, matched_count: 0, excluded_count: 0, partial_scan: true,
      scan_status: 'discovery_concurrency_limit', partial_reasons: ['discovery_concurrency_limit'] },
  })
  render(<DiscoveryPanel monitorStocks={[]} onOpenStock={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '設定條件' }))
  fireEvent.change(screen.getByLabelText('本益比上限'), { target: { value: '20' } })
  fireEvent.click(screen.getByRole('button', { name: '執行官方條件篩選' }))
  expect(await screen.findByText(/本次沒有可用的篩選結果/)).toBeTruthy()
  expect(screen.queryByText('目前候選中沒有符合全部條件的股票。')).toBeNull()
  cleanup()
})

it('discards the old response after conditions change and allows a new screen', async () => {
  let finishFirst: (value: unknown) => void = () => {}
  discoveryApi.screenTaiwanOfficialStocks.mockReset()
  discoveryApi.screenTaiwanOfficialStocks.mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve }))
  const newer = officialResponse()
  newer.matches[0].name = '更新後候選'
  discoveryApi.screenTaiwanOfficialStocks.mockResolvedValueOnce(newer)
  render(<DiscoveryPanel monitorStocks={[]} onOpenStock={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '設定條件' }))
  fireEvent.change(screen.getByLabelText('本益比上限'), { target: { value: '20' } })
  fireEvent.click(screen.getByRole('button', { name: '執行官方條件篩選' }))
  fireEvent.change(screen.getByLabelText('本益比上限'), { target: { value: '10' } })
  fireEvent.click(screen.getByRole('button', { name: '執行官方條件篩選' }))
  expect(await screen.findByRole('button', { name: /更新後候選/ })).toBeTruthy()
  await act(async () => { finishFirst(officialResponse()) })
  expect(screen.queryByRole('button', { name: /台積電/ })).toBeNull()
  const calls = discoveryApi.screenTaiwanOfficialStocks.mock.calls
  expect(calls[calls.length - 1]?.[0]).toEqual({ pe_max: 10, limit: 20 })
  cleanup()
})

})
