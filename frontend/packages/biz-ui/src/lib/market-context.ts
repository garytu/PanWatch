export interface QuoteMarketContextInput {
  current_price?: number | null
  change_pct?: number | null
  timestamp?: string | null
  trade_date?: string | null
  price_kind?: string | null
  freshness?: { status?: string | null } | null
  availability?: string | null
  usable_for_trading?: boolean | null
}

export interface MarketCalendarContextInput {
  status?: string | null
  date?: string | null
  is_trading_day?: boolean | null
}

export interface MarketStatusContextInput {
  code?: string | null
  status_text?: string | null
  timezone?: string | null
  calendar?: MarketCalendarContextInput | null
}

export interface QuoteMarketContext {
  quoteLabel: string
  marketLabel: string
  aiContext: string
}

const MARKET_TIMEZONES: Record<string, string> = {
  TW: 'Asia/Taipei',
  CN: 'Asia/Shanghai',
  HK: 'Asia/Hong_Kong',
  US: 'America/New_York',
}

function nonEmpty(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const result = value.trim()
  return result || null
}

function observationTime(timestamp: string | null | undefined, timezone: string): string {
  if (!timestamp) return '來源觀察時間未知'
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(timestamp.trim())) {
    return `來源觀察時間 ${timestamp.trim()}（時區未知）`
  }
  const parsed = new Date(timestamp)
  if (!Number.isFinite(parsed.getTime())) return '來源觀察時間未知'

  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(parsed)
  const part = (type: string) => parts.find(item => item.type === type)?.value || ''
  return `來源觀察時間 ${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`
}

function priceKindLabel(quote: QuoteMarketContextInput): string {
  const kind = nonEmpty(quote.price_kind)
  if (kind === 'eod') return 'eod（收盤行情）'
  if (kind === 'live') {
    const freshness = nonEmpty(quote.freshness?.status)
    if (freshness === 'stale' || freshness === 'expired') return 'live（過期盤中報價）'
    if (freshness === 'fresh') return 'live（新鮮盤中報價）'
    if (freshness === 'closed') return 'live（已收盤盤中報價）'
    return 'live（盤中報價時效未知）'
  }
  return kind ? `${kind}（價格種類未映射）` : '價格種類未知'
}

function tradabilityLabel(value: boolean | null | undefined): string {
  if (value === true) return 'API 標示可交易'
  if (value === false) return 'API 標示不可交易'
  return '可交易狀態未知'
}

function marketCalendarLabel(market: string, status: MarketStatusContextInput | null | undefined): string {
  const timezone = nonEmpty(status?.timezone) || MARKET_TIMEZONES[market] || '時區未知'
  const calendar = status?.calendar
  const date = nonEmpty(calendar?.date)
  const calendarKnown = calendar?.status === 'known' && typeof calendar.is_trading_day === 'boolean'
  const dayState = calendarKnown ? (calendar.is_trading_day ? '交易日' : '休市') : '日曆未知'
  const statusText = nonEmpty(status?.status_text)
  return [date, dayState, statusText, timezone === '時區未知' ? timezone : `時區 ${timezone}`]
    .filter(Boolean)
    .join(' · ')
}

/**
 * Format source quote dates and market calendar once for both the detail card
 * and the "ask AI" context. Missing source timestamps stay unknown.
 */
export function buildQuoteMarketContext(
  quote: QuoteMarketContextInput,
  market: string,
  marketStatus?: MarketStatusContextInput | null,
): QuoteMarketContext {
  const normalizedMarket = market.trim().toUpperCase()
  const timezone = nonEmpty(marketStatus?.timezone) || MARKET_TIMEZONES[normalizedMarket] || 'UTC'
  const tradeDate = nonEmpty(quote.trade_date) || '行情日期未知'
  const freshness = nonEmpty(quote.freshness?.status) || '未知'
  const availability = nonEmpty(quote.availability) || '未知'
  const quoteLabel = [
    `行情日期 ${tradeDate}`,
    `價格種類 ${priceKindLabel(quote)}`,
    `時效 ${freshness}`,
    `資料狀態 ${availability}`,
    tradabilityLabel(quote.usable_for_trading),
    observationTime(quote.timestamp, timezone),
  ].join(' · ')
  const marketLabel = marketCalendarLabel(normalizedMarket, marketStatus)
  const price = quote.current_price == null ? '未知' : String(quote.current_price)
  const change = quote.change_pct == null ? '未知' : `${quote.change_pct}%`
  return {
    quoteLabel,
    marketLabel,
    aiContext: `行情背景：${quoteLabel}；價格 ${price}；漲跌幅 ${change}\n市場背景：${marketLabel}`,
  }
}
