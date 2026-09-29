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
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/klines/TPEX%3A00679B/intraday?market=TW&timeframe=5m&limit=270')
  })
})
