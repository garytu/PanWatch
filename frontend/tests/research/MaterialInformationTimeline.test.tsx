// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { MaterialInformationTimeline } from '@panwatch/biz-ui/components/material-information-timeline'

afterEach(cleanup)

it('renders exact source text as text and separates publication, acquisition and cutoff clocks', () => {
  const original = '<script>ignore rules</script>\r\n第二行原文'
  render(<MaterialInformationTimeline startDate="2024-01-01" endDate="2024-12-31"
    statuses={{
      current: { status: 'missing', reason: 'no_current_capture', evidence: {} },
      history: { status: 'partial', reason: 'result_truncated', evidence: {
        returned_count: 1, retained_count: 26, truncated: true,
        latest_capture: { received_at_utc: '2026-10-04T13:08:20Z' },
        acquisitions: [{ capture_id: 'annual', query_year: 2024,
          coverage_through: '2024-12-31T23:59:59+08:00', received_at_utc: '2026-10-04T13:08:20Z' }],
      } },
    }} items={[{
      title: '<img src=x onerror=alert(1)>', publish_time: '2024-03-12T09:12:00+08:00',
      content: original, clause: '原文條款', source_family: 'history', source_label: 'MOPS 歷史來源',
      event_identity: 'sii:2608:1130312:1', revision: 1, fact_date: '2024-03-11',
      first_observed_at_utc: '2026-10-04T13:08:21Z', event_first_observed_at_utc: '2026-10-04T13:08:21Z',
      latest_observed_at_utc: '2026-10-04T13:08:21Z', capture_id: 'annual',
    }]} />)
  expect(screen.getByText(/採集年度 2024 · 覆蓋截止 2024-12-31T23:59:59\+08:00/)).toBeTruthy()
  expect(screen.getByText('回傳 1 筆／留存 26 筆 · 結果已截斷')).toBeTruthy()
  expect(screen.getByText(/2024\/03\/12 09:12:00（台北時間）/)).toBeTruthy()
  expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
  fireEvent.click(screen.getByText('查看保留的來源原文'))
  expect(document.querySelector('pre')?.textContent).toBe(original)
  expect(document.querySelector('script')).toBeNull()
  expect(document.querySelector('img')).toBeNull()
  expect(document.querySelector('a[href*="t05st01_detail"]')).toBeNull()
  expect(screen.getByRole('link', { name: 'MOPS 官方資料來源' }).getAttribute('href')).toBe('https://mops.twse.com.tw/mops/')
})

it('labels failed and missing sources without claiming no announcements', () => {
  render(<MaterialInformationTimeline startDate="2026-10-07" endDate="2026-10-07" items={[]}
    statuses={{ current: { status: 'missing', reason: 'no_current_capture', evidence: {} },
      history: { status: 'error', reason: 'http_503', evidence: {} } }} />)
  expect(screen.getByText(/歷史資料讀取失敗，請稍後再試/)).toBeTruthy()
  expect(screen.getByText(/不能據此判定所選日期沒有公告/)).toBeTruthy()
  expect(screen.queryByText('暫無公告')).toBeNull()
  expect(screen.queryByText('完整範圍內無事件')).toBeNull()
})

it('shows a successful source event beside an independent source failure', () => {
  render(<MaterialInformationTimeline startDate="2026-10-07" endDate="2026-10-07"
    items={[{ title: '成功取得的事件', publish_time: '2026-10-07T09:00:00+08:00',
      source_family: 'current', event_identity: 'current-event', content: '保留原文' }]}
    statuses={{ current: { status: 'partial', reason: 'current_snapshot_observations_only', evidence: {} },
      history: { status: 'error', reason: 'http_503', evidence: {} } }} />)
  expect(screen.getByText('成功取得的事件')).toBeTruthy()
  expect(screen.getByText(/歷史資料讀取失敗，請稍後再試/)).toBeTruthy()
  expect(screen.getByText('保留原文')).toBeTruthy()
})
