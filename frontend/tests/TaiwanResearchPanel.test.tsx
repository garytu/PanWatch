// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TaiwanResearchPanel } from '@panwatch/biz-ui/components/taiwan-research-panel'
import { researchApi } from '@panwatch/api'

vi.mock('@panwatch/api', () => ({ researchApi: { taiwan: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

function block(data: any, status = 'available', reason = 'selected_record_present', evidence: any = {}) {
  return { data, status, reason, evidence: { provider: 'twmd', ...evidence } }
}

it('shows source dates, exact values, units, nulls, and partial month coverage', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10' },
    blocks: {
      valuation: block({ instrument_id: 'TWSE:2330', observations: [{ trade_date: '2026-10-02', close_price: '1234.5000', pe_ratio: null, pb_ratio: '3.50', dividend_yield_pct: '0.88', dividend_reference_year: 114 }] }, 'partial', 'some_requested_dates_missing_or_absent', {
        source_contract: 'twse_daily_valuation/v1',
        freshness: {
          frequency: 'daily', data_period: '2026-10-02', report_date: null,
          publication_time: null, source_received_at_utc: '2026-10-03T00:00:00Z',
          source_receipt_age_seconds: 3600, data_period_age_days: 5,
          source_served_at: null, evaluated_at_utc: '2026-10-07T00:00:00Z',
          publisher_sla: null, age_status: 'age_known_sla_unknown',
          frequency_hint: '日資料；未驗證交易日曆或發布 SLA。',
          coverage: { block_status: 'partial', source_coverage_header: 'available=1;missing=1' },
        },
      }),
      institutional_flows: block({ observations: [{ trade_date: '2026-10-02', native_values: { total_institutional_net_shares: -5343414 } }] }),
      company_profile: block({ instrument_id: 'TWSE:2330', profile: { report_date: '2026-10-03', company_name: '台積電', industry_code: '24', paid_in_capital: '259323700670', issued_share_count: 25932370067 }, units: { paid_in_capital: 'TWD', issued_share_count: 'shares' } }, 'partial', 'retained_profile_snapshot_absent', {
        source_contract: 'twse_openapi_t187ap03_L/v1', retained_profile: { revision: 2, capture_id: 'profile-capture', received_at_utc: '2026-10-04T13:07:03Z', payload_sha256: 'profile-hash' },
        freshness: {
          frequency: 'latest_only_snapshot', data_period: '2026-10-03', report_date: '2026-10-03',
          publication_time: null, source_received_at_utc: '2026-10-04T13:07:03Z',
          source_receipt_age_seconds: 80000, data_period_age_days: 4,
          source_served_at: null, evaluated_at_utc: '2026-10-07T00:00:00Z',
          publisher_sla: null, age_status: 'age_known_sla_unknown', frequency_hint: '最新快照型資料；沒有 publisher SLA。',
          coverage: { block_status: 'partial', snapshot_coverage_status: 'AVAILABLE', latest_snapshot_presence: 'absent' },
          latest_snapshot: { report_date: '2026-10-03', source_received_at_utc: '2026-10-04T13:07:03Z', source_receipt_age_seconds: 80000, data_period_age_days: 4 },
        },
      }),
      monthly_revenues: block({ units: { revenue: 'TWD thousands (inferred from issuer notes; publisher strings retained)' }, months: [
        { data_month: '2026-07-01', presence: 'missing', row: null },
        { data_month: '2026-08-01', presence: 'present', row: { monthly_revenue: '514805337', month_over_month_pct: '3.21', year_over_year_pct: null, cumulative_revenue: '1133811744', cumulative_yoy_pct: '-0.61' } },
      ] }, 'partial', 'some_months_missing', { source_contract: 'mops_t21_sii_monthly_revenue/v1', selectors: { instrument_id: 'TWSE:2330', start_month: '2026-07', end_month: '2026-08' }, per_month_coverage: [
        { data_month: '2026-08-01', source_contract: 'mops_t21_sii_monthly_revenue/v1', report_date: '2026-09-10', received_at_utc: '2026-09-11T01:00:00Z', capture_id: 'revenue-capture', payload_sha256: 'revenue-hash' },
      ], per_month_presence_and_provenance: [
        { data_month: '2026-08-01', retained_row: { revision: 3, capture_id: 'row-capture', received_at_utc: '2026-09-11T01:00:00Z', payload_sha256: 'row-hash' } },
      ], freshness: {
        frequency: 'monthly', data_period: '2026-08', report_date: '2026-09-10',
        publication_time: null, source_received_at_utc: '2026-09-11T01:00:00Z',
        source_receipt_age_seconds: 2246400, data_period_age_days: 37,
        source_served_at: '2026-10-06T15:53:57Z', evaluated_at_utc: '2026-10-07T00:00:00Z',
        publisher_sla: null, age_status: 'age_known_sla_unknown',
        frequency_hint: '月資料；沒有推定發布時間或申報 SLA。',
        coverage: { block_status: 'partial', requested_month_count: 2, month_presence_counts: { missing: 1, present: 1 } },
      } }),
    },
    limitations: { financial_statements: { status: 'not_integrated', data: null, message: 'Not integrated.' } },
  })

  render(<TaiwanResearchPanel symbol="2330" market="TW" open />)

  await waitFor(() => expect(screen.getByText('官方估值')).toBeTruthy())
  expect(screen.getAllByText('部分可用').length).toBe(3)
  expect(screen.getByText('資料日 2026-10-02 · TWSE 官方來源')).toBeTruthy()
  expect(screen.getByText('1234.5000')).toBeTruthy()
  expect(screen.getByText('官方殖利率（%，股利年度 2025）')).toBeTruthy()
  expect(screen.getByText('0.88')).toBeTruthy()
  expect(screen.getByText('依來源股利年度採計的官方數值；與最新季度配息年化估算的口徑不同。')).toBeTruthy()
  expect(screen.getByText('實收資本額（TWD）')).toBeTruthy()
  expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  expect(screen.getByText('來源期間 2026-07 至 2026-08 · TWSE 官方來源 · TWD 千元（依來源資料推定）')).toBeTruthy()
  expect(screen.getByText('514805337')).toBeTruthy()
  expect(screen.getByText('3.21')).toBeTruthy()
  expect(screen.getAllByText('累計年增率（%）').length).toBe(2)
  expect(screen.getByText('來源沒有此月份覆蓋')).toBeTruthy()
  expect(screen.getByText('所選資料中最新期別 2026-10-02 · 報表日 未提供 · 期別距今 5 個日曆日')).toBeTruthy()
  expect(screen.getByText(/來源接收後 1 小時 · 覆蓋：已取得 1 日 · 未取得覆蓋 1 日/)).toBeTruthy()
  expect(screen.getByText(/2 個月：未取得覆蓋 1 月、有列示 1 月/)).toBeTruthy()
  expect(screen.getByText(/月份結束距今 37 個日曆日/)).toBeTruthy()
  expect(screen.getAllByText('來源發布時間：未提供').length).toBe(4)
  expect(screen.getByText('來源 TWSE 官方來源 · 報表日 2026-09-10')).toBeTruthy()
  expect(screen.getByText('來源契約：mops_t21_sii_monthly_revenue/v1')).toBeTruthy()
  expect(screen.getByText('來源接收時間：2026-09-11T01:00:00Z')).toBeTruthy()
  expect(screen.getByText('修訂版本：2')).toBeTruthy()
  expect(screen.getByText('修訂版本：3')).toBeTruthy()
  expect(screen.getByText('擷取識別碼：profile-capture')).toBeTruthy()
  expect(screen.getByText('擷取識別碼：revenue-capture, row-capture')).toBeTruthy()
  expect(screen.getByText(/月營收是月度公告資料/)).toBeTruthy()
})

it('explains ETF profile and revenue scope while showing the available blocks', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TPEX:006201',
    instrument: { venue: 'TPEX', symbol: '006201', security_type: 'ETF', is_active: true, name: '元大富櫃50' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10' },
    blocks: {
      valuation: block({ instrument_id: 'TPEX:006201', observations: [] }, 'unsupported', 'unsupported_valuation_selector', { source_contract: 'tpex_daily_valuation/v1' }),
      institutional_flows: block({ observations: [{ trade_date: '2026-10-02', native_values: { total_institutional_net_shares: 1000 } }] }),
      company_profile: block(null, 'unsupported', 'unsupported_etf'),
      monthly_revenues: block({ dataset: 'tpex_monthly_revenue_latest', qualification: 'unsupported_etf', coverage_status: 'AVAILABLE', units: { revenue: 'TWD thousands (inferred from issuer notes; publisher strings retained)' }, months: [
        { data_month: '2026-08-01', presence: 'not_in_captured_report', row: null },
      ] }, 'unsupported', 'unsupported_etf', { source_contract: 'tpex.openapi.mopsfin_t187ap05_O/v1.0.0', selectors: { instrument_id: 'TPEX:006201', start_month: '2026-08', end_month: '2026-08' } }),
    },
    limitations: { financial_statements: { status: 'not_integrated', data: null, message: 'Not integrated.' } },
  })

  render(<TaiwanResearchPanel symbol="TPEX:006201" market="TW" open />)

  await waitFor(() => expect(screen.getAllByText('不適用').length).toBe(3))
  expect(screen.getByText('TPEx 估值來源目前只支援四位數證券代碼。')).toBeTruthy()
  expect(screen.getByText('1000')).toBeTruthy()
  expect(screen.getByText('ETF 不發布這類發行公司月營收資料。')).toBeTruthy()
  expect(screen.getByText('ETF 不發布這類發行公司月營收或公司 profile 資料。')).toBeTruthy()
  expect(screen.getByText(/月營收是月度公告資料/)).toBeTruthy()
})

