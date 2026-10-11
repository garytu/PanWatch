import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MarketIndexCards from '@/components/MarketIndexCards'
import type { DashboardMarketIndex } from '@panwatch/api'

const indices: DashboardMarketIndex[] = [
  { symbol: 'TAIEX', name: '加權指數', market: 'TW', current_price: 22_000, change_pct: 0.5, change_amount: 100, prev_close: 21_900, price_kind: 'eod', trade_date: '2026-10-07', change_start_date: '2026-10-06', unit: 'index_points', source_partial: true, spark: [21_900, 22_000] },
  { symbol: 'TPEX', name: '櫃買指數', market: 'TW', current_price: 260, change_pct: -0.1, change_amount: -0.3, prev_close: 260.3, price_kind: 'eod', trade_date: '2026-10-07', change_start_date: '2026-10-06', unit: 'index_points', spark: [260.3, 260] },
  { symbol: '000001', name: '上證指數', market: 'CN', current_price: 3_200, change_pct: 0, change_amount: 0, prev_close: 3_200 },
  { symbol: '399001', name: '深證成指', market: 'CN', current_price: 10_000, change_pct: 0, change_amount: 0, prev_close: 10_000 },
  { symbol: '399006', name: '創業板指', market: 'CN', current_price: 2_000, change_pct: 0, change_amount: 0, prev_close: 2_000 },
  { symbol: 'HSI', name: '恒生指數', market: 'HK', current_price: 18_000, change_pct: 0, change_amount: 0, prev_close: 18_000 },
  { symbol: 'IXIC', name: '納斯達克', market: 'US', current_price: 18_000, change_pct: 0, change_amount: 0, prev_close: 18_000 },
  { symbol: 'DJI', name: '道瓊斯', market: 'US', current_price: 42_000, change_pct: 0, change_amount: 0, prev_close: 42_000 },
]

describe('MarketIndexCards', () => {
  it('shows Taiwan official EOD benchmarks alongside every enabled market index', () => {
    render(<MarketIndexCards indices={indices} />)

    for (const index of indices) expect(screen.getByText(index.name)).toBeTruthy()
    expect(screen.getAllByText('日線收盤 · 2026-10-07')).toHaveLength(2)
    expect(screen.getByText('22000.00 點')).toBeTruthy()
    expect(screen.getByText('260.00 點')).toBeTruthy()
    expect(screen.getAllByText('較 2026-10-06')).toHaveLength(2)
    expect(screen.getByText('來源回傳部分資料')).toBeTruthy()
  })
})
