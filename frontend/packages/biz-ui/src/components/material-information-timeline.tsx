import {
  materialInformationStatusLabel,
  materialInformationStatusText,
  type MaterialInformationDisplayStatus,
} from '../lib/material-information-display'

interface TimelineItem {
  title: string
  publish_time: string
  source_family?: 'current' | 'history'
  source_label?: string
  event_identity?: string
  content?: string
  clause?: string
  fact_date?: string
  revision?: number
  latest_observed_at_utc?: string
  first_observed_at_utc?: string
  event_first_observed_at_utc?: string
  capture_id?: string
}

function publicationTime(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-TW', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(parsed)
}

export function MaterialInformationTimeline({ items, statuses, startDate, endDate }: {
  items: readonly TimelineItem[]
  statuses: Record<string, MaterialInformationDisplayStatus>
  startDate: string
  endDate: string
}) {
  return <div className="space-y-3">
    <div className="rounded border border-border/60 p-3 space-y-2 text-[11px] text-muted-foreground">
      <div>台股官方重大訊息：最新快照與歷史資料分開顯示，不會合併不同來源的事件。</div>
      <div>查詢日期：{startDate} 至 {endDate}（台北時間）</div>
      {(['current', 'history'] as const).map((source) => {
        const status = statuses[source]
        const label = source === 'current' ? 'TWSE 最新快照' : 'MOPS 歷史資料'
        if (!status) return <div key={source}>{label}：正在查詢。</div>
        const evidence = status.evidence
        const capture = evidence.latest_capture
        return <div key={source} className="space-y-1">
          <div>{label} · {materialInformationStatusLabel(status.status)} · {materialInformationStatusText(source, status)}</div>
          {typeof evidence.returned_count === 'number' && <div>回傳 {evidence.returned_count} 筆／留存 {evidence.retained_count} 筆{evidence.truncated ? ' · 結果已截斷' : ''}</div>}
          {capture && <div>最新取得時間（UTC）：{capture.received_at_utc}{capture.report_date ? ` · 快照報表日 ${capture.report_date}` : ''}</div>}
          {(evidence.acquisitions || []).map((acquisition: Record<string, any>) => <div key={acquisition.capture_id}>
            採集年度 {acquisition.query_year} · 覆蓋截止 {acquisition.coverage_through || '未建立覆蓋'} · 取得時間（UTC）{acquisition.received_at_utc}
          </div>)}
          {source === 'history' && (evidence.acquisitions || []).length > 0 && <div>採集年度與公告日期篩選分開；截止當日可能只有部分資料。留存資料不代表當時已知資訊。</div>}
          <a className="text-primary underline" href={source === 'current' ? 'https://openapi.twse.com.tw/v1/opendata/t187ap04_L' : 'https://mops.twse.com.tw/mops/'} target="_blank" rel="noreferrer">{source === 'current' ? 'TWSE 官方資料來源' : 'MOPS 官方資料來源'}</a>
        </div>
      })}
    </div>
    {items.length === 0 ? <div className="card p-6 text-[12px] text-muted-foreground text-center">
      目前沒有可顯示的事件；請參考上方來源狀態。資料尚未取得或不完整時，不能判定所選日期沒有公告。
    </div> : items.map((item) => <div key={`${item.source_family}:${item.event_identity}`} className="card p-4 space-y-2">
      <div className="text-[13px] font-medium text-foreground">{item.title}</div>
      <div className="text-[11px] text-muted-foreground">{item.source_label} · {publicationTime(item.publish_time)}（台北時間）</div>
      <div className="text-[10px] text-muted-foreground">事實發生日 {item.fact_date || '未提供'} · 修訂版本 {item.revision ?? '未提供'} · 最近觀察（UTC）{item.latest_observed_at_utc || '未提供'}</div>
      <details className="text-[11px]">
        <summary className="cursor-pointer text-muted-foreground">查看保留的來源原文</summary>
        {item.clause && <div className="mt-2 whitespace-pre-wrap break-words">{item.clause}</div>}
        {item.content && <pre className="mt-2 whitespace-pre-wrap break-words font-sans">{item.content}</pre>}
        <div className="mt-2 break-words text-muted-foreground">來源事件 ID：{item.event_identity}</div>
        <div className="break-words text-muted-foreground">事件首次觀察（UTC）：{item.event_first_observed_at_utc || '未提供'} · 此內容首次觀察（UTC）：{item.first_observed_at_utc || '未提供'}</div>
        <div className="break-words text-muted-foreground">擷取識別碼：{item.capture_id || '未提供'}</div>
        <div className="mt-2 text-muted-foreground">來源未提供事件專屬網頁；以上保留的原文可用於核對摘要。</div>
      </details>
    </div>)}
  </div>
}
