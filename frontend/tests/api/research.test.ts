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
