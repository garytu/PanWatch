import { describe, expect, it } from 'vitest'
import {
  buildMaterialInformationDisplay,
  materialInformationStatusText,
} from '@panwatch/biz-ui/lib/material-information-display'

describe('Taiwan material-information display', () => {
  it('keeps identical current and history identifiers in separate timeline entries', () => {
    const event = {
      announcement_date: '2024-03-12',
      announced_at: '2024-03-12T09:12:00+08:00',
      subject: '來源標題',
      clause: '來源 clause 原文',
      detail: '來源 detail 原文',
      fact_date: '2024-03-12',
      content_hash: 'source-hash',
      revision: 2,
      latest_observed_at_utc: '2026-10-04T03:17:23Z',
      source_event_id: 'same-provider-key',
      provider_key: 'same-provider-key',
      source_reference: {
        label: 'MOPS detail request',
        method: 'POST',
        url: 'https://mops.twse.com.tw/mops/api/t05st01_detail',
        selectors: { serialNumber: '1' },
        is_navigable_permalink: false,
      },
    }
    const block = (source_family: string, status: string) => ({
      instrument_id: 'TWSE:2608',
      source_family,
      block: {
        data: { instrument_id: 'TWSE:2608', source_family, events: [event] },
        status,
        reason: '',
        evidence: { history_complete: status === 'available' },
      },
    })

    const display = buildMaterialInformationDisplay(
      ['current', 'history'],
      [
        { status: 'fulfilled', value: block('current', 'partial') } as PromiseFulfilledResult<any>,
        { status: 'fulfilled', value: block('history', 'available') } as PromiseFulfilledResult<any>,
      ],
    )

    expect(display.items).toHaveLength(2)
    expect(display.items.map((item) => item.source_family)).toEqual(['current', 'history'])
    expect(display.items[0].event_identity).toBe(display.items[1].event_identity)
    expect(display.items[0].content).toBe('來源 detail 原文')
    expect(display.items[1].source_reference?.is_navigable_permalink).toBe(false)
    expect(display.items[1].url).toContain('/t05st01_detail')
  })

  it('does not turn missing snapshot data into a no-events claim and labels truncation', () => {
    const missingText = materialInformationStatusText('current', {
      status: 'missing', reason: 'no_current_capture', evidence: {},
    })
    expect(missingText).toContain('不能據此判定所選日期沒有公告')
    expect(missingText).not.toContain('該範圍內沒有事件')

    const truncatedText = materialInformationStatusText('current', {
      status: 'partial', reason: 'result_truncated', evidence: { truncated: true },
    })
    expect(truncatedText).toContain('只顯示部分項目')
  })

  it('deduplicates reobservations by authoritative identity and uses observation time for content rollback', () => {
    const event = { source_event_id: 'stable-id', announced_at: '2026-10-07T09:00:00+08:00',
      announcement_date: '2026-10-07', subject: '標題', clause: '', detail: '修訂內容',
      fact_date: '2026-10-07', revision: 3, latest_observed_at_utc: '2026-10-07T02:00:00Z' }
    const rollback = { ...event, detail: '回到原內容', revision: 1, latest_observed_at_utc: '2026-10-07T03:00:00Z' }
    const response = { block: { status: 'partial', reason: '', evidence: {}, data: { events: [event, rollback, event] } } }
    const display = buildMaterialInformationDisplay(['current'], [{ status: 'fulfilled', value: response } as any])
    expect(display.items).toHaveLength(1)
    expect(display.items[0].content).toBe('回到原內容')
    expect(display.items[0].revision).toBe(1)
    expect(display.items[0].event_identity).toBe('stable-id')
  })
})
