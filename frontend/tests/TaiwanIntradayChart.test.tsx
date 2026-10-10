// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TaiwanIntradayChart } from '@panwatch/biz-ui/components/taiwan-intraday-chart'
import { klinesApi } from '@panwatch/api'

vi.mock('@panwatch/api', () => ({ klinesApi: { intraday: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

function response({
  instrumentId = 'TWSE:2330',
  timeframe = '5m',
  selectedDate = '2026-10-08',
  mode = 'auto',
  bars = [],
  coverage = [],
  complete = false,
  extra = {},
}: {
  instrumentId?: string
  timeframe?: string
  selectedDate?: string | null
  mode?: 'auto' | 'selected' | 'previous'
  bars?: Array<Record<string, unknown>>
  coverage?: Array<Record<string, unknown>>
  complete?: boolean
  extra?: Record<string, unknown>
} = {}) {
  const rows = bars.length ? bars : [{
    instrument_id: instrumentId,
    trade_date: selectedDate,
    timeframe,
    timestamp: `${selectedDate}T09:00:00+08:00`,
    open: 100,
    close: 101,
    high: 102,
    low: 99,
    volume: 100,
    status: 'observed',
    finalized: true,
  }]
  return {
    instrument_id: instrumentId,
    timeframe,
    start_date: selectedDate || undefined,
    end_date: selectedDate || undefined,
    price_kind: 'intraday',
    adjustment_mode: 'provider_reported',
    coverage_complete: complete,
    display_coverage_complete: complete,
    returned_count: rows.length,
    total_count: rows.length,
    truncated: false,
    availability: complete ? 'available' : 'incomplete',
    summary: null,
    live_collection: false,
    date_selection: { mode, requested_date: null, selected_date: selectedDate, reason: 'fixture' },
    coverage,
    bars: rows,
    klines: rows,
    ...extra,
  }
}

it('labels the actual historical date and keeps missing intervals as chart gaps', async () => {
  const bar = { instrument_id: 'TWSE:2330', trade_date: '2026-10-08', timeframe: '5m',
    timestamp: '2026-10-08T09:00:00+08:00', open: 100, high: 102, low: 99, close: 101,
    volume: 100, status: 'observed', finalized: true }
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({
    bars: [bar, { ...bar, timestamp: '2026-10-08T09:05:00+08:00', status: 'missing', close: null },
      { ...bar, timestamp: '2026-10-08T09:10:00+08:00', close: 102 }],
  }) as never)
  const view = render(<TaiwanIntradayChart symbol="TWSE:2330" />)

  await waitFor(() => expect(screen.getByText(/資料不完整/)).toBeTruthy())
  expect(screen.getByText(/實際資料日：2026-10-08/)).toBeTruthy()
  expect(screen.getByText(/歷史資料/)).toBeTruthy()
  expect(view.container.querySelector('path')?.getAttribute('d')?.match(/M/g)?.length).toBe(2)
  expect(view.container.querySelector('path')?.getAttribute('d')).not.toContain('L')
  expect(vi.mocked(klinesApi.intraday).mock.calls[0]?.[3]).toEqual({ mode: 'auto' })
})

it('keeps an explicitly selected holiday and explains that there is no fallback', async () => {
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({
    selectedDate: '2026-10-09', mode: 'selected', bars: [],
    coverage: [{ trade_date: '2026-10-09', status: 'incomplete', calendar_status: 'unknown' }],
    extra: { availability: 'unavailable', klines: [], date_selection: {
      mode: 'selected', requested_date: '2026-10-09', selected_date: '2026-10-09',
      calendar_status: 'known', calendar: { status: 'known', is_trading_day: false },
      coverage_status: 'incomplete', coverage_calendar_status: 'unknown',
    } },
  }) as never)
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  fireEvent.change(screen.getByLabelText('選擇交易日期'), { target: { value: '2026-10-09' } })

  await waitFor(() => expect(screen.getByText(/休市；保留該日期/)).toBeTruthy())
  expect(vi.mocked(klinesApi.intraday).mock.calls.at(-1)?.[3]).toEqual({ mode: 'selected', date: '2026-10-09' })
  expect((screen.getByLabelText('選擇交易日期') as HTMLInputElement).value).toBe('2026-10-09')
})

it('shows calendar closure alongside retained but incomplete holiday rows', async () => {
  const holidayBar = { instrument_id: 'TWSE:2330', trade_date: '2026-10-09', timeframe: '5m',
    timestamp: '2026-10-09T09:00:00+08:00', open: 100, high: 102, low: 99, close: 101,
    volume: 100, status: 'observed', finalized: true }
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({
    selectedDate: '2026-10-09', mode: 'selected', bars: [holidayBar],
    coverage: [{ trade_date: '2026-10-09', status: 'incomplete', calendar_status: 'unknown' }],
    extra: { date_selection: { mode: 'selected', requested_date: '2026-10-09', selected_date: '2026-10-09',
      calendar: { status: 'known', is_trading_day: false }, coverage_status: 'incomplete',
      coverage_calendar_status: 'unknown' } },
  }) as never)
  const view = render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  fireEvent.change(screen.getByLabelText('選擇交易日期'), { target: { value: '2026-10-09' } })

  await waitFor(() => expect(screen.getByText(/日曆顯示休市；所選日期仍保留原始分K/)).toBeTruthy())
  expect(screen.getByText(/資料不完整/)).toBeTruthy()
  expect(view.container.querySelector('svg')).toBeTruthy()
})

it('pins the automatically resolved date when changing timeframe', async () => {
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({ selectedDate: '2026-10-08' }) as never)
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText(/實際資料日：2026-10-08/)).toBeTruthy())

  fireEvent.click(screen.getByRole('button', { name: '1 分' }))
  await waitFor(() => expect(vi.mocked(klinesApi.intraday).mock.calls.length).toBe(2))
  expect(vi.mocked(klinesApi.intraday).mock.calls[0]?.[3]).toEqual({ mode: 'auto' })
  expect(vi.mocked(klinesApi.intraday).mock.calls[1]?.[1]).toBe('1m')
  expect(vi.mocked(klinesApi.intraday).mock.calls[1]?.[3]).toEqual({ mode: 'selected', date: '2026-10-08' })
})