it('shows a provider error without inventing zero data', async () => {
  vi.mocked(researchApi.taiwan).mockRejectedValue(new Error('研究服務逾時'))
  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await waitFor(() => expect(screen.getByText('研究服務逾時')).toBeTruthy())
  expect(screen.queryByText('0')).toBeNull()
})

it('keeps dated flow coverage visible beside a timed-out profile without treating missing calendar dates as closures', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-10-02', end_date: '2026-10-05', start_month: '2026-07', end_month: '2026-08' },
    blocks: {
      valuation: block({ observations: [{ trade_date: '2026-10-05', close_price: '1234.50' }] }),
      institutional_flows: block({ observations: [{ trade_date: '2026-10-02', native_values: { total_institutional_net_shares: -123 } }] }, 'partial', 'some_requested_dates_missing', {
        freshness: { frequency: 'daily', data_period: '2026-10-02', report_date: null, data_period_age_days: 5, source_receipt_age_seconds: null,
          coverage: { reported_status_counts: { AVAILABLE: 1, MISSING: 3 }, selected_presence_counts: { present: 1, missing: 3 } },
          frequency_hint: '日資料；距今按日曆日計算。尚未核對交易日曆或來源更新期限；未取得覆蓋不代表休市或零值。' },
      }),
      company_profile: block(null, 'error', 'timeout', { freshness: { frequency: 'latest_only_snapshot', data_period: null, report_date: null, data_period_age_days: null, source_receipt_age_seconds: null, coverage: { block_status: 'error' } } }),
      monthly_revenues: block(null, 'missing', 'coverage_missing'),
    },
    limitations: { financial_statements: { status: 'not_integrated', data: null, message: 'Not integrated.' } },
  })
  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await waitFor(() => expect(screen.getByText('來源讀取逾時。')).toBeTruthy())
  expect(screen.getByText('1234.50')).toBeTruthy()
  expect(screen.getByText('-123')).toBeTruthy()
  expect(screen.getByText(/所選資料中最新期別 2026-10-02.*期別距今 5 個日曆日/)).toBeTruthy()
  expect(screen.getByText(/覆蓋：已取得 1 日 · 未取得覆蓋 3 日/)).toBeTruthy()
  expect(screen.getByText(/未取得覆蓋不代表休市或零值/)).toBeTruthy()
  expect(screen.getByText(/區塊讀取失敗 · 來源未回報覆蓋細節/)).toBeTruthy()
  expect(screen.queryByText(/undefined 日/)).toBeNull()
})

