import type { MaterialInformationQueryResult } from '@panwatch/api'

export type MaterialInformationSourceFamily = 'current' | 'history'

export interface MaterialInformationDisplayItem {
  source: MaterialInformationSourceFamily
  source_label: string
  source_family: MaterialInformationSourceFamily
  event_identity: string
  title: string
  content: string
  clause: string
  publish_time: string
  url: string
  fact_date: string
  content_hash: string
  revision: number
  latest_observed_at_utc: string
  first_observed_at_utc: string
  event_first_observed_at_utc: string
  capture_id: string
  source_reference: NonNullable<MaterialInformationQueryResult['block']['data']>['events'][number]['source_reference']
}

export interface MaterialInformationDisplayStatus {
  status: string
  reason: string
  evidence: Record<string, any>
}

export function materialInformationStatusText(
  source: MaterialInformationSourceFamily,
  item: MaterialInformationDisplayStatus,
): string {
  const sourceLabel = source === 'current' ? '最新快照' : '歷史資料'
  if (item.status === 'error') return `${sourceLabel}讀取失敗，請稍後再試。`
  if (item.status === 'unsupported') return `${sourceLabel}不支援此標的；官方重大訊息目前只支援四位數上市公司。`
  if (source === 'current') {
    if (item.evidence?.truncated) return '快照結果已截斷；只顯示部分項目，且最新快照不代表完整歷史。'
    if (item.status === 'missing') return '最新快照沒有可回傳項目；此來源不完整，不能據此判定所選日期沒有公告。'
    return '最新快照不是完整歷史流；日期含今日時，當日證據仍可能不完整。'
  }
  if (item.status === 'empty') return '完整的公司／年度資料涵蓋所選日期範圍，該範圍內沒有事件；不代表公告庫完整無缺。'
  if (item.status === 'missing') return '目前沒有完整的歷史採集證據，不能據此判定所選日期沒有公告。'
  if (item.status === 'partial') return item.evidence?.truncated
    ? '結果已截斷，列表不完整。'
    : '有部分歷史資料，但所選日期範圍仍有未取得的日期。'
  return '歷史資料在所選日期範圍有完整採集證據和事件；取得時間與事件發布時間分開。'
}

export function materialInformationStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    available: '有資料', partial: '部分資料', missing: '未取得資料', empty: '完整範圍內無事件',
    unsupported: '不支援', error: '讀取失敗',
  }
  return labels[status] || '狀態未知'
}

export function buildMaterialInformationDisplay(
  families: readonly MaterialInformationSourceFamily[],
  results: readonly PromiseSettledResult<MaterialInformationQueryResult>[],
): { statuses: Record<string, MaterialInformationDisplayStatus>; items: MaterialInformationDisplayItem[] } {
  const statuses: Record<string, MaterialInformationDisplayStatus> = {}
  const byIdentity = new Map<string, MaterialInformationDisplayItem>()
  results.forEach((result, index) => {
    const source = families[index]
    if (result.status === 'rejected') {
      statuses[source] = {
        status: 'error',
        reason: 'request_failed',
        evidence: {},
      }
      return
    }
    const block = result.value.block
    statuses[source] = { status: block.status, reason: block.reason, evidence: block.evidence || {} }
    for (const event of block.data?.events || []) {
      const identity = source === 'current' ? event.source_event_id : event.provider_key
      if (!identity) continue
      const item: MaterialInformationDisplayItem = {
        source,
        source_label: source === 'current' ? 'TWSE 最新快照' : 'MOPS 歷史來源',
        source_family: source,
        event_identity: identity,
        title: event.subject,
        content: event.detail,
        clause: event.clause,
        publish_time: event.announced_at,
        url: event.source_reference?.url || '',
        fact_date: event.fact_date,
        content_hash: event.content_hash,
        revision: event.revision,
        latest_observed_at_utc: event.latest_observed_at_utc,
        first_observed_at_utc: event.first_observed_at_utc,
        event_first_observed_at_utc: event.event_first_observed_at_utc,
        capture_id: event.capture_id,
        source_reference: event.source_reference,
      }
      const key = `${source}:${identity}`
      const previous = byIdentity.get(key)
      // Revision numbers identify content, including rollbacks, rather than time.
      if (!previous || Date.parse(item.latest_observed_at_utc) > Date.parse(previous.latest_observed_at_utc)) {
        byIdentity.set(key, item)
      }
    }
  })
  const items = [...byIdentity.values()]
  items.sort((left, right) => right.publish_time.localeCompare(left.publish_time))
  return { statuses, items }
}
