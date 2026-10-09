// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TaiwanResearchPanel } from '@panwatch/biz-ui/components/taiwan-research-panel'
import { BrokerFlowPanel } from '@panwatch/biz-ui/components/broker-flow-panel'
import { defaultFiscalScope, FinancialStatementsPanel, taipeiTodayParts } from '@panwatch/biz-ui/components/financial-statements-panel'
import { researchApi } from '@panwatch/api'

vi.mock('@panwatch/api', () => ({ researchApi: { taiwan: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

function block(data: any, status = 'available', reason = 'selected_record_present', evidence: any = {}) {
  return { data, status, reason, evidence: { provider: 'twmd', ...evidence } }
}

function supplementalBlocks() {
  return {
    margin_short_sale: block({ instrument_id: 'TWSE:2330', native_unit: 'trading_units', latest: null }, 'unknown', 'selected_presence_unreported'),
    shareholder_distribution: block({ instrument_id: 'TWSE:2330', native_unit: 'shares', latest: null }, 'unknown', 'selected_presence_unreported'),
    broker_flow: block(null, 'unknown', 'selected_presence_unreported'),
    corporate_actions: block({
      instrument_id: 'TWSE:2330', ex_right_dividend: { data: [] }, capital_reduction: { data: [] },
      known_event_dates: [],
      price_interpretation: {
        prior_close_and_reference_price_are_not_cash_dividend_amounts: true,
        rights_dividend_value_is_combined_adjustment_not_cash_dividend: true,
        announcement_time: null, payment_time: null,
        raw_daily_bars_are_not_adjusted_by_these_annotations: true,
      },
    }, 'unknown', 'coverage_not_returned', { source_contract: 'TWSE TWT49U and TWTAUU realized results' }),
  }
}

function currentSelectors(fiscalYear: number, fiscalQuarter: number) {
  const { year, month, day } = taipeiTodayParts()
  const todayUtc = Date.UTC(year, month - 1, day)
  const isoDate = (offsetDays: number) => new Date(todayUtc + offsetDays * 86_400_000).toISOString().slice(0, 10)
  const endMonthDate = new Date(Date.UTC(year, month - 2, 1))
  const startMonthDate = new Date(Date.UTC(endMonthDate.getUTCFullYear(), endMonthDate.getUTCMonth() - 11, 1))
  const monthLabel = (value: Date) => `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, '0')}`
  return {
    start_date: isoDate(-30), end_date: isoDate(-1),
    start_month: monthLabel(startMonthDate), end_month: monthLabel(endMonthDate),
    fiscal_year: fiscalYear, fiscal_quarter: fiscalQuarter, statement: null,
  }
}

function completeBlocks(providerScope: string, overrides: Record<string, any> = {}) {
  const blocks = {
    valuation: block({ observations: [] }),
    institutional_flows: block({ observations: [] }),
    company_profile: block({ profile: null }),
    monthly_revenues: block({ months: [], units: {} }),
    ...supplementalBlocks(),
    financial_statements: block(null, 'missing', 'never_collected'),
    benchmark_comparison: block({ observations: [], comparison: null }, 'unknown', 'coverage_not_returned'),
    ...overrides,
  }
  return Object.fromEntries(Object.entries(blocks).map(([name, value]) => [name, {
    ...value,
    evidence: { ...value.evidence, provider_scope: providerScope },
  }]))
}

it('shows source dates, exact values, units, nulls, and partial month coverage', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10' },
    blocks: {
      ...supplementalBlocks(),
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
    limitations: { financial_statements: { status: 'limited_scope', message: 'TWSE industry-24 retained reports.' } },
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
  expect(screen.getAllByText('來源發布時間：未提供').length).toBe(7)
  expect(screen.getByText('來源 TWSE 官方來源 · 報表日 2026-09-10')).toBeTruthy()
  expect(screen.getByText('來源契約：mops_t21_sii_monthly_revenue/v1')).toBeTruthy()
  expect(screen.getByText('來源接收時間：2026-09-11T01:00:00Z')).toBeTruthy()
  expect(screen.getByText('修訂版本：2')).toBeTruthy()
  expect(screen.getByText('修訂版本：3')).toBeTruthy()
  expect(screen.getByText('擷取識別碼：profile-capture')).toBeTruthy()
  expect(screen.getByText('擷取識別碼：revenue-capture, row-capture')).toBeTruthy()
  expect(screen.getByText(/月營收是月度公告資料/)).toBeTruthy()
})

it('lets the stock research entry select a historical fiscal year and quarter', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10', fiscal_year: 2026, fiscal_quarter: 3, statement: null },
    blocks: {
      valuation: block({ observations: [] }),
      institutional_flows: block({ observations: [] }),
      company_profile: block(null),
      monthly_revenues: block({ months: [] }),
      ...supplementalBlocks(),
      financial_statements: block(null, 'missing', 'never_collected'),
    },
    limitations: { financial_statements: { status: 'limited_scope', message: '' } },
  } as any)

  render(<TaiwanResearchPanel symbol="2330" market="TW" open />)

  await waitFor(() => expect(researchApi.taiwan).toHaveBeenCalled())
  const firstCall = vi.mocked(researchApi.taiwan).mock.calls[0]
  expect(firstCall[0]).toBe('2330')
  expect(firstCall[1]).toEqual({ fiscal_year: expect.any(Number), fiscal_quarter: expect.any(Number) })
  expect(firstCall[2]?.signal).toBeInstanceOf(AbortSignal)
  fireEvent.change(screen.getByRole('combobox', { name: '財報年度' }), { target: { value: '2024' } })
  fireEvent.change(screen.getByRole('combobox', { name: '財報季度' }), { target: { value: '4' } })

  await waitFor(() => expect(vi.mocked(researchApi.taiwan).mock.calls.at(-1)?.[1]?.fiscal_year).toBe(2024))
  const lastCall = vi.mocked(researchApi.taiwan).mock.calls.at(-1)
  expect(lastCall?.[1]?.fiscal_quarter).toBe(4)
  expect(lastCall?.[1]?.blocks).toContain('financial_statements')
  expect(lastCall?.[2]?.signal).toBeInstanceOf(AbortSignal)
})

it('keeps compatible successful blocks and retries only failed blocks', async () => {
  const fiscalScope = defaultFiscalScope()
  const selectors = currentSelectors(fiscalScope.year, fiscalScope.quarter)
  const firstBlocks = completeBlocks('https://provider.example', {
    valuation: block({ observations: [{ trade_date: selectors.end_date, close_price: '1234.50' }] }),
    company_profile: block(null, 'error', 'timeout'),
  })
  const refreshedProfile = block({ profile: {
    report_date: selectors.end_date, company_name: '台積電', industry_code: '24',
    paid_in_capital: '259323700670', issued_share_count: 25932370067,
  }, units: { paid_in_capital: 'TWD', issued_share_count: 'shares' } }, 'available', 'selected_record_present', {
    provider_scope: 'https://provider.example',
  })
  vi.mocked(researchApi.taiwan)
    .mockResolvedValueOnce({
      instrument_id: 'TWSE:2330',
      instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
      selectors, requested_blocks: Object.keys(firstBlocks), blocks: firstBlocks,
      limitations: { financial_statements: { status: 'limited_scope', message: '' } },
    } as any)
    .mockResolvedValueOnce({
      instrument_id: 'TWSE:2330',
      instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
      selectors, requested_blocks: ['company_profile'], blocks: { company_profile: refreshedProfile },
      limitations: { financial_statements: { status: 'limited_scope', message: '' } },
    } as any)

  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await screen.findByText('來源讀取逾時。')
  expect(screen.getByText('1234.50')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: '重新載入官方研究資料' }))
  await waitFor(() => expect(researchApi.taiwan).toHaveBeenCalledTimes(2))
  expect(vi.mocked(researchApi.taiwan).mock.calls[1][1]?.blocks).toEqual(['company_profile'])
  await screen.findByText('259323700670')
  expect(screen.getByText('1234.50')).toBeTruthy()
  expect(screen.queryByText('來源讀取逾時。')).toBeNull()
})

