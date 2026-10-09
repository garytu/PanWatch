import { useEffect, useRef, useState } from 'react'
import { klinesApi } from '@panwatch/api'
import type { IntradayDateOptions, IntradayResponse } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

type SelectionMode = IntradayDateOptions['mode']

function taipeiToday(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date())
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

function taipeiDate(value: string): string {
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) throw new Error('分K回應時間缺少時區')
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) throw new Error('分K回應含有無效時間')
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(parsed)
  const fields = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${fields.year}-${fields.month}-${fields.day}`
}

function isCanonicalTaiwanSymbol(symbol: string): boolean {
  return /^(TWSE|TPEX):[0-9A-Z]{4,7}$/i.test(symbol)
}

function responseDate(data: IntradayResponse | null): string | undefined {
  if (!data) return undefined
  if (data.date_selection?.selected_date) return data.date_selection.selected_date
  if (data.start_date && data.start_date === data.end_date) return data.start_date
  return data.klines?.[0]?.trade_date
    || (data.klines?.[0]?.timestamp ? taipeiDate(data.klines[0].timestamp) : undefined)
}

function validateResponse(data: IntradayResponse, symbol: string, timeframe: '1m' | '5m',
                          options: IntradayDateOptions): string | undefined {
  if (data.instrument_id) {
    if (isCanonicalTaiwanSymbol(symbol) && data.instrument_id !== symbol) {
      throw new Error('分K回應的標的與目前選擇不一致')
    }
    if (!isCanonicalTaiwanSymbol(symbol) && data.instrument_id.split(':')[1] !== symbol.replace(/\.(TW|TWO)$/i, '')) {
      throw new Error('分K回應的標的與目前選擇不一致')
    }
  }
  if (data.timeframe && data.timeframe !== timeframe) throw new Error('分K回應的週期與目前選擇不一致')

  const selectedDate = responseDate(data)
  if (options.mode === 'selected' && selectedDate !== options.date) {
    throw new Error('分K回應的日期與目前選擇不一致')
  }
  if (options.mode === 'previous' && options.date && selectedDate && selectedDate >= options.date) {
    throw new Error('上一交易日回應日期不一致')
  }
  const envelopeDates = [data.date_selection?.selected_date, data.start_date, data.end_date]
    .filter((value): value is string => Boolean(value))
  if (selectedDate && envelopeDates.some(value => value !== selectedDate)) {
    throw new Error('分K回應的日期範圍與實際資料日不一致')
  }

  const rows = [...(data.bars || []), ...(data.klines || [])]
  const rowDates = new Set<string>()
  for (const row of rows) {
    if (row.instrument_id && ((data.instrument_id && row.instrument_id !== data.instrument_id)
      || (isCanonicalTaiwanSymbol(symbol) && row.instrument_id !== symbol))) {
      throw new Error('分K資料列的標的與回應不一致')
    }
    if (row.timeframe && row.timeframe !== timeframe) throw new Error('分K資料列的週期與目前選擇不一致')
    const dates = [row.trade_date, row.timestamp, row.interval_start, row.interval_end, row.provider_timestamp]
      .filter((value): value is string => Boolean(value))
      .map(value => value === row.trade_date ? value : taipeiDate(value))
    if (dates.some(value => dates[0] !== value)) throw new Error('分K資料列的交易日期與時間戳不一致')
    if (dates[0]) rowDates.add(dates[0])
  }
  if (rowDates.size > 1 || (selectedDate && [...rowDates].some(value => value !== selectedDate))) {
    throw new Error('分K資料列混入其他日期')
  }
  return selectedDate
}

function dateLabel(value?: string): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return ''
  return `${value.slice(5, 7)}/${value.slice(8, 10)}`
}

function timeLabel(value?: string): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(parsed)
}

function noPriceMessage(data: IntradayResponse | null, selectedDate?: string): string {
  if (!data) return '尚無歷史分K資料'
  const selection = data.date_selection
  const coverage = data.coverage?.find(row => row.trade_date === selectedDate)
  const calendarStatus = selection?.calendar?.status
  const calendarOpen = selection?.calendar?.is_trading_day
  const coverageCalendar = selection?.coverage_calendar_status
    || (typeof coverage?.calendar_status === 'string' ? coverage.calendar_status : undefined)
  const coverageStatus = selection?.coverage_status
    || (typeof coverage?.status === 'string' ? coverage.status : undefined)
  const rows = data.klines || data.bars || []
  if (data.availability === 'unsupported' || coverageStatus === 'unsupported') return '來源不支援所選歷史分K'
  if (selection?.reason === 'calendar_unknown') return '交易日曆未知；無法確認上一交易日，請選擇明確日期'
  if ((calendarStatus === 'known' && calendarOpen === false) || coverageCalendar === 'official_closed') {
    return '所選日期休市；保留該日期，沒有切換到其他日期'
  }
  if (selection?.mode === 'auto' && !selection.selected_date) {
    if (selection.reason === 'schema_not_ready') return '分K留存查詢尚未就緒'
    if (selection.reason === 'request_budget_exhausted') return '已達有限查找次數，尚未找到可用分K'
    if (selection.reason === 'no_priced_complete_candidate') return '查找範圍內沒有可畫價格的已完成分K'
    if (selection.reason === 'no_known_completed_trading_date') return '查找範圍內沒有日曆已確認的完成交易日'
  }
  if (rows.length > 0 && rows.every(row => row.status === 'no_trade')) return '當日無成交，價格維持空值'
  if (coverageStatus === 'incomplete' || coverageStatus === 'failed'
      || (rows.length > 0 && rows.some(row => ['missing', 'pending', 'incomplete'].includes(row.status)))) {
    return '分K資料不完整；缺失時段保留空值'
  }
  if (calendarStatus === 'unknown' && coverageCalendar === 'unknown') return '交易日曆未知；保留所選日期，未推測是否開市'
  if (coverageStatus === 'empty_unverified') return '來源無法確認所選日期是否有分K資料'
  if (coverageStatus === 'missing' || data.availability === 'unavailable') return '所選日期沒有分K留存'
  return '所選日期沒有可畫的歷史價格'
}

export function TaiwanIntradayChart({ symbol }: { symbol: string }) {
  const [timeframe, setTimeframe] = useState<'1m' | '5m'>('5m')
  const [mode, setMode] = useState<SelectionMode>('auto')
  const [date, setDate] = useState('')
  const [pinnedDate, setPinnedDate] = useState('')
  const [data, setData] = useState<IntradayResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectionRevision, setSelectionRevision] = useState(0)
  const pinnedDateRef = useRef('')
  const pinnedSymbolRef = useRef('')
  const requestSequenceRef = useRef(0)
  const shownDate = mode === 'selected' ? date : (pinnedDate || (mode === 'previous' ? date : taipeiToday()))

  useEffect(() => {
    const sequence = ++requestSequenceRef.current
    const controller = new AbortController()
    let active = true

    if (mode === 'auto' && !isCanonicalTaiwanSymbol(symbol)) {
      setData(null)
      setError('最近可用日查找需要 TWSE 或 TPEX 標的識別')
      setLoading(false)
      return () => { active = false; controller.abort() }
    }

    let options: IntradayDateOptions
    if (mode === 'auto' && pinnedSymbolRef.current === symbol && pinnedDateRef.current) {
      options = { mode: 'selected', date: pinnedDateRef.current }
    } else if (mode === 'previous' && pinnedDateRef.current) {
      options = { mode: 'selected', date: pinnedDateRef.current }
    } else if (mode === 'previous') {
      options = { mode: 'previous', date: date || pinnedDateRef.current || taipeiToday() }
    } else if (mode === 'selected') {
      options = { mode: 'selected', date: date || taipeiToday() }
    } else {
      options = { mode: 'auto' }
    }

    setLoading(true)
    setData(null)
    setError('')
    klinesApi.intraday(symbol, timeframe, controller.signal, options)
      .then(result => {
        if (!active || controller.signal.aborted || requestSequenceRef.current !== sequence) return
        const resolved = validateResponse(result, symbol, timeframe, options)
        if (resolved) {
          pinnedDateRef.current = resolved
          pinnedSymbolRef.current = symbol
          setPinnedDate(resolved)
        } else {
          pinnedDateRef.current = ''
          pinnedSymbolRef.current = ''
        }
        setData(result)
      })
      .catch(err => {
        if (active && !controller.signal.aborted && requestSequenceRef.current === sequence) setError(String(err))
      })
      .finally(() => {
        if (active && !controller.signal.aborted && requestSequenceRef.current === sequence) setLoading(false)
      })
    return () => { active = false; controller.abort() }
  }, [symbol, timeframe, mode, date, selectionRevision])

  const chooseAutomatic = () => {
    pinnedDateRef.current = ''
    pinnedSymbolRef.current = ''
    setPinnedDate('')
    setDate('')
    setMode('auto')
    setSelectionRevision(value => value + 1)
  }

  const choosePrevious = () => {
    const baseDate = responseDate(data) || pinnedDate || date || taipeiToday()
    pinnedDateRef.current = ''
    pinnedSymbolRef.current = ''
    setPinnedDate('')
    setDate(baseDate)
    setMode('previous')
    setSelectionRevision(value => value + 1)
  }

  const chooseDate = (value: string) => {
    pinnedDateRef.current = value
    pinnedSymbolRef.current = symbol
    setPinnedDate(value)
    setDate(value)
    setMode('selected')
    setSelectionRevision(revision => revision + 1)
  }

  const bars = data?.klines || []
  const drawable = (bar: IntradayResponse['klines'][number]) => bar.status === 'observed' && bar.finalized === true
    && typeof bar.close === 'number' && Number.isFinite(bar.close) && bar.close > 0
  const prices = bars.flatMap(bar => drawable(bar) ? [bar.close as number] : [])
  const low = Math.min(...prices)
  const high = Math.max(...prices)
  const span = high - low || 1
  let connected = false
  let path = ''
  bars.forEach((bar, index) => {
    if (!drawable(bar) || bar.close == null) {
      connected = false
      return
    }
    const x = 12 + index / Math.max(1, bars.length - 1) * 576
    const y = 150 - (bar.close - low) / span * 130
    path += `${connected ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `
    connected = true
  })
  const clock = (timestamp?: string) => timeLabel(timestamp)
  const selectedDate = responseDate(data) || pinnedDate || (mode === 'selected' ? date : undefined)
  const expectedRows = timeframe === '1m' ? 270 : 54
  const displayComplete = data?.display_coverage_complete ?? (
    data?.coverage_complete === true && data.truncated === false
    && data.returned_count === expectedRows && data.total_count === expectedRows
    && bars.length === expectedRows
  )
  const selection = data?.date_selection
  const automaticHolidayLabel = selection?.mode === 'auto' && selection.as_of_date
    && selection.selected_date && selection.as_of_date !== selection.selected_date
    && selection.as_of_calendar?.status === 'known' && selection.as_of_calendar?.is_trading_day === false
    ? `${dateLabel(selection.as_of_date)} 休市，顯示 ${dateLabel(selection.selected_date)} 歷史 ${timeframe === '1m' ? '1' : '5'} 分K`
    : null
  const manualHolidayLabel = selection?.mode !== 'auto' && selection?.selected_date
    && selection.calendar?.status === 'known' && selection.calendar.is_trading_day === false
    ? `${dateLabel(selection.selected_date)} 日曆顯示休市；所選日期仍保留原始分K與覆蓋狀態`
    : null

  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2" aria-label="台股歷史分K">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium">台股歷史分K</span>
        <div className="flex flex-wrap items-center gap-1">
          <Button size="sm" variant={mode === 'auto' ? 'default' : 'secondary'} onClick={chooseAutomatic}>
            最近可用日
          </Button>
          <Button size="sm" variant="secondary" onClick={choosePrevious}>
            上一交易日
          </Button>
          <label className="sr-only" htmlFor="taiwan-intraday-date">選擇交易日期</label>
          <input
            id="taiwan-intraday-date"
            aria-label="選擇交易日期"
            type="date"
            max={taipeiToday()}
            value={shownDate}
            onChange={event => chooseDate(event.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          />
          {(['1m', '5m'] as const).map(value => (
            <Button key={value} size="sm" variant={timeframe === value ? 'default' : 'secondary'} onClick={() => setTimeframe(value)}>
              {value === '1m' ? '1 分' : '5 分'}
            </Button>
          ))}
        </div>
      </div>
      {automaticHolidayLabel && <p className="text-[11px] text-muted-foreground">{automaticHolidayLabel}</p>}
      {manualHolidayLabel && <p className="text-[11px] text-muted-foreground">{manualHolidayLabel}</p>}
      {selectedDate && <p className="text-[11px] text-muted-foreground">實際資料日：{selectedDate} · 台北時間 · {data?.date_selection?.coverage_status || '覆蓋狀態未知'}</p>}
      {loading ? <p className="text-xs text-muted-foreground">載入中…</p> : error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : prices.length === 0 ? <p className="text-xs text-muted-foreground">{noPriceMessage(data, selectedDate)}</p> : (
        <>
          <p className="text-[11px] text-muted-foreground">
            {automaticHolidayLabel ? `${automaticHolidayLabel} · ` : manualHolidayLabel ? `${manualHolidayLabel} · ` : ''}
            {selectedDate || bars[0]?.timestamp.slice(0, 10)} · 台北時間 · {displayComplete ? '覆蓋完整' : '資料不完整'}
            {' · 歷史資料 · 股 / TWD'}
          </p>
          <svg viewBox="0 0 600 175" className="w-full" role="img" aria-label={`${symbol} 歷史收盤價格走勢`}>
            <title>歷史分K收盤價；缺失與未成交區間保留斷點</title>
            <path d={path} fill="none" stroke="currentColor" strokeWidth="2" className="text-primary" />
            <text x="12" y="12" fontSize="10" fill="currentColor">{high.toFixed(2)}</text>
            <text x="12" y="165" fontSize="10" fill="currentColor">{clock(bars[0]?.timestamp)}</text>
            <text x="585" y="165" textAnchor="end" fontSize="10" fill="currentColor">{clock(bars[bars.length - 1]?.timestamp)}</text>
          </svg>
          {data?.summary && <p className="text-[11px] text-muted-foreground">
            分K技術指標：RSI6 {typeof data.summary.rsi6 === 'number' ? data.summary.rsi6.toFixed(1) : '—'}
            {' · MA20 '}{typeof data.summary.ma20 === 'number' ? data.summary.ma20.toFixed(2) : '—'}
          </p>}
        </>
      )}
    </section>
  )
}
