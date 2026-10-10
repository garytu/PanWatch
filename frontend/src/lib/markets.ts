// 暫時僅啟用台股；恢復其他市場時同步更新後端 ENABLED_MARKETS。
export const ENABLED_MARKETS = [
  // 'CN',
  // 'HK',
  // 'US',
  'TW',
] as const
export const DEFAULT_MARKET = ENABLED_MARKETS[0]
export const MARKET_LABELS: Record<string, string> = { CN: 'A股', HK: '港股', US: '美股', TW: '台股' }
export const isMarketEnabled = (market: string) => ENABLED_MARKETS.some(code => code === market)
export const MARKET_OPTIONS = ENABLED_MARKETS.map(value => ({ value, label: MARKET_LABELS[value] }))