it('drops period-incompatible and other-provider blocks before merging a selective response', async () => {
  const initialScope = defaultFiscalScope()
  const selectors = currentSelectors(initialScope.year, initialScope.quarter)
  const newQuarter = initialScope.quarter
  let resolveNext!: (payload: any) => void
  const nextResponse = new Promise<any>((resolve) => { resolveNext = resolve })
  vi.mocked(researchApi.taiwan)
    .mockResolvedValueOnce({
      instrument_id: 'TWSE:2330',
      instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
      selectors, requested_blocks: Object.keys(completeBlocks('https://old-provider.example')),
      blocks: completeBlocks('https://old-provider.example', {
        valuation: block({ observations: [{ trade_date: selectors.end_date, close_price: '1234.50' }] }),
        financial_statements: block({ fiscal_year: initialScope.year, fiscal_quarter: initialScope.quarter,
          report: { member_filename: 'old-period-report.html', semantic_revision_id: 'old-revision' }, facts: [] }),
      }),
      limitations: { financial_statements: { status: 'limited_scope', message: '' } },
    } as any)
    .mockReturnValueOnce(nextResponse)

  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await screen.findByText('1234.50')
  const oldPeriodLabel = new RegExp(`${initialScope.year}Q${initialScope.quarter} · 合併 · old-period-report.html`)
  expect(screen.getByText(oldPeriodLabel)).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox', { name: '財報年度' }), { target: { value: '2024' } })
  await waitFor(() => expect(researchApi.taiwan).toHaveBeenCalledTimes(2))
  expect(screen.queryByText(oldPeriodLabel)).toBeNull()
  expect(screen.getByText('1234.50')).toBeTruthy()
  expect(vi.mocked(researchApi.taiwan).mock.calls[1][1]?.blocks).toEqual(['financial_statements'])
  resolveNext({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: currentSelectors(2024, newQuarter), requested_blocks: ['financial_statements'],
    blocks: { financial_statements: block(null, 'missing', 'never_collected', { provider_scope: 'https://new-provider.example' }) },
    limitations: { financial_statements: { status: 'limited_scope', message: '' } },
  })
  await waitFor(() => expect(screen.getByText(/TWSE:2330 · EQUITY · 區間/)).toBeTruthy())
  expect(screen.queryByText('1234.50')).toBeNull()
})

