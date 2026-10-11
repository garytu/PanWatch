// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { klinesApi } from '@panwatch/api/klines'

describe('klinesApi', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', { getItem: () => null })
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('loads multiple summaries with one batch request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        code: 0,
        success: true,
        data: [{ symbol: '600519', market: 'CN', summary: { trend: '多頭排列' } }],
        message: '',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )

    await klinesApi.summaryBatch([
      { symbol: '600519', market: 'CN' },
      { symbol: '00700', market: 'HK' },
    ])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/klines/summary/batch')
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      items: [
        { symbol: '600519', market: 'CN' },
        { symbol: '00700', market: 'HK' },
      ],
    })
  })

  it('keeps canonical Taiwan identity and minute timeframe in the intraday request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ code: 0, success: true, data: { klines: [], live_collection: false }, message: '' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    await klinesApi.intraday('TPEX:00679B', '5m')
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/klines/TPEX%3A00679B/intraday?market=TW&timeframe=5m&limit=54')
  })

  it('sends an explicitly selected session as an exact paired date range', async () => {
    let releaseFetch!: (response: Response) => void
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockReturnValue(
      new Promise<Response>(resolve => { releaseFetch = resolve }),
    )
    const controller = new AbortController()
    const request = klinesApi.intraday('TWSE:2330', '1m', controller.signal, { mode: 'selected', date: '2026-10-08' })
    await Promise.resolve()
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/klines/TWSE%3A2330/intraday?market=TW&timeframe=1m&limit=270&date_mode=selected&start_date=2026-10-08&end_date=2026-10-08',
    )
    const forwardedSignal = fetchMock.mock.calls[0]?.[1]?.signal
    expect(forwardedSignal).toBeTruthy()
    controller.abort()
    expect(forwardedSignal?.aborted).toBe(true)
    releaseFetch(new Response(JSON.stringify({ code: 0, success: true, data: { klines: [] }, message: '' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await request
  })

  it('uses the selected date as the previous trading day anchor', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ code: 0, success: true, data: { klines: [] }, message: '' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    await klinesApi.intraday('TWSE:2330', '5m', undefined, { mode: 'previous', date: '2026-10-12' })
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/klines/TWSE%3A2330/intraday?market=TW&timeframe=5m&limit=54&date_mode=previous&trade_date=2026-10-12',
    )
  })
})
