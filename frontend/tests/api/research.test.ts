// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { researchApi } from '@panwatch/api/research'

beforeEach(() => vi.stubGlobal('localStorage', { getItem: () => null }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('selective research requests', () => {
  it('sends repeated block selectors and preserves the old full-read request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({
      code: 0, data: {},
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await researchApi.taiwan('TWSE:2330', { blocks: ['valuation', 'company_profile'] })
    const query = new URL(String(fetchMock.mock.calls[0][0]), 'http://test').searchParams
    expect(query.get('instrument_id')).toBe('TWSE:2330')
    expect(query.getAll('blocks')).toEqual(['valuation', 'company_profile'])
    await researchApi.taiwan('TPEX:5347')
    expect(new URL(String(fetchMock.mock.calls[1][0]), 'http://test').searchParams.has('blocks')).toBe(false)
  })

  it('uses the bounded financial-period endpoint with the caller abort signal', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      code: 0, data: { index_status: 'unknown' },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const controller = new AbortController()
    await researchApi.financialPeriods('TWSE:2330', {
      report_scope: 'consolidated', statement: 'cash_flows', limit: 40, cursor: 'opaque-cursor',
    }, { signal: controller.signal })

    const [url, options] = fetchMock.mock.calls[0]
    const query = new URL(String(url), 'http://test').searchParams
    expect(String(url)).toContain('/api/research/taiwan/financial-periods?')
    expect(query.get('instrument_id')).toBe('TWSE:2330')
    expect(query.get('report_scope')).toBe('consolidated')
    expect(query.get('statement')).toBe('cash_flows')
    expect(query.get('limit')).toBe('40')
    expect(query.get('cursor')).toBe('opaque-cursor')
    expect(options?.signal).toBeInstanceOf(AbortSignal)
    expect(options?.signal?.aborted).toBe(false)
  })

  it.each(['deadline', 'caller'] as const)('still aborts a stalled request on %s with an external signal', async (trigger) => {
    vi.useFakeTimers()
    const caller = new AbortController()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    const request = researchApi.taiwan('TWSE:2330', { blocks: ['financial_statements'] }, { signal: caller.signal })
    const settled = expect(request).rejects.toThrow('請求超時')
    if (trigger === 'deadline') await vi.advanceTimersByTimeAsync(30_000)
    else caller.abort()
    await settled
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
})