it('renders current and comparative source facts without deriving quarterly values or rescaling twice', () => {
  const duration = (year: number) => ({
    kind: 'duration', instant: null, start_date: `${year}-01-01`, end_date: `${year}-09-30`,
  })
  const instant = (value: string) => ({ kind: 'instant', instant: value, start_date: null, end_date: null })
  const ordinals: Record<string, number> = {}
  const fact = (statement: string, concept: string, value: string, lexical: string, scale: number, period: any, unit = 'TWD') => ({
    statement, occurrence_ordinal: ordinals[statement] = (ordinals[statement] || 0) + 1, concept_qname: `{urn:ifrs}${concept}`,
    context: { source_id: 'source-context', entity_identifier: '2330', entity_scheme: 'http://www.twse.com.tw', period, dimensions: [] },
    unit: { source_id: unit, numerator: ['{http://www.xbrl.org/2003/iso4217}TWD'], denominator: [] },
    value, is_nil: false, lexical_value: lexical, format_qname: null, scale, sign: null, decimals: null, precision: null,
  })
  const facts = [
    fact('balance_sheet', 'Assets', '6691938000000', '6,691,938,000', 3, instant('2024-09-30')),
    fact('balance_sheet', 'Assets', '5532371215000', '5,532,371,215', 3, instant('2023-12-31')),
    fact('comprehensive_income', 'Revenue', '2894307699000', '2,894,307,699', 3, duration(2024)),
    fact('comprehensive_income', 'Revenue', '2161738540000', '2,161,738,540', 3, duration(2023)),
    { ...fact('comprehensive_income', 'BasicEarningsLossPerShare', '45.25', '45.25', 0, duration(2024), 'EarningsPerShare'), unit: { source_id: 'EarningsPerShare', numerator: ['{http://www.xbrl.org/2003/iso4217}TWD'], denominator: ['{http://www.xbrl.org/2003/instance}shares'] } },
  ]
  render(<FinancialStatementsPanel block={block({
    instrument_id: 'TWSE:2330', fiscal_year: 2024, fiscal_quarter: 4, report_scope: 'consolidated',
    qualification: { status: 'qualified', reason: 'twse_equity_industry_24' },
    coverage: { status: 'AVAILABLE', latest_discovery_presence: 'present' },
    report: { member_filename: 'mops-report.html', semantic_revision_id: 'revision-1', original_received_at_utc: '2026-10-04T13:00:00Z' },
    facts, returned_fact_count: 5, total_fact_count: 5, truncated: false,
  }) as any} />)

  fireEvent.click(screen.getByText(/綜合損益表 · 3 筆回傳事實/))
  fireEvent.click(screen.getByText(/資產負債表 · 2 筆回傳事實/))
  expect(screen.getAllByText(/年初至今（YTD）/).length).toBeGreaterThan(0)
  expect(screen.queryByText(/全年期間/)).toBeNull()
  expect(screen.getByText('6691938000000')).toBeTruthy()
  expect(screen.getByText('6,691,938,000')).toBeTruthy()
  expect(screen.getAllByText('45.25')).toHaveLength(2)
  expect(screen.getByText(/scale=0/)).toBeTruthy()
  expect(screen.getByText(/沒有推導單季值或財務比率/)).toBeTruthy()
})