it('ignores a late response for the prior venue of the same code', async () => {
  let resolveTwse!: (payload: any) => void
  const twse = new Promise<any>((resolve) => { resolveTwse = resolve })
  const tpexPayload = {
    instrument_id: 'TPEX:2330',
    instrument: { venue: 'TPEX', symbol: '2330', security_type: 'EQUITY', is_active: true, name: 'TPEx 公司' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10' },
    blocks: {
      valuation: block({ observations: [{ trade_date: '2026-10-02', close_price: '22.00' }] }),
      institutional_flows: block({ observations: [] }),
      company_profile: block(null, 'missing', 'profile_snapshot_not_retained'),
      monthly_revenues: block({ months: [], units: { revenue: 'TWD thousands' } }, 'missing', 'coverage_missing', { selectors: { start_month: '2025-11', end_month: '2026-10' } }),
    },
    limitations: { financial_statements: { status: 'not_integrated', data: null, message: 'Not integrated.' } },
  }
  vi.mocked(researchApi.taiwan).mockReturnValueOnce(twse).mockResolvedValueOnce(tpexPayload as any)
  const view = render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await waitFor(() => expect(researchApi.taiwan).toHaveBeenCalledTimes(1))
  view.rerender(<TaiwanResearchPanel symbol="TPEX:2330" market="TW" open />)
  await waitFor(() => expect(researchApi.taiwan).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByText('TPEX:2330 · EQUITY · 區間 2026-09-08 至 2026-10-06')).toBeTruthy())
  expect(screen.getByText('官方殖利率（%，股利年度 未提供）')).toBeTruthy()
  expect(screen.getByText('來源未提供股利年度，無法確認採計期間；此處保留官方原值。')).toBeTruthy()
  resolveTwse({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: 'TWSE 公司' },
    selectors: tpexPayload.selectors,
    blocks: tpexPayload.blocks,
  })
  await Promise.resolve()
  expect(screen.getByText('TPEX:2330 · EQUITY · 區間 2026-09-08 至 2026-10-06')).toBeTruthy()
  expect(screen.queryByText('TWSE:2330 · EQUITY · 區間 2026-09-08 至 2026-10-06')).toBeNull()
})
