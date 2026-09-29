import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'

interface FeedStatus {
  quotes: { status?: string; observed_live_data?: boolean; collection_health?: { status?: string } }
  intraday: { live_collection?: boolean }
  calendar: { status?: string }
  unsubscribed_instrument_ids: string[]
  unresolved_symbols: string[]
  active_instrument_counts: { TWSE: number; TPEX: number }
}

export function TaiwanFeedStatus() {
  const [data, setData] = useState<FeedStatus | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    const load = () => fetchAPI<FeedStatus>('/quotes/taiwan/status', { signal: controller.signal })
      .then(result => { setData(result); setFailed(false) })
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
    void load()
    const timer = window.setInterval(() => { void load() }, 60_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [])
  if (!data && !failed) return null
  return <div className="text-xs text-muted-foreground rounded-md border p-2" role="status">
    {failed ? '台股資料服務暫時無法連線' : <>
      台股行情：{data?.quotes.collection_health?.status === 'connected' ? '已連線' : '尚未就緒'}
      {!data?.quotes.observed_live_data && ' · 尚未收到盤中報價'}
      {data?.intraday.live_collection === false && ' · 分K為歷史資料'}
      {data?.calendar.status === 'unknown' && ' · 交易日曆未就緒'}
      {data?.active_instrument_counts?.TPEX === 0 && ' · 上櫃標的目錄未就緒'}
      {!!data?.unsubscribed_instrument_ids.length && ` · 尚未訂閱：${data.unsubscribed_instrument_ids.join(', ')}`}
      {!!data?.unresolved_symbols.length && ` · 需指定交易所：${data.unresolved_symbols.join(', ')}`}
    </>}
  </div>
}