it('distinguishes unsupported, no-report discovery, and truncated partial statements', () => {
  const unsupported = block({ qualification: { status: 'unsupported', reason: 'financial_statements_twse_only' }, coverage: { latest_discovery_presence: 'missing' }, report: null, facts: [] }, 'unsupported', 'financial_statements_twse_only')
  const noReport = block({ qualification: { status: 'qualified' }, coverage: { status: 'MISSING', latest_discovery_presence: 'not_advertised', reason: 'report_not_advertised' }, report: null, facts: [] }, 'missing', 'report_not_advertised')
  const partial = block({
    fiscal_year: 2024, fiscal_quarter: 4, report_scope: 'consolidated',
    qualification: { status: 'qualified' }, coverage: { status: 'AVAILABLE', latest_discovery_presence: 'present' },
    report: { member_filename: 'report.html', semantic_revision_id: 'revision-1' }, facts: [],
    returned_fact_count: 1, total_fact_count: 2, truncated: true,
  }, 'partial', 'result_truncated')

  const view = render(<FinancialStatementsPanel block={unsupported as any} />)
  expect(screen.getByText(/不支援：financial_statements_twse_only/)).toBeTruthy()
  view.rerender(<FinancialStatementsPanel block={noReport as any} />)
  expect(screen.getByText(/最新成功發現未列出此報表/)).toBeTruthy()
  view.rerender(<FinancialStatementsPanel block={partial as any} />)
  expect(screen.getByText(/結果已截斷，只返回 1\/2 筆，屬部分報表/)).toBeTruthy()
})

it('shows native margin lots and TDCC denominator, and leaves a missing prior week uncomputed', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10' },
    blocks: {
      ...supplementalBlocks(),
      valuation: block({ observations: [] }, 'unknown', 'selected_presence_unreported'),
      institutional_flows: block({ observations: [] }, 'unknown', 'selected_presence_unreported'),
      company_profile: block(null, 'unknown', 'selected_presence_unreported'),
      monthly_revenues: block({ months: [] }, 'unknown', 'selected_presence_unreported'),
      margin_short_sale: block({
        instrument_id: 'TWSE:2330', native_unit: 'trading_units',
        latest: { trade_date: '2026-10-06', margin_balance: 123456, short_sale_balance: 789 },
        previous: null, changes: { margin_balance: null, short_sale_balance: null },
      }, 'available', 'selected_record_present', {
        endpoint: '/api/v1/margin-short-sale',
        dataset_coverage: [{ dataset: 'twse_margin_short_sale', partition_key: '2026-10-06', status: 'AVAILABLE', record_count: 1 }],
        freshness: { frequency: 'daily', data_period: '2026-10-06', data_period_age_days: 1,
          coverage: { reported_status_counts: { AVAILABLE: 1 } }, frequency_hint: '日資料；來源未提供發布 SLA。' },
      }),
      shareholder_distribution: block({
        instrument_id: 'TWSE:2330', native_unit: 'shares', report_date: '2026-10-02',
        report_variant: 'bulk_current', previous: null, changes: null,
        comparison_reason: 'previous_week_unavailable_or_variant_changed',
        latest: {
          report_date: '2026-10-02', report_variant: 'bulk_current', total_share_count: 1000000,
          total_holder_accounts: 20000,
          large_holding: { threshold: '>400,000 shares', minimum_shares: 400001, share_count: 500000,
            percentage_of_official_total: '50.000000', denominator_share_count: 1000000 },
          buckets: [
            { source_level: 12, source_tier_label: '400,001-600,000', holder_count: 10, share_count: 200000 },
            { source_level: 13, source_tier_label: '600,001-800,000', holder_count: 5, share_count: 150000 },
            { source_level: 14, source_tier_label: '800,001-1,000,000', holder_count: 3, share_count: 100000 },
            { source_level: 15, source_tier_label: '1,000,001以上', holder_count: 2, share_count: 50000 },
          ],
        },
      }, 'available', 'selected_record_present', {
        endpoint: '/api/v1/shareholder-distribution',
        per_period_provenance: [{ report_date: '2026-10-02', report_variant: 'bulk_current', provider: 'tdcc_open_data_1_5' }],
        freshness: { frequency: 'weekly', data_period: '2026-10-02', report_date: '2026-10-02', data_period_age_days: 5,
          coverage: { reported_status_counts: { AVAILABLE: 1 } }, frequency_hint: '週資料；來源未提供發布 SLA。' },
      }),
      broker_flow: block(null, 'unknown', 'selected_presence_unreported'),
    },
    limitations: { financial_statements: { status: 'limited_scope', message: 'TWSE industry-24 retained reports.' } },
  } as any)

  render(<TaiwanResearchPanel symbol="2330" market="TW" open />)

  await waitFor(() => expect(screen.getByText('融資融券')).toBeTruthy())
  expect(screen.getByText('交易日 2026-10-06 · TWSE 官方來源 · trading_units')).toBeTruthy()
  expect(screen.getByText('123456')).toBeTruthy()
  expect(screen.getByText('集保持股分布')).toBeTruthy()
  expect(screen.getByText('報表日 2026-10-02 · bulk_current · TDCC 集保來源 · 股數為股')).toBeTruthy()
  expect(screen.getByText('集保總股數（官方總計）')).toBeTruthy()
  expect(screen.getByText('1000000')).toBeTruthy()
  expect(screen.getByText('500000')).toBeTruthy()
  expect(screen.getByText('50.000000')).toBeTruthy()
  expect(screen.getByText('同一來源變體的前一週資料缺少，未計算變化。')).toBeTruthy()
  expect(screen.getByText(/保管帳戶分布不代表實際股東或投資人身分/)).toBeTruthy()
})

