// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TaiwanIntradayChart } from '@panwatch/biz-ui/components/taiwan-intraday-chart'
import { klinesApi } from '@panwatch/api'

vi.mock('@panwatch/api', () => ({ klinesApi: { intraday: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('labels historical prices and keeps missing intervals as chart gaps', async () => {
  const bar = { timestamp: '2026-09-29T09:00:00+08:00', open: 100, high: 102, low: 99, close: 101,
    volume: 100, status: 'observed', finalized: true }
  vi.mocked(klinesApi.intraday).mockResolvedValue({ instrument_id: 'TWSE:2330', timeframe: '5m', price_kind: 'intraday',
    adjustment_mode: 'provider_reported', coverage_complete: false, returned_count: 3, summary: null, live_collection: false,
    klines: [bar, { ...bar, timestamp: '2026-09-29T09:05:00+08:00', status: 'missing', close: null },
      { ...bar, timestamp: '2026-09-29T09:10:00+08:00', close: 102 }],
  })
  const view = render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText(/資料不完整/)).toBeTruthy())
  expect(screen.getByText(/歷史資料/)).toBeTruthy()
  expect(view.container.querySelector('path')?.getAttribute('d')?.match(/M/g)?.length).toBe(2)
  expect(view.container.querySelector('path')?.getAttribute('d')).not.toContain('L')
})

it('shows unavailable data without inventing a zero price', async () => {
  vi.mocked(klinesApi.intraday).mockRejectedValue(new Error('twmd unavailable'))
  const view = render(<TaiwanIntradayChart symbol="TPEX:6488" />)
  await waitFor(() => expect(screen.getByText(/twmd unavailable/)).toBeTruthy())
  expect(view.container.querySelector('svg')).toBeNull()
})
