import { describe, expect, it } from 'vitest'
import { buildQuoteMarketContext } from '@panwatch/biz-ui/lib/market-context'

describe('buildQuoteMarketContext', () => {
  const taiwanHoliday = {
    code: 'TW',
    status_text: '休市（交易日曆）',
    timezone: 'Asia/Taipei',
    calendar: { status: 'known', date: '2026-10-09', is_trading_day: false },
  }

  it('labels stale live data with its quote date, observed time, and false tradability', () => {
    const context = buildQuoteMarketContext({
      current_price: 1450,
      change_pct: 0.25,
      trade_date: '2026-10-08',
      price_kind: 'live',
      timestamp: '2026-10-08T05:30:00Z',
      freshness: { status: 'stale' },
      availability: 'available',
      usable_for_trading: false,
    }, 'TW', taiwanHoliday)

    expect(context.quoteLabel).toContain('行情日期 2026-10-08')
    expect(context.quoteLabel).toContain('live（過期盤中報價）')
    expect(context.quoteLabel).toContain('來源觀察時間 2026-10-08 13:30')
    expect(context.quoteLabel).toContain('API 標示不可交易')
    expect(context.marketLabel).toContain('2026-10-09 · 休市')
    expect(context.marketLabel).toContain('Asia/Taipei')
    expect(context.aiContext).toContain(context.quoteLabel)
    expect(context.aiContext).toContain(context.marketLabel)
  })

  it('keeps EOD and a null observation timestamp separate from report time', () => {
    const context = buildQuoteMarketContext({
      current_price: 50,
      trade_date: '2026-10-08',
      price_kind: 'eod',
      timestamp: null,
      freshness: { status: 'closed' },
      usable_for_trading: false,
    }, 'TW', taiwanHoliday)

    expect(context.quoteLabel).toContain('行情日期 2026-10-08')
    expect(context.quoteLabel).toContain('eod（收盤行情）')
    expect(context.quoteLabel).toContain('來源觀察時間未知')
    expect(context.quoteLabel).toContain('API 標示不可交易')
    expect(context.quoteLabel).not.toContain('2026-10-09 13:30')
  })

  it('keeps missing quote and calendar fields explicitly unknown', () => {
    const context = buildQuoteMarketContext({}, 'TW', null)

    expect(context.quoteLabel).toContain('行情日期未知')
    expect(context.quoteLabel).toContain('價格種類未知')
    expect(context.quoteLabel).toContain('可交易狀態未知')
    expect(context.marketLabel).toContain('日曆未知')
    expect(context.marketLabel).toContain('Asia/Taipei')
  })

  it('does not assign a timezone to a naive source observation timestamp', () => {
    const context = buildQuoteMarketContext({
      price_kind: 'live',
      timestamp: '2026-10-08T13:30:00',
    }, 'TW', taiwanHoliday)

    expect(context.quoteLabel).toContain('來源觀察時間 2026-10-08T13:30:00（時區未知）')
  })

  it.each([
    ['fresh', true, '新鮮盤中報價', '交易中', true],
    ['closed', false, '已收盤盤中報價', '已收盤', true],
    ['stale', false, '過期盤中報價', '休市（週末）', false],
  ])('preserves %s live provenance and the market state', (freshness, usable, label, state, tradingDay) => {
    const context = buildQuoteMarketContext({
      price_kind: 'live', trade_date: '2026-10-08',
      freshness: { status: freshness }, usable_for_trading: usable,
    }, 'TW', {
      code: 'TW', status_text: state, timezone: 'Asia/Taipei',
      calendar: { status: 'known', date: '2026-10-10', is_trading_day: tradingDay },
    })
    expect(context.quoteLabel).toContain(label)
    expect(context.quoteLabel).toContain(usable ? 'API 標示可交易' : 'API 標示不可交易')
    expect(context.marketLabel).toContain(state)
    expect(context.aiContext).not.toContain('即時行情')
  })
})