it('explains ETF profile and revenue scope while showing the available blocks', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TPEX:006201',
    instrument: { venue: 'TPEX', symbol: '006201', security_type: 'ETF', is_active: true, name: '元大富櫃50' },
    selectors: { start_date: '2026-09-08', end_date: '2026-10-06', start_month: '2025-11', end_month: '2026-10' },
    blocks: {
      ...supplementalBlocks(),
      shareholder_distribution: block(null, 'unsupported', 'tdcc_contract_is_twse_four_digit_only'),
      broker_flow: block(null, 'unsupported', 'twse_four_digit_only'),
      valuation: block({ instrument_id: 'TPEX:006201', observations: [] }, 'unsupported', 'unsupported_valuation_selector', { source_contract: 'tpex_daily_valuation/v1' }),
      institutional_flows: block({ observations: [{ trade_date: '2026-10-02', native_values: { total_institutional_net_shares: 1000 } }] }),
      company_profile: block(null, 'unsupported', 'unsupported_etf'),
      monthly_revenues: block({ dataset: 'tpex_monthly_revenue_latest', qualification: 'unsupported_etf', coverage_status: 'AVAILABLE', units: { revenue: 'TWD thousands (inferred from issuer notes; publisher strings retained)' }, months: [
        { data_month: '2026-08-01', presence: 'not_in_captured_report', row: null },
      ] }, 'unsupported', 'unsupported_etf', { source_contract: 'tpex.openapi.mopsfin_t187ap05_O/v1.0.0', selectors: { instrument_id: 'TPEX:006201', start_month: '2026-08', end_month: '2026-08' } }),
    },
    limitations: { financial_statements: { status: 'limited_scope', message: 'TWSE industry-24 retained reports.' } },
  })

  render(<TaiwanResearchPanel symbol="TPEX:006201" market="TW" open />)

  await waitFor(() => expect(screen.getAllByText('不適用').length).toBe(4))
  expect(screen.getByText('TPEx 估值來源目前只支援四位數證券代碼。')).toBeTruthy()
  expect(screen.getByText('1000')).toBeTruthy()
  expect(screen.getByText('ETF 不發布這類發行公司月營收資料。')).toBeTruthy()
  expect(screen.getByText('ETF 不發布這類發行公司月營收或公司 profile 資料。')).toBeTruthy()
  expect(screen.getByText('集保持股分布目前只支援四位數上市標的。')).toBeTruthy()
  expect(screen.getByText('券商分點目前只支援四位數上市標的；上櫃標的尚不支援。')).toBeTruthy()
  expect(screen.getByText(/月營收是月度公告資料/)).toBeTruthy()
})

