import { useEffect, useState } from 'react'
import { klinesApi } from '@panwatch/api'
import type { IntradayResponse } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

export function TaiwanIntradayChart({ symbol }: { symbol: string }) {
  const [timeframe, setTimeframe] = useState<'1m' | '5m'>('5m')
  const [data, setData] = useState<IntradayResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setData(null)
    setError('')
    klinesApi.intraday(symbol, timeframe, controller.signal)
      .then(result => { if (!controller.signal.aborted) setData(result) })
      .catch(err => { if (!controller.signal.aborted) setError(String(err)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [symbol, timeframe])

  const bars = data?.klines || []
  const prices = bars.flatMap(bar => bar.close != null ? [bar.close] : [])
  const low = Math.min(...prices)
  const high = Math.max(...prices)
  const span = high - low || 1
  let connected = false
  let path = ''
  bars.forEach((bar, index) => {
    if (bar.close == null || bar.status !== 'observed' || !bar.finalized) { connected = false; return }
    const x = 12 + index / Math.max(1, bars.length - 1) * 576
    const y = 150 - (bar.close - low) / span * 130
    path += `${connected ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `
    connected = true
  })
  const clock = (timestamp?: string) => timestamp ? timestamp.slice(11, 16) : ''
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2" aria-label="台股歷史分K">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">台股歷史分K</span>
        <div className="flex gap-1">
          {(['1m', '5m'] as const).map(value => (
            <Button key={value} size="sm" variant={timeframe === value ? 'default' : 'secondary'} onClick={() => setTimeframe(value)}>
              {value === '1m' ? '1 分' : '5 分'}
            </Button>
          ))}
        </div>
      </div>
      {loading ? <p className="text-xs text-muted-foreground">載入中…</p> : error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : prices.length === 0 ? <p className="text-xs text-muted-foreground">尚無歷史分K資料</p> : (
        <>
          <p className="text-[11px] text-muted-foreground">
            {bars[0]?.timestamp.slice(0, 10)} · 台北時間 · {data?.coverage_complete ? '覆蓋完整' : '資料不完整'}
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