it('uses the resolved previous date for later timeframe reads', async () => {
  vi.mocked(klinesApi.intraday).mockImplementation(async (_symbol, timeframe, _signal, options) => {
    if (options?.mode === 'previous') {
      return response({ timeframe, selectedDate: '2026-10-08', mode: 'previous', extra: {
        date_selection: { mode: 'previous', requested_date: '2026-10-12', selected_date: '2026-10-08' },
      } }) as never
    }
    return response({ timeframe, selectedDate: '2026-10-12' }) as never
  })
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText(/實際資料日：2026-10-12/)).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: '上一交易日' }))
  await waitFor(() => expect(screen.getByText(/實際資料日：2026-10-08/)).toBeTruthy())

  fireEvent.click(screen.getByRole('button', { name: '1 分' }))
  await waitFor(() => expect(vi.mocked(klinesApi.intraday).mock.calls.length).toBe(3))
  expect(vi.mocked(klinesApi.intraday).mock.calls[1]?.[3]).toEqual({ mode: 'previous', date: '2026-10-12' })
  expect(vi.mocked(klinesApi.intraday).mock.calls[2]?.[3]).toEqual({ mode: 'selected', date: '2026-10-08' })
})

it('aborts and ignores a late response from the previous symbol', async () => {
  let resolveOld!: (value: never) => void
  let resolveNew!: (value: never) => void
  vi.mocked(klinesApi.intraday)
    .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve }))
  const view = render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  view.rerender(<TaiwanIntradayChart symbol="TPEX:6488" />)
  expect(vi.mocked(klinesApi.intraday).mock.calls[0]?.[2]?.aborted).toBe(true)

  await act(async () => { resolveNew(response({ instrumentId: 'TPEX:6488', selectedDate: '2026-10-08' }) as never) })
  await act(async () => { resolveOld(response({ instrumentId: 'TWSE:2330', selectedDate: '2026-10-07' }) as never) })
  expect(screen.getByText(/實際資料日：2026-10-08/)).toBeTruthy()
  expect(screen.queryByText(/2026-10-07/)).toBeNull()
})