it('shows a provider error without inventing zero data', async () => {
  vi.mocked(researchApi.taiwan).mockRejectedValue(new Error('研究服務逾時'))
  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await waitFor(() => expect(screen.getByText('研究服務逾時')).toBeTruthy())
  expect(screen.queryByText('0')).toBeNull()
})

it('keeps Capital lots and TWSE shares separate and explains bounded concentration and detail gaps', () => {
  render(<BrokerFlowPanel block={block({
    quantity_range: { start_date: '2026-07-23', end_date: '2026-07-24', status: 'available', reason: 'selected_records_present' },
    quantity_groups: [
      {
        provider: 'capital', native_unit: 'lots', precision_shares: 1000,
        top_buy: [{ source_branch_key: 'capital:4:12340001', branch_code: '0001', branch_name: 'Capital 台北', buy_native: 9, sell_native: 1, net_native: 8, share_of_observed_group_pct: '100.0000', revisions: ['cap-r1'] }],
        top_sell: [{ source_branch_key: 'capital:4:12340001', branch_code: '0001', branch_name: 'Capital 台北', buy_native: 9, sell_native: 1, net_native: 8, share_of_observed_group_pct: '100.0000', revisions: ['cap-r1'] }],
        top_n_buy_concentration_pct: '100.0000', top_n_sell_concentration_pct: '100.0000',
        observed_buy_denominator_native: 9, observed_sell_denominator_native: 1,
        coverage_complete_for_source_dates: false, coverage_status_counts: { MISSING: 1, AVAILABLE: 1 },
        coverage_missing_dates: ['2026-07-23'], revision_ids: ['cap-r1'],
        denominator_definition: 'Observed Capital rows only; no all-market denominator.',
      },
      {
        provider: 'twse', native_unit: 'shares', precision_shares: 1,
        top_buy: [{ source_branch_key: 'twse:0001', branch_code: '0001', branch_name: 'TWSE 台北', buy_native: 100000, sell_native: 50000, net_native: 50000, share_of_observed_group_pct: '100.0000', revisions: ['twse-r1'] }],
        top_sell: [{ source_branch_key: 'twse:0001', branch_code: '0001', branch_name: 'TWSE 台北', buy_native: 100000, sell_native: 50000, net_native: 50000, share_of_observed_group_pct: '100.0000', revisions: ['twse-r1'] }],
        top_n_buy_concentration_pct: '100.0000', top_n_sell_concentration_pct: '100.0000',
        observed_buy_denominator_native: 100000, observed_sell_denominator_native: 50000,
        coverage_complete_for_source_dates: true, coverage_status_counts: { AVAILABLE: 1 },
        coverage_missing_dates: [], revision_ids: ['twse-r1'],
        denominator_definition: 'Observed TWSE rows only; no all-market denominator.',
      },
    ],
    quantity_observations: [{ provider: 'twse', trade_date: '2026-07-24', source_branch_key: 'twse:0001', branch_code: '0001', branch_name: 'TWSE 台北', buy_vwap: '100.123456', sell_vwap: null }],
    coverage_range: { start_date: '2026-07-23', end_date: '2026-07-24', status: 'partial', reason: 'some_requested_dates_missing_or_failed' },
    coverage_status_counts_by_provider: { capital: { MISSING: 1 }, twse: { AVAILABLE: 1 } },
    coverage_observations: [],
    price_levels: { trade_date: '2026-07-24', status: 'not_materialized', reason: 'detail_projection_not_materialized', observations: [], revision_consistency_with_same_date_quantities: 'unknown', no_rows_interpretation: null },
    revision_consistency_warnings: [],
  }, 'partial', 'broker_flow_source_scope_limited', { vwap_meaning: 'Source transaction VWAP; not position cost basis.' }) as any} />)

  expect(screen.getByText('Capital · 張（原生單位）')).toBeTruthy()
  expect(screen.getByText('TWSE BSR · 股（精確股數）')).toBeTruthy()
  expect(screen.getAllByText('Capital 台北（0001）').length).toBe(2)
  expect(screen.getAllByText('TWSE 台北（0001）').length).toBe(2)
  expect(screen.getAllByText(/分母為此來源在所選期間已回傳分點/).length).toBe(2)
  expect(screen.getByText('來源買賣成交均價（VWAP）')).toBeTruthy()
  expect(screen.getByText(/100\.123456/)).toBeTruthy()
  expect(screen.getByText('買賣 VWAP 是來源成交均價（TWD），不是分點持倉成本。')).toBeTruthy()
  expect(screen.getByText('來源尚未保留此標的在該日的成交價格明細。')).toBeTruthy()
})

