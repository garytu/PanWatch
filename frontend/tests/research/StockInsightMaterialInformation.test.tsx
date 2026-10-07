// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import { insightApi } from '@panwatch/api'

vi.mock('@panwatch/api', () => ({
  insightApi: {
    quote: vi.fn().mockResolvedValue({ name: '測試公司', current_price: null }),
    klineSummary: vi.fn().mockResolvedValue({ summary: null }),
    klines: vi.fn().mockResolvedValue({ klines: [] }),
    suggestions: vi.fn().mockResolvedValue([]), news: vi.fn().mockResolvedValue([]),
    history: vi.fn().mockResolvedValue([]), portfolioSummary: vi.fn().mockResolvedValue({ accounts: [] }),
    materialInformation: vi.fn(),
  },
  stocksApi: { list: vi.fn().mockResolvedValue([]) }, tradingAgentsApi: {},
}))
vi.mock('@panwatch/biz-ui', () => ({ getMarketBadge: () => 'TW' }))
vi.mock('@panwatch/biz-ui/components/InteractiveKline', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/stock-price-alert-panel', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/add-position-calculator', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/taiwan-research-panel', () => ({ TaiwanResearchPanel: () => null }))
vi.mock('@panwatch/biz-ui/components/suggestion-badge', () => ({ SuggestionBadge: () => null }))
vi.mock('@panwatch/biz-ui/components/kline-indicators', () => ({ KlineIndicators: () => null }))
vi.mock('@panwatch/biz-ui/components/technical-badge', () => ({ TechnicalBadge: () => null }))

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals() })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function payload(instrument: string, family: string, title?: string) {
  return { instrument_id: instrument, source_family: family, block: {
    status: title ? 'partial' : 'missing', reason: 'test', evidence: {},
    data: { instrument_id: instrument, source_family: family, events: title ? [{
      subject: title, source_event_id: `${instrument}-event`, detail: '原文', clause: '',
      announced_at: '2026-10-07T09:00:00+08:00', announcement_date: '2026-10-07',
      latest_observed_at_utc: '2026-10-07T02:00:00Z', revision: 1,
    }] : [] },
  } }
}

it('isolates late official responses after switching stocks in the mounted announcement modal', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-06T16:30:00Z'))
  const stored = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
  })
  localStorage.setItem('stock_insight_auto_refresh_enabled', 'false')
  const oldCurrent = deferred<any>(), oldHistory = deferred<any>()
  vi.mocked(insightApi.materialInformation).mockImplementation((params) => {
    if (params.instrument_id === 'TWSE:2330') return params.source === 'current' ? oldCurrent.promise : oldHistory.promise
    return Promise.resolve(payload(params.instrument_id, params.source, params.source === 'current' ? '新股票公告' : undefined)) as any
  })
  const { rerender } = render(<StockInsightModal open onOpenChange={() => {}} symbol="TWSE:2330" market="TW" stockName="測試公司" />)
  await waitFor(() => expect(insightApi.materialInformation).toHaveBeenCalledWith(expect.objectContaining({
    instrument_id: 'TWSE:2330', end_date: '2026-10-07', source: 'current',
  })))
  rerender(<StockInsightModal open onOpenChange={() => {}} symbol="TWSE:2608" market="TW" stockName="測試公司" />)
  await waitFor(() => expect(screen.getByRole('button', { name: '公告 (1)' })).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: '公告 (1)' }))
  await waitFor(() => expect(screen.getByText('新股票公告')).toBeTruthy())
  await act(async () => {
    oldCurrent.resolve(payload('TWSE:2330', 'current', '舊股票公告'))
    oldHistory.resolve(payload('TWSE:2330', 'history'))
  })
  expect(screen.queryByText('舊股票公告')).toBeNull()
  expect(screen.getByText('新股票公告')).toBeTruthy()
})