it('rejects conflicting response and row date identities', async () => {
  const bar = { instrument_id: 'TWSE:2330', trade_date: '2026-10-07', timeframe: '5m',
    timestamp: '2026-10-07T09:00:00+08:00', close: 101, status: 'observed', finalized: true }
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({ selectedDate: '2026-10-08', bars: [bar] }) as never)
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText(/資料列混入其他日期/)).toBeTruthy())
})

it('shows no-trade and unavailable states without inventing a zero price', async () => {
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({
    bars: [{ instrument_id: 'TPEX:6488', trade_date: '2026-10-08', timeframe: '5m',
      timestamp: '2026-10-08T09:00:00+08:00', close: null, status: 'no_trade', finalized: true }],
    extra: { instrument_id: 'TPEX:6488', availability: 'closed', klines: [{
      instrument_id: 'TPEX:6488', trade_date: '2026-10-08', timeframe: '5m',
      timestamp: '2026-10-08T09:00:00+08:00', close: null, status: 'no_trade', finalized: true,
    }] },
  }) as never)
  const view = render(<TaiwanIntradayChart symbol="TPEX:6488" />)
  await waitFor(() => expect(screen.getByText(/當日無成交/)).toBeTruthy())
  expect(view.container.querySelector('svg')).toBeNull()
})

it('explains the fixed holiday while showing all 54 retained five-minute bars', async () => {
  const bars = Array.from({ length: 54 }, (_, index) => ({
    instrument_id: 'TWSE:2330', trade_date: '2026-10-08', timeframe: '5m',
    timestamp: `2026-10-08T${String(9 + Math.floor(index * 5 / 60)).padStart(2, '0')}:${String(index * 5 % 60).padStart(2, '0')}:00+08:00`,
    close: 100 + index, status: 'observed', finalized: true,
  }))
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({ bars, complete: true, extra: {
    date_selection: { mode: 'auto', selected_date: '2026-10-08', as_of_date: '2026-10-09',
      as_of_calendar: { status: 'known', is_trading_day: false } },
  } }) as never)
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText('10/09 休市，顯示 10/08 歷史 5 分K')).toBeTruthy())
  expect(screen.getByText(/覆蓋完整/)).toBeTruthy()
})

it.each([
  ['calendar_unknown', undefined, /交易日曆未知；無法確認上一交易日/],
  ['manual_selection', 'unsupported', /來源不支援所選歷史分K/],
])('keeps the %s reason distinct from missing retention', async (reason, availability, label) => {
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({ selectedDate: null, extra: {
    bars: [], klines: [], availability: availability || 'unavailable',
    date_selection: { mode: 'previous', selected_date: null, reason, calendar: { status: 'unknown' } },
  } }) as never)
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText(label)).toBeTruthy())
  expect(screen.queryByText(/沒有分K留存/)).toBeNull()
})

it('ignores late date and timeframe responses after a newer selection', async () => {
  const pending: Array<(value: never) => void> = []
  vi.mocked(klinesApi.intraday).mockImplementation(() => new Promise(resolve => { pending.push(resolve) }))
  render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  fireEvent.change(screen.getByLabelText('選擇交易日期'), { target: { value: '2026-10-07' } })
  fireEvent.click(screen.getByRole('button', { name: '1 分' }))
  await act(async () => { pending[2](response({ selectedDate: '2026-10-07', timeframe: '1m', mode: 'selected' }) as never) })
  await act(async () => {
    pending[1](response({ selectedDate: '2026-10-07', mode: 'selected' }) as never)
    pending[0](response({ selectedDate: '2026-10-08' }) as never)
  })
  expect(screen.getByText(/實際資料日：2026-10-07/)).toBeTruthy()
  expect(screen.queryByText(/2026-10-08/)).toBeNull()
  expect(vi.mocked(klinesApi.intraday).mock.calls.slice(0, 2).every(call => call[2]?.aborted)).toBe(true)
})

it('keeps numeric missing rows out of the price range and plotted path', async () => {
  vi.mocked(klinesApi.intraday).mockResolvedValue(response({ bars: [
    { timestamp: '2026-10-08T09:00:00+08:00', close: 999, status: 'missing', finalized: false },
  ] }) as never)
  const view = render(<TaiwanIntradayChart symbol="TWSE:2330" />)
  await waitFor(() => expect(screen.getByText(/分K資料不完整/)).toBeTruthy())
  expect(view.container.querySelector('svg')).toBeNull()
})