it('keeps dated flow coverage visible beside a timed-out profile without treating missing calendar dates as closures', async () => {
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-10-02', end_date: '2026-10-05', start_month: '2026-07', end_month: '2026-08' },
    blocks: {
      ...supplementalBlocks(),
      valuation: block({ observations: [{ trade_date: '2026-10-05', close_price: '1234.50' }] }),
      institutional_flows: block({ observations: [{ trade_date: '2026-10-02', native_values: { total_institutional_net_shares: -123 } }] }, 'partial', 'some_requested_dates_missing', {
        freshness: { frequency: 'daily', data_period: '2026-10-02', report_date: null, data_period_age_days: 5, source_receipt_age_seconds: null,
          coverage: { reported_status_counts: { AVAILABLE: 1, MISSING: 3 }, selected_presence_counts: { present: 1, missing: 3 } },
          frequency_hint: '日資料；距今按日曆日計算。尚未核對交易日曆或來源更新期限；未取得覆蓋不代表休市或零值。' },
      }),
      company_profile: block(null, 'error', 'timeout', { freshness: { frequency: 'latest_only_snapshot', data_period: null, report_date: null, data_period_age_days: null, source_receipt_age_seconds: null, coverage: { block_status: 'error' } } }),
      monthly_revenues: block(null, 'missing', 'coverage_missing'),
    },
    limitations: { financial_statements: { status: 'limited_scope', message: 'TWSE industry-24 retained reports.' } },
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
      ...supplementalBlocks(),
      shareholder_distribution: block(null, 'unsupported', 'tdcc_contract_is_twse_four_digit_only'),
      valuation: block({ observations: [{ trade_date: '2026-10-02', close_price: '22.00' }] }),
      institutional_flows: block({ observations: [] }),
      company_profile: block(null, 'missing', 'profile_snapshot_not_retained'),
      monthly_revenues: block({ months: [], units: { revenue: 'TWD thousands' } }, 'missing', 'coverage_missing', { selectors: { start_month: '2025-11', end_month: '2026-10' } }),
    },
    limitations: { financial_statements: { status: 'limited_scope', message: 'TWSE industry-24 retained reports.' } },
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

it('shows producer failure, ambiguous empty detail and revision limits beside independent component states', () => {
  render(<BrokerFlowPanel block={block({
    quantity_range: { status: 'available' }, quantity_groups: [], quantity_observations: [],
    coverage_range: { status: 'partial' }, coverage_status_counts_by_provider: { twse: { FAILED: 1 } },
    coverage_observations: [{ provider: 'twse', trade_date: '2026-10-02', status: 'FAILED', failure_reason: 'archive parse failed' }],
    price_levels: { trade_date: '2026-10-02', status: 'unknown', reason: 'materialized_no_rows_status_unknown', observations: [] },
    revision_consistency_warnings: ['twse:2026-10-02 quantity count or revision conflicts with coverage'],
  }, 'partial') as any} />)
  expect(screen.getByText(/TWSE BSR：來源處理失敗 1 日/)).toBeTruthy()
  expect(screen.getByText(/來源失敗：archive parse failed/)).toBeTruthy()
  expect(screen.getByText('來源沒有回傳成交價格列，目前無法確認是無成交明細（EMPTY）或處理失敗（FAILED）。')).toBeTruthy()
  expect(screen.getByText(/分點數量、覆蓋或明細的筆數／修訂不一致/)).toBeTruthy()
})

it('keeps same-day corporate action kinds, exact values, partial source errors, and unknown coverage visible', async () => {
  const empty = block(null, 'unknown', 'coverage_not_returned')
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330', instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: 'Fixture' },
    selectors: { start_date: '2026-06-01', end_date: '2026-06-30' },
    blocks: {
      ...supplementalBlocks(), valuation: empty, institutional_flows: empty, company_profile: empty, monthly_revenues: empty,
      corporate_actions: block({ instrument_id: 'TWSE:2330',
        ex_right_dividend: { status: 'available', data: [
          { instrument_id: 'TWSE:2330', effective_date: '2026-06-02', action_kind: 'ex_right', prior_close: '100.00', reference_price: '98.765', rights_dividend_value: '-1.235' },
          { instrument_id: 'TWSE:2330', effective_date: '2026-06-02', action_kind: 'ex_dividend', prior_close: '100.00', reference_price: '99.125', rights_dividend_value: '0.875' },
        ] }, capital_reduction: { status: 'error', reason: 'timeout', data: [] },
      }, 'partial', 'one_corporate_action_source_failed'),
    },
  } as any)
  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await screen.findByText('2026-06-02 · 除權')
  expect(screen.getByText('2026-06-02 · 除息')).toBeTruthy()
  expect(screen.getByText('98.765')).toBeTruthy()
  expect(screen.getByText('-1.235')).toBeTruthy()
  expect(screen.getByText(/除權息：可用 · 減資：讀取失敗/)).toBeTruthy()
  expect(screen.getByText('來源讀取逾時。')).toBeTruthy()
  expect(screen.getByText(/TWD／股 · 覆蓋未知/)).toBeTruthy()
  expect(screen.getByText(/權息合併調整值不是現金股利/)).toBeTruthy()
})

