import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'

interface FeedStatus {
  quotes: { status?: string; observed_live_data?: boolean; collection_health?: { status?: string } }
  intraday: { live_collection?: boolean }
  calendar: { status?: string }
  unsubscribed_instrument_ids: string[]
  pending_subscription_instrument_ids: string[]
  subscription_control: { available: boolean; error?: string | null; instrument_ids: string[]; count: number | null; limit: number | null }
  unresolved_symbols: string[]
  active_instrument_counts: { TWSE: number; TPEX: number }
}

export function TaiwanFeedStatus() {
  const [data, setData] = useState<FeedStatus | null>(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const load = (signal?: AbortSignal) => fetchAPI<FeedStatus>('/quotes/taiwan/status', { signal })
    .then(result => { setData(result); setFailed(false) })
    .catch(() => { if (!signal?.aborted) setFailed(true) })
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    const timer = window.setInterval(() => { void load(controller.signal) }, 60_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [])
  const changeSubscription = async (instrumentId: string, method: 'PUT' | 'DELETE') => {
    setBusy(instrumentId)
    setActionError(null)
    try {
      await fetchAPI(`/quotes/taiwan/subscriptions/${encodeURIComponent(instrumentId)}`, { method })
      await load()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '訂閱更新失敗')
    } finally {
      setBusy(null)
    }
  }
  if (!data && !failed) return null
  return <div className="text-xs text-muted-foreground rounded-md border p-2" role="status">
    {failed ? '台股資料服務暫時無法連線' : <>
      台股行情：{data?.quotes.collection_health?.status === 'connected' ? '已連線' : '尚未就緒'}
      {!data?.quotes.observed_live_data && ' · 尚未收到盤中報價'}
      {data?.intraday.live_collection === false && ' · 分K為歷史資料'}
      {data?.calendar.status === 'unknown' && ' · 交易日曆未就緒'}
      {data?.active_instrument_counts?.TPEX === 0 && ' · 上櫃標的目錄未就緒'}
      {data?.subscription_control.available ? <>
        {' · 訂閱 '} {data.subscription_control.count}/{data.subscription_control.limit}
        {!!data.unsubscribed_instrument_ids.length && <div>尚未要求訂閱：{data.unsubscribed_instrument_ids.map(id => <button key={id} type="button" disabled={busy !== null} onClick={() => void changeSubscription(id, 'PUT')} className="ml-2 text-primary underline disabled:opacity-50">訂閱 {id}</button>)}</div>}
        {!!data.pending_subscription_instrument_ids.length && <div>等待行情源確認：{data.pending_subscription_instrument_ids.join(', ')}</div>}
        {!!data.subscription_control.instrument_ids.length && <div>已要求訂閱：{data.subscription_control.instrument_ids.map(id => <span key={id} className="mr-2">{id} <button type="button" disabled={busy !== null} onClick={() => void changeSubscription(id, 'DELETE')} className="text-primary underline disabled:opacity-50">取消</button></span>)}</div>}
      </> : ' · 訂閱控制未就緒'}
      {data?.subscription_control.error && ` · ${data.subscription_control.error}`}
      {!!data?.unresolved_symbols.length && ` · 需指定交易所：${data.unresolved_symbols.join(', ')}`}
      {actionError && <div role="alert">{actionError}</div>}
    </>}
  </div>
}