it('shows venue-matched raw-price comparison and partial source evidence', async () => {
  const empty = block(null, 'unknown', 'coverage_not_returned')
  vi.mocked(researchApi.taiwan).mockResolvedValue({
    instrument_id: 'TWSE:2330',
    instrument: { venue: 'TWSE', symbol: '2330', security_type: 'EQUITY', is_active: true, name: '台積電' },
    selectors: { start_date: '2026-09-01', end_date: '2026-10-06' },
    blocks: {
      ...supplementalBlocks(), valuation: empty, institutional_flows: empty,
      company_profile: empty, monthly_revenues: empty,
      benchmark_comparison: block({
        instrument_id: 'TWSE:2330', benchmark_id: 'TAIEX', venue: 'TWSE',
        common_observation_dates: ['2026-09-01', '2026-10-06'],
        observations: [{ trade_date: '2026-10-06', stock_close: '1938.75', benchmark_close: '45631.2',
          stock_coverage: { dataset: 'twse_daily_price', status: 'AVAILABLE', record_count: 1050, acquired_at: '2026-10-07T08:00:00Z', checksum: 'stock-checksum' },
          benchmark_captured_at: '2026-10-07T09:00:00Z', benchmark_revision: 1, benchmark_capture_id: 'captured-receipt',
          benchmark_source_url: 'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?date=20261001&response=json',
          benchmark_source_contract: 'TWSE daily OHLC', benchmark_request_scope: '2026-10', benchmark_payload_sha256: 'benchmark-checksum' }],
        comparison: {
          calculation_start_date: '2026-09-01', calculation_end_date: '2026-10-06', observation_count: 24,
          stock_return_pct: '5.9426229508', benchmark_return_pct: '6.1212105463',
          relative_return_percentage_points: '-0.1785875955', basis: 'raw_price_return',
        },
      }, 'available', 'raw_price_returns_on_common_observation_dates', {
        stock_source: { provider: 'TWSE', returned_count: 669, partial: true, requested_period_returned_count: 24 },
        benchmark_source: { provider: 'TWSE', returned_count: 24, partial: true, truncated: false,
          bar_receipts: [{ trade_date: '2026-10-06', revision: 1, capture_id: 'captured-receipt' }] },
        freshness: { frequency: 'daily', data_period: '2026-10-06', data_period_age_days: 2, coverage: { calendar_assessed: false } },
      }),
    },
    limitations: { financial_statements: { status: 'limited_scope', message: 'TWSE industry-24 retained reports.' } },
  } as any)
  render(<TaiwanResearchPanel symbol="TWSE:2330" market="TW" open />)
  await screen.findByText('個股相對大盤')
  expect(screen.getByText('比較個股與 TAIEX 的原始價格報酬 · 不含股利 · 非即時行情')).toBeTruthy()
  expect(screen.getByText('個股原始價格報酬')).toBeTruthy()
  expect(screen.getByText('-0.18 個百分點')).toBeTruthy()
  expect(screen.getByText(/擷取識別碼：captured-receipt/)).toBeTruthy()
  expect(screen.getByText('2026-10-06 逐日來源證據')).toBeTruthy()
  expect(screen.getByText(/個股採集時間：2026-10-07T08:00:00Z/)).toBeTruthy()
  expect(screen.getByText(/指數來源：https:\/\/www.twse.com.tw/)).toBeTruthy()
  expect(screen.getByText('採集與擷取時間不代表來源發布時間。')).toBeTruthy()
  expect(screen.getAllByText(/來源回傳部分資料/).length).toBeGreaterThan(0)
  expect(screen.getByText(/只比較兩邊都有資料的共同觀察日/)).toBeTruthy()
})
