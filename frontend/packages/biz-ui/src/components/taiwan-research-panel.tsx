import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { researchApi, type ResearchBlockName, type ResearchDataBlock, type ResearchFreshness, type TaiwanFinancialPeriodIndexResult, type TaiwanResearchPayload } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { BrokerFlowPanel } from './broker-flow-panel'
import { FinancialStatementsPanel, defaultFiscalScope, taipeiTodayParts } from './financial-statements-panel'

type AnyBlock = ResearchDataBlock<Record<string, any>>

const RESEARCH_BLOCK_NAMES: ResearchBlockName[] = [
  'valuation', 'institutional_flows', 'company_profile', 'monthly_revenues', 'margin_short_sale',
  'shareholder_distribution', 'broker_flow', 'financial_statements', 'corporate_actions', 'benchmark_comparison',
]
const MAIN_RESEARCH_BLOCK_NAMES = RESEARCH_BLOCK_NAMES.filter((name) => name !== 'financial_statements')

const BLOCK_SELECTOR_FIELDS: Record<ResearchBlockName, Array<keyof TaiwanResearchPayload['selectors']>> = {
  valuation: ['start_date', 'end_date'],
  institutional_flows: ['start_date', 'end_date'],
  company_profile: [],
  monthly_revenues: ['start_month', 'end_month'],
  margin_short_sale: ['start_date', 'end_date'],
  shareholder_distribution: ['end_date'],
  broker_flow: ['start_date', 'end_date'],
  financial_statements: ['fiscal_year', 'fiscal_quarter', 'statement'],
  corporate_actions: ['start_date', 'end_date'],
  benchmark_comparison: ['start_date', 'end_date'],
}

function providerScopes(payload: TaiwanResearchPayload): Set<string> {
  return new Set(Object.values(payload.blocks)
    .map((block) => String(block?.evidence?.provider_scope || ''))
    .filter(Boolean))
}

function retainSelectorCompatibleBlocks(
  payload: TaiwanResearchPayload,
  selectors: TaiwanResearchPayload['selectors'],
): TaiwanResearchPayload {
  const blocks: TaiwanResearchPayload['blocks'] = {}
  for (const name of Object.keys(payload.blocks) as ResearchBlockName[]) {
    const changed = BLOCK_SELECTOR_FIELDS[name].some((field) => payload.selectors?.[field] !== selectors[field])
    if (!changed) blocks[name] = payload.blocks[name] as never
  }
  return { ...payload, blocks, requested_blocks: Object.keys(blocks) as ResearchBlockName[] }
}

function mergeResearchPayload(
  previous: TaiwanResearchPayload | null,
  incoming: TaiwanResearchPayload,
): TaiwanResearchPayload {
  const receivedBlocks = incoming.blocks || {}
  const incomingNames = new Set<ResearchBlockName>(
    incoming.requested_blocks || Object.keys(receivedBlocks) as ResearchBlockName[],
  )
  let canMerge = previous?.instrument_id === incoming.instrument_id
  if (canMerge && previous) {
    const previousScopes = providerScopes(previous)
    const nextScopes = providerScopes(incoming)
    if ((previousScopes.size || nextScopes.size) && (
      previousScopes.size !== nextScopes.size
      || [...previousScopes].some((scope) => !nextScopes.has(scope))
    )) canMerge = false
  }

  const mergedBlocks: TaiwanResearchPayload['blocks'] = {}
  if (canMerge && previous) {
    for (const name of Object.keys(previous.blocks) as ResearchBlockName[]) {
      if (incomingNames.has(name)) continue
      const fields = BLOCK_SELECTOR_FIELDS[name]
      const scopeChanged = fields.some((field) => previous.selectors?.[field] !== incoming.selectors?.[field])
      if (!scopeChanged) mergedBlocks[name] = previous.blocks[name] as never
    }
  }
  Object.assign(mergedBlocks, receivedBlocks)
  return {
    ...incoming,
    requested_blocks: Object.keys(mergedBlocks) as ResearchBlockName[],
    blocks: mergedBlocks,
  }
}

function expectedSelectors(year: number, quarter: number): TaiwanResearchPayload['selectors'] {
  const { year: todayYear, month: todayMonth, day: todayDay } = taipeiTodayParts()
  const todayUtc = Date.UTC(todayYear, todayMonth - 1, todayDay)
  const isoDate = (offsetDays: number) => new Date(todayUtc + offsetDays * 86_400_000).toISOString().slice(0, 10)
  const endMonthDate = new Date(Date.UTC(todayYear, todayMonth - 2, 1))
  const monthStartDate = new Date(Date.UTC(endMonthDate.getUTCFullYear(), endMonthDate.getUTCMonth() - 11, 1))
  const monthLabel = (value: Date) => `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, '0')}`
  return {
    start_date: isoDate(-30),
    end_date: isoDate(-1),
    start_month: monthLabel(monthStartDate),
    end_month: monthLabel(endMonthDate),
    fiscal_year: year,
    fiscal_quarter: quarter,
    statement: null,
  }
}

const STATUS_LABELS: Record<string, string> = {
  available: '可用',
  partial: '部分可用',
  missing: '缺少資料',
  absent: '未包含此標的',
  empty: '來源回報無資料',
  unsupported: '不適用',
  stale: '資料較舊',
  error: '讀取失敗',
  unknown: '狀態未知',
  unavailable: '目前不可比較',
}

function statusLabel(status?: string): string {
  return STATUS_LABELS[status || ''] || status || '狀態未知'
}

function sourceLabel(block: AnyBlock): string {
  const evidence = block.evidence || {}
  if (evidence.endpoint === '/api/v1/shareholder-distribution') return 'TDCC 集保來源'
  const identity = String(evidence.instrument_id || (block.data as any)?.instrument_id || '')
  const contract = String(evidence.source_contract || '').toLowerCase()
  if (identity.startsWith('TPEX:') || contract.startsWith('tpex')) return 'TPEx 官方來源'
  if (identity.startsWith('TWSE:') || contract.startsWith('twse') || contract.startsWith('mops')) return 'TWSE 官方來源'
  return 'TWMD 官方來源'
}

function sourceUnitLabel(raw: unknown): string {
  const value = String(raw || '')
  if (value.startsWith('TWD thousands')) return 'TWD 千元（依來源資料推定）'
  return value || '來源未提供單位'
}

function contractSourceLabel(contract: string): string {
  const normalized = contract.toLowerCase()
  if (normalized.startsWith('tpex')) return 'TPEx 官方來源'
  if (normalized.startsWith('twse') || normalized.startsWith('mops')) return 'TWSE 官方來源'
  return contract
}

function missingText(block: AnyBlock, securityType?: string): string {
  if (securityType === 'ETF' && block.status === 'unsupported') {
    return 'ETF 不發布這類發行公司月營收或公司 profile 資料。'
  }
  const reasons: Record<string, string> = {
    coverage_missing: '來源沒有這段期間的資料。',
    coverage_not_returned: '來源沒有回報此區間的覆蓋狀態。',
    monthly_revenue_coverage_missing: '來源沒有回報月營收覆蓋資料。',
    profile_snapshot_not_retained: '來源尚未保留可用的公司資料快照。',
    unsupported_etf: 'ETF 不適用發行公司月營收資料。',
    unsupported_valuation_selector: 'TPEx 估值來源目前只支援四位數證券代碼。',
    tdcc_contract_is_twse_four_digit_only: '集保持股分布目前只支援四位數上市標的。',
    issuer_absent_from_available_report: '來源報告可用，但未包含此標的。',
    issuer_absent_from_latest_snapshot: '最新公司資料快照未列入此標的。',
    issuer_absent_from_latest_snapshot_retained_profile: '來源保留了公司資料，但最新快照未列入此標的。',
    issuer_absent_from_captured_reports: '來源報告沒有列入此標的。',
    selected_presence_unreported: '來源未回報此標的是否列於報告。',
    some_requested_dates_missing_or_absent: '部分所選日期沒有資料或未列入來源報告；尚未核對交易日曆。',
    some_requested_dates_missing: '部分所選日期沒有來源資料；尚未核對交易日曆。',
    some_requested_months_missing: '部分月份沒有來源資料。',
    some_requested_months_absent: '部分月份未列於來源報告。',
    some_requested_months_missing_or_absent: '部分月份缺少來源資料或未列入報告。',
    requested_months_missing: '所選月份沒有來源覆蓋。',
    retained_row_with_report_absence: '來源保留了歷史資料，但未列於當月擷取報告。',
    some_months_missing_or_absent: '部分月份缺少來源資料或未列入報告。',
    some_months_missing: '部分月份沒有來源資料。',
    instrument_not_found: '找不到此標的，請確認程式碼和交易所。',
    ambiguous_instrument: '此程式碼跨交易所重複，請指定 TWSE 或 TPEX。',
    instrument_inactive: '此標的目前未在來源目錄中標記為有效。',
    timeout: '來源讀取逾時。',
    concurrency_limit: '目前查詢量較高，請稍後重試。',
    http_503: '來源服務暫時不可用。',
    provider_error: '來源讀取失敗。',
    twse_only: '除權息與減資結果來源目前只涵蓋 TWSE。',
  }
  return reasons[block.reason] || '目前沒有可顯示的資料；可展開來源證據查看狀態。'
}

function blockHeader(title: string, block: AnyBlock) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h4 className="text-sm font-semibold">{title}</h4>
      <span className="rounded-full border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground">
        {statusLabel(block.status)}
      </span>
    </div>
  )
}

function ageLabel(seconds: unknown): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '未知'
  if (seconds < 60) return `${Math.floor(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分鐘`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小時`
  return `${(seconds / 86400).toFixed(1)} 日`
}

function coverageSummary(freshness: ResearchFreshness): string {
  const coverage = freshness?.coverage || {}
  if (coverage.month_presence_counts) {
    const labels: Record<string, string> = {
      present: '有列示',
      missing: '未取得覆蓋',
      not_in_captured_report: '報告未列此標的',
    }
    const counts = Object.entries(coverage.month_presence_counts as Record<string, number>)
      .map(([key, count]) => `${labels[key] || key} ${count} 月`)
    return `${coverage.requested_month_count ?? '所選'} 個月：${counts.join('、') || '未回報月份狀態'}`
  }
  if (coverage.reported_status_counts) {
    const labels: Record<string, string> = { AVAILABLE: '已取得', EMPTY: '來源回報無資料', MISSING: '未取得覆蓋' }
    const presenceLabels: Record<string, string> = { present: '有列示', absent: '未列示', missing: '覆蓋未知', empty: '來源回報無資料', unknown: '狀態未知' }
    const counts = Object.entries(coverage.reported_status_counts as Record<string, number>)
      .map(([key, count]) => `${labels[key] || statusLabel(key)} ${count} 日`)
    const presence = Object.entries(coverage.selected_presence_counts || {})
      .map(([key, count]) => `標的${presenceLabels[key] || statusLabel(key)} ${count} 日`)
    return [...counts, ...presence].join(' · ') || '來源未回報每日覆蓋'
  }
  if (coverage.latest_snapshot_presence || coverage.snapshot_coverage_status) {
    const snapshotLabels: Record<string, string> = { AVAILABLE: '已取得', MISSING: '未取得覆蓋' }
    const presenceLabels: Record<string, string> = { present: '有列示', absent: '未列示' }
    return `最新快照 ${snapshotLabels[String(coverage.snapshot_coverage_status)] || '未知'} · 標的${presenceLabels[String(coverage.latest_snapshot_presence)] || '狀態未知'}`
  }
  if (coverage.source_coverage_header) {
    const labels: Record<string, string> = { available: '已取得', empty: '來源回報無資料', missing: '未取得覆蓋' }
    const counts = String(coverage.source_coverage_header).split(';').flatMap((part) => {
      const match = /^(available|empty|missing)=(\d+)$/.exec(part.trim())
      return match ? [`${labels[match[1]]} ${match[2]} 日`] : []
    })
    if (counts.length) return counts.join(' · ')
  }
  return `區塊${statusLabel(String(coverage.block_status || 'unknown'))} · 來源未回報覆蓋細節`
}

function FreshnessSummary({ block }: { block: AnyBlock }) {
  const freshness = block.evidence?.freshness as ResearchFreshness | undefined
  if (!freshness) return null
  const age = typeof freshness.data_period_age_days === 'number' && Number.isFinite(freshness.data_period_age_days)
    ? `${freshness.data_period_age_days} 個日曆日` : '未知'
  const ageBasis = freshness.frequency === 'monthly' ? '月份結束距今' : '期別距今'
  return (
    <div className="rounded bg-muted/30 px-2 py-1.5 text-[10px] text-muted-foreground space-y-0.5">
      <div>所選資料中最新期別 {freshness.data_period || '未提供'} · 報表日 {freshness.report_date || '未提供'} · {ageBasis} {age}</div>
      <div>來源接收後 {ageLabel(freshness.source_receipt_age_seconds)} · 覆蓋：{coverageSummary(freshness)}</div>
      {freshness.latest_snapshot ? <div>最新快照報表日 {freshness.latest_snapshot.report_date || '未提供'} · 快照接收後 {ageLabel(freshness.latest_snapshot.source_receipt_age_seconds)}</div> : null}
      <div>{freshness.frequency_hint}</div>
    </div>
  )
}

function exactValue(value: unknown): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function EvidenceDetails({ block }: { block: AnyBlock }) {
  const evidence: any = block.evidence || {}
  const freshness: any = evidence.freshness || {}
  const provenance = [
    ...(evidence.per_period_provenance || []),
    ...(evidence.per_month_coverage || []),
    ...(evidence.per_month_presence_and_provenance || [])
      .map((row: any) => row.retained_row)
      .filter(Boolean),
    ...(evidence.retained_profile ? [evidence.retained_profile] : []),
    ...(evidence.latest_snapshot ? [evidence.latest_snapshot] : []),
    ...(evidence.benchmark_source?.bar_receipts || []),
  ] as any[]
  const received = [...new Set([
    evidence.source_received_at_utc,
    ...provenance.map((row) => row.received_at_utc || row.source_received_at_utc || row.captured_at),
  ].filter(Boolean).map(String))].sort()
  const revisions = [...new Set([
    evidence.revision,
    ...provenance.map((row) => row.revision),
  ].filter((value) => value !== undefined && value !== null).map(String))].sort()
  const captures = [...new Set([
    evidence.capture_id,
    ...provenance.map((row) => row.capture_id),
  ].filter(Boolean).map(String))].sort()
  return (
    <details className="pt-1 text-[10px] text-muted-foreground">
      <summary className="cursor-pointer">來源證據</summary>
      <div className="mt-1 space-y-1 break-all">
        <div>狀態原因：{block.reason || '未提供'}</div>
        <div>資料期別：{String(freshness.data_period || '未提供')}</div>
        <div>來源報表日：{String(freshness.report_date || '未提供')}</div>
        <div>來源發布時間：{String(freshness.publication_time || '未提供')}</div>
        <div>來源接收時間年齡：{ageLabel(freshness.source_receipt_age_seconds)}</div>
        <div>上游回應時間：{String(evidence.served_at || '未提供')}</div>
        <div>新鮮度評估時間：{String(freshness.evaluated_at_utc || '未提供')}</div>
        {freshness.latest_snapshot ? <div>最新整體快照接收時間：{String(freshness.latest_snapshot.source_received_at_utc || '未提供')}</div> : null}
        <div>來源契約：{Array.isArray(evidence.source_contract) ? evidence.source_contract.join(', ') : String(evidence.source_contract || '未提供')}</div>
        <div>端點：{String(evidence.endpoint || '未提供')}</div>
        <div>來源接收時間：{received.length ? received.join(', ') : '未提供'}</div>
        <div>修訂版本：{revisions.length ? revisions.join(', ') : '未提供'}</div>
        <div>擷取識別碼：{captures.length ? captures.join(', ') : '未提供'}</div>
        <div>內容雜湊：{String(evidence.payload_sha256 || '未提供')}</div>
        {(block.data as any)?.observations?.filter((row: any) => row.stock_coverage).map((row: any) => (
          <details key={row.trade_date} className="border-t border-border/40 pt-1">
            <summary className="cursor-pointer">{row.trade_date} 逐日來源證據</summary>
            <div>個股原始收盤：{row.stock_close} TWD／股 · 指數：{row.benchmark_close} 點</div>
            <div>個股覆蓋：{row.stock_coverage.dataset || '未提供'} · {row.stock_coverage.status} · 筆數 {row.stock_coverage.record_count ?? '未提供'}</div>
            <div>個股採集時間：{row.stock_coverage.acquired_at || '未提供'} · 雜湊：{row.stock_coverage.checksum || '未提供'}</div>
            <div>指數擷取時間：{row.benchmark_captured_at} · 修訂 {row.benchmark_revision} · 識別碼 {row.benchmark_capture_id}</div>
            <div>指數來源：{row.benchmark_source_url || '未提供'} · {row.benchmark_source_contract} · {row.benchmark_request_scope}</div>
            <div>指數內容雜湊：{row.benchmark_payload_sha256}</div>
            <div>採集與擷取時間不代表來源發布時間。</div>
          </details>
        ))}
      </div>
    </details>
  )
}

function ValuationBlock({ block }: { block: AnyBlock }) {
  const rows = [...((block.data as any)?.observations || [])].sort((a, b) => String(b.trade_date).localeCompare(String(a.trade_date)))
  const latest = rows[0]
  const sourceYear = latest?.dividend_reference_year
  const dividendYear = Number.isInteger(sourceYear) && sourceYear > 0
    ? (sourceYear < 1000 ? sourceYear + 1911 : sourceYear)
    : null
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('官方估值', block)}
      {latest ? <>
        <div className="text-[11px] text-muted-foreground">資料日 {latest.trade_date || '未提供'} · {sourceLabel(block)}</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <span>收盤價（TWD／股）</span><span className="text-right font-mono">{exactValue(latest.close_price)}</span>
          <span>本益比（倍，口徑未指定）</span><span className="text-right font-mono">{exactValue(latest.pe_ratio)}</span>
          <span>股價淨值比（倍）</span><span className="text-right font-mono">{exactValue(latest.pb_ratio)}</span>
          <span>官方殖利率（%，股利年度 {dividendYear ?? '未提供'}）</span><span className="text-right font-mono">{exactValue(latest.dividend_yield_pct)}</span>
        </div>
        <p className="text-[10px] text-muted-foreground">
          {dividendYear
            ? '依來源股利年度採計的官方數值；與最新季度配息年化估算的口徑不同。'
            : '來源未提供股利年度，無法確認採計期間；此處保留官方原值。'}
        </p>
      </> : <p className="text-xs text-muted-foreground">{missingText(block)}</p>}
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
    </section>
  )
}

function FlowBlock({ block }: { block: AnyBlock }) {
  const rows = [...((block.data as any)?.observations || [])].sort((a, b) => String(b.trade_date).localeCompare(String(a.trade_date)))
  const latest = rows[0]
  const values = latest?.native_values || {}
  const preferred = [
    ['foreign_non_dealer_net_shares', '外資（不含自營商）淨股數'],
    ['foreign_ex_dealer_net_shares', '外資（不含自營商）淨股數'],
    ['investment_trust_net_shares', '投信淨股數'],
    ['dealer_reported_net_shares', '自營商淨股數'],
    ['combined_dealer_net_shares', '自營商合計淨股數'],
    ['total_institutional_net_shares', '三大法人合計淨股數'],
  ] as const
  const entries = preferred.filter(([key]) => key in values)
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('三大法人', block)}
      {latest ? <>
        <div className="text-[11px] text-muted-foreground">交易日 {latest.trade_date || '未提供'} · {sourceLabel(block)} · 股</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {entries.map(([key, label]) => <div key={key} className="contents"><span>{label}</span><span className="text-right font-mono">{exactValue(values[key])}</span></div>)}
        </div>
      </> : <p className="text-xs text-muted-foreground">{missingText(block)}</p>}
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
    </section>
  )
}

function ProfileBlock({ block, securityType }: { block: AnyBlock; securityType?: string }) {
  const data = (block.data || {}) as any
  const profile = data.profile
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('公司資料', block)}
      {profile ? <>
        <div className="text-[11px] text-muted-foreground">資料日期 {profile.report_date || '未提供'} · {sourceLabel(block)}</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <span>公司名稱</span><span className="text-right">{exactValue(profile.company_name)}</span>
          <span>產業</span><span className="text-right">{exactValue(profile.industry_code)}</span>
          <span>實收資本額（{data.units?.paid_in_capital || '單位未提供'}）</span><span className="text-right font-mono">{exactValue(profile.paid_in_capital)}</span>
          <span>已發行股數（股）</span><span className="text-right font-mono">{exactValue(profile.issued_share_count)}</span>
        </div>
      </> : <p className="text-xs text-muted-foreground">{missingText(block, securityType)}</p>}
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
    </section>
  )
}

function RevenueBlock({ block, securityType }: { block: AnyBlock; securityType?: string }) {
  const data = (block.data || {}) as any
  const months = data.months || []
  const selectors: any = block.evidence?.selectors || {}
  const revenueUnit = sourceUnitLabel(data.units?.revenue)
  const perMonthCoverage: any[] = (block.evidence?.per_month_coverage as any[]) || []
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('月營收', block)}
      {months.length ? <>
        <div className="text-[11px] text-muted-foreground" title={String(data.units?.revenue || '')}>來源期間 {selectors.start_month || '未提供'} 至 {selectors.end_month || '未提供'} · {sourceLabel(block)} · {revenueUnit}</div>
        <div className="space-y-1">
          {[...months].reverse().slice(0, 12).map((item: any) => (
            <div key={item.data_month} className="space-y-1 rounded border border-border/30 p-2 text-xs">
              <div className="flex justify-between gap-2">
                <span>{item.data_month?.slice(0, 7)}</span>
                <span className="text-muted-foreground">{revenuePresenceLabel(item)}</span>
              </div>
              <div className="text-[10px] text-muted-foreground">
                {monthProvenance(item, perMonthCoverage)}
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                <span>月營收</span><span className="text-right font-mono">{exactValue(item.row?.monthly_revenue)}</span>
                <span>月增率（%）</span><span className="text-right font-mono">{exactValue(item.row?.month_over_month_pct)}</span>
                <span>年增率（%）</span><span className="text-right font-mono">{exactValue(item.row?.year_over_year_pct)}</span>
                <span>累計營收</span><span className="text-right font-mono">{exactValue(item.row?.cumulative_revenue)}</span>
                <span>累計年增率（%）</span><span className="text-right font-mono">{exactValue(item.row?.cumulative_yoy_pct)}</span>
              </div>
            </div>
          ))}
        </div>
      </> : <p className="text-xs text-muted-foreground">{missingText(block, securityType)}</p>}
      {securityType === 'ETF' && block.status === 'unsupported'
        ? <p className="text-xs text-muted-foreground">ETF 不發布這類發行公司月營收資料。</p>
        : null}
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
      <p className="text-[10px] text-muted-foreground">月營收是月度公告資料，不是季度財報或完整損益表。</p>
    </section>
  )
}

function CorporateActionsBlock({ block = { data: null, status: 'unknown', reason: 'coverage_not_returned', evidence: {} } }: { block?: AnyBlock }) {
  const data = (block.data || {}) as any
  const exRight = data.ex_right_dividend || {}
  const reductions = data.capital_reduction || {}
  const exRows: any[] = exRight.data || []
  const reductionRows: any[] = reductions.data || []
  const events = [
    ...exRows.map((row) => ({ ...row, dataset: 'TWT49U', eventDate: row.effective_date, kind: row.action_kind })),
    ...reductionRows.map((row) => ({ ...row, dataset: 'TWTAUU', eventDate: row.recovery_date, kind: row.reduction_reason })),
  ].sort((left, right) => left.eventDate.localeCompare(right.eventDate) || left.dataset.localeCompare(right.dataset) || left.kind.localeCompare(right.kind))
  const labels: Record<string, string> = {
    ex_right: '除權', ex_dividend: '除息', ex_right_dividend: '除權息',
    loss_offset: '減資彌補虧損', return_of_capital: '減資退還股款',
  }
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('除權息與減資結果', block)}
      <p className="text-[11px] text-muted-foreground">TWSE 已實現計算結果 · 價格單位 TWD／股 · 覆蓋未知。</p>
      <p className="text-[11px] text-muted-foreground">除權息：{statusLabel(exRight.status)} · 減資：{statusLabel(reductions.status)}</p>
      {[exRight, reductions].filter((part) => part.status === 'error').map((part, index) => <p key={index} className="text-[11px] text-destructive">{missingText(part)}</p>)}
      {events.length ? <div className="space-y-1">
        {events.map((row: any, index: number) => <div key={`${row.dataset}:${row.eventDate}:${row.kind}:${index}`} className="rounded border border-border/30 p-2 text-xs">
          <div className="flex justify-between gap-2"><span>{row.eventDate} · {labels[row.kind] || row.kind}</span><span className="text-muted-foreground">{row.dataset} · {row.instrument_id}</span></div>
          {row.dataset === 'TWT49U' ? <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
            <span>原收盤</span><span className="text-right font-mono break-all">{exactValue(row.prior_close)}</span>
            <span>參考價</span><span className="text-right font-mono break-all">{exactValue(row.reference_price)}</span>
            <span>權息合併調整值</span><span className="text-right font-mono break-all">{exactValue(row.rights_dividend_value)}</span>
          </div> : <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
            <span>停牌前收盤</span><span className="text-right font-mono break-all">{exactValue(row.pre_suspension_close)}</span>
            <span>恢復參考價</span><span className="text-right font-mono break-all">{exactValue(row.recovery_reference_price)}</span>
            <span>除權參考價</span><span className="text-right font-mono break-all">{exactValue(row.ex_right_reference_price)}</span>
          </div>}
        </div>)}
      </div> : <p className="text-xs text-muted-foreground">{block.status === 'unsupported' ? missingText(block) : '目前沒有回傳已知事件列；來源覆蓋未知，不能據此判定沒有公司行動。'}</p>}
      <p className="text-[10px] text-muted-foreground">參考價與原收盤是價格欄位，權息合併調整值不是現金股利。列表不提供完整覆蓋、接收時間或修訂證據；公告與付款時間未知，原始日線未作回溯調整。</p>
      <EvidenceDetails block={block} />
    </section>
  )
}

function BenchmarkComparisonBlock({ block = { data: null, status: 'unknown', reason: 'coverage_not_returned', evidence: {} } }: { block?: AnyBlock }) {
  const data = (block.data || {}) as any
  const comparison = data.comparison
  const stockSource = block.evidence?.stock_source || {}
  const benchmarkSource = block.evidence?.benchmark_source || {}
  const percent = (value: unknown) => {
    const numeric = Number(value)
    return value == null || !Number.isFinite(numeric) ? '—' : `${numeric > 0 ? '+' : ''}${numeric.toFixed(2)}%`
  }
  const points = (value: unknown) => {
    const numeric = Number(value)
    return value == null || !Number.isFinite(numeric) ? '—' : `${numeric > 0 ? '+' : ''}${numeric.toFixed(2)} 個百分點`
  }
  const sourceReadLabel = (source: any) => {
    const partial = source?.partial === true || source?.truncated === true ? '來源回傳部分資料' : '來源未標記部分資料'
    return `${source?.provider || '來源未知'} · ${source?.returned_count ?? '—'} 筆 · ${partial}`
  }
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('個股相對大盤', block)}
      <p className="text-[11px] text-muted-foreground">比較個股與 {data.benchmark_id || '所屬市場基準'} 的原始價格報酬 · 不含股利 · 非即時行情</p>
      {comparison ? <>
        <div className="text-[11px] text-muted-foreground">共同觀察日 {comparison.calculation_start_date} 至 {comparison.calculation_end_date} · {comparison.observation_count} 日</div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <span>個股原始價格報酬</span><span className="text-right font-mono">{percent(comparison.stock_return_pct)}</span>
          <span>{data.benchmark_id} 指數報酬</span><span className="text-right font-mono">{percent(comparison.benchmark_return_pct)}</span>
          <span>個股相對報酬</span><span className="text-right font-mono">{points(comparison.relative_return_percentage_points)}</span>
        </div>
      </> : <p className="text-xs text-muted-foreground">{block.reason === 'benchmark_no_bars' ? '所選大盤沒有已留存指數日線；不會建立假點或報酬。' : block.reason === 'insufficient_common_observation_dates' ? '共同觀察日期不足兩日，暫不計算報酬。' : block.reason === 'stock_no_observed_closes_in_range' ? '所選期間沒有可用的個股原始收盤。' : '目前無法計算相對表現；個股其他研究資料仍可使用。'}</p>}
      <div className="space-y-1 text-[10px] text-muted-foreground">
        <div>個股日線：{sourceReadLabel(stockSource)} · 依所選日期篩選近期回傳資料</div>
        <div>大盤日線：{sourceReadLabel(benchmarkSource)}</div>
        <div>只比較兩邊都有資料的共同觀察日；缺少日期不代表休市或採集失敗。</div>
      </div>
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
    </section>
  )
}

function MarginBlock({ block }: { block: AnyBlock }) {
  const data = (block.data || {}) as any
  const latest = data.latest
  const previous = data.previous
  const changes = data.changes || {}
  const values: Array<[string, string]> = [
    ['margin_balance_previous', '融資前日餘額'],
    ['margin_purchase', '融資買進'],
    ['margin_sale', '融資賣出'],
    ['margin_cash_redemption', '融資現金償還'],
    ['margin_balance', '融資今日餘額'],
    ['margin_quota', '融資次一營業日限額'],
    ['short_sale_balance_previous', '融券前日餘額'],
    ['short_sale', '融券賣出'],
    ['short_cover', '融券買進／券買'],
    ['short_stock_redemption', '現券償還'],
    ['short_sale_balance', '融券今日餘額'],
    ['short_sale_quota', '融券次一營業日限額'],
    ['offsetting', '資券互抵'],
  ]
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('融資融券', block)}
      {latest ? <>
        <div className="text-[11px] text-muted-foreground">交易日 {latest.trade_date} · {sourceLabel(block)} · {data.native_unit || 'trading_units'}</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {values.map(([key, label]) => <div key={key} className="contents"><span>{label}</span><span className="text-right font-mono">{exactValue(latest[key])}</span></div>)}
          <span>融資餘額變化</span><span className="text-right font-mono">{exactValue(changes.margin_balance)}</span>
          <span>融券餘額變化</span><span className="text-right font-mono">{exactValue(changes.short_sale_balance)}</span>
          <span>融資使用率（%）</span><span className="text-right font-mono">{exactValue(latest.margin_utilization_rate)}</span>
          <span>融券使用率（%）</span><span className="text-right font-mono">{exactValue(latest.short_sale_utilization_rate)}</span>
        </div>
        {previous ? <div className="text-[10px] text-muted-foreground">比較列 {previous.trade_date}；數量維持交易單位，未換算股數或金額。</div>
          : <div className="text-[10px] text-muted-foreground">尚無可比較的前一筆資料。</div>}
        {latest.note ? <div className="text-[10px] text-muted-foreground">來源註記：{latest.note}</div> : null}
      </> : <p className="text-xs text-muted-foreground">{missingText(block)}</p>}
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
    </section>
  )
}

function ShareholderDistributionBlock({ block }: { block: AnyBlock }) {
  const data = (block.data || {}) as any
  const latest = data.latest
  const previous = data.previous
  const large = latest?.large_holding
  const buckets = latest?.buckets || []
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      {blockHeader('集保持股分布', block)}
      {latest ? <>
        <div className="text-[11px] text-muted-foreground">報表日 {latest.report_date} · {latest.report_variant} · {sourceLabel(block)} · 股數為股</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <span>集保總股數（官方總計）</span><span className="text-right font-mono">{exactValue(latest.total_share_count)}</span>
          <span>保管帳戶總數</span><span className="text-right font-mono">{exactValue(latest.total_holder_accounts)}</span>
          <span>大額分級股數（&gt;400,000 股）</span><span className="text-right font-mono">{exactValue(large?.share_count)}</span>
          <span>占官方總股數（%）</span><span className="text-right font-mono break-all">{exactValue(large?.percentage_of_official_total)}</span>
        </div>
        <div className="space-y-1 rounded border border-border/30 p-2 text-[11px]">
          <div className="font-medium">門檻分級（來源級別 12–15）</div>
          {buckets.filter((row: any) => [12, 13, 14, 15].includes(row.source_level)).map((row: any) => (
            <div key={row.source_level} className="flex justify-between gap-2">
              <span>{row.source_tier_label || `第 ${row.source_level} 級`} · {exactValue(row.holder_count)} 個帳戶</span>
              <span className="font-mono">{exactValue(row.share_count)} 股</span>
            </div>
          ))}
        </div>
        {previous ? <div className="text-[10px] text-muted-foreground">
          同來源變體前週 {previous.report_date} · 大額股數變化 {exactValue(data.changes?.large_holding_share_count)} 股 · 占比變化 {exactValue(data.changes?.large_holding_percentage_points)} 個百分點
        </div> : <div className="text-[10px] text-muted-foreground">{data.comparison_reason === 'previous_week_unavailable_or_variant_changed' ? '同一來源變體的前一週資料缺少，未計算變化。' : '尚無可比較的前一週資料。'}</div>}
        <p className="text-[10px] text-muted-foreground">大額占比＝第 12–15 級股數 ÷ 官方總計股數；保管帳戶分布不代表實際股東或投資人身分。</p>
        {latest.adjustment ? <p className="text-[10px] text-muted-foreground">調整股數 {exactValue(latest.adjustment.share_count)}；官方總計已扣除調整列，大額分級股數保留來源原值。</p> : null}
      </> : <p className="text-xs text-muted-foreground">{missingText(block)}</p>}
      <FreshnessSummary block={block} />
      <EvidenceDetails block={block} />
    </section>
  )
}

function revenuePresenceLabel(item: any): string {
  if (item.presence === 'present') return '來源報告有列示'
  if (item.presence === 'missing') return '來源沒有此月份覆蓋'
  if (item.row) return '有留存歷史列，未列於此月擷取報告'
  return '未列於擷取報告'
}

function monthProvenance(item: any, coverage: any[]): string {
  const reports = coverage.filter((entry) => entry.data_month === item.data_month)
  const sources = [...new Set([
    item.row?.source_contract,
    ...reports.map((entry) => entry.source_contract),
  ].filter(Boolean).map((contract) => contractSourceLabel(String(contract))))]
  const dates = [...new Set([
    item.row?.report_date,
    ...reports.map((entry) => entry.report_date),
  ].filter(Boolean))]
  return `來源 ${sources.length ? sources.join(', ') : '未提供'} · 報表日 ${dates.length ? dates.join(', ') : '未提供'}`
}

export function TaiwanResearchPanel({ symbol, market, open }: { symbol: string; market: string; open: boolean }) {
  const [payload, setPayload] = useState<TaiwanResearchPayload | null>(null)
  const [mainLoading, setMainLoading] = useState(false)
  const [mainError, setMainError] = useState('')
  const [financialBlock, setFinancialBlock] = useState<AnyBlock | null>(null)
  const [financialLoading, setFinancialLoading] = useState(false)
  const [financialError, setFinancialError] = useState('')
  const [financialUnsupported, setFinancialUnsupported] = useState<{ symbolScope: string; reason: string } | null>(null)
  const [financialPeriodIndex, setFinancialPeriodIndex] = useState<TaiwanFinancialPeriodIndexResult | null>(null)
  const [financialIndexLoading, setFinancialIndexLoading] = useState(false)
  const [financialIndexError, setFinancialIndexError] = useState('')
  const [financialSelectionMode, setFinancialSelectionMode] = useState<'latest' | 'manual'>('latest')
  const [fiscalScope, setFiscalScope] = useState(defaultFiscalScope)
  const mainRequestSequence = useRef(0)
  const financialRequestSequence = useRef(0)
  const financialIndexRequestSequence = useRef(0)
  const payloadRef = useRef<TaiwanResearchPayload | null>(null)
  const payloadInputRef = useRef<string | null>(null)
  const financialBlockRef = useRef<AnyBlock | null>(null)
  const financialInputScopeRef = useRef<string | null>(null)
  const financialIndexInputScopeRef = useRef<string | null>(null)
  const mainControllerRef = useRef<AbortController | null>(null)
  const financialControllerRef = useRef<AbortController | null>(null)
  const financialIndexControllerRef = useRef<AbortController | null>(null)
  const financialSelectionModeRef = useRef<'latest' | 'manual'>('latest')
  const loadFinancialRef = useRef<((scope: { year: number; quarter: number }, indexResult?: TaiwanFinancialPeriodIndexResult | null) => Promise<void>) | null>(null)
  const inputScope = `${market}:${symbol}`

  const loadMain = useCallback(async (reason: 'auto' | 'refresh' = 'auto') => {
    if (!symbol || market !== 'TW') return
    const requestScope = `${market}:${symbol}`
    const sequence = ++mainRequestSequence.current
    let currentPayload = payloadInputRef.current === requestScope ? payloadRef.current : null
    if (!currentPayload) {
      payloadRef.current = null
      payloadInputRef.current = requestScope
      setPayload(null)
    }
    mainControllerRef.current?.abort()
    const controller = new AbortController()
    mainControllerRef.current = controller
    const defaultScope = defaultFiscalScope()
    const targetSelectors = expectedSelectors(defaultScope.year, defaultScope.quarter)
    if (currentPayload) {
      currentPayload = retainSelectorCompatibleBlocks(currentPayload, targetSelectors)
      payloadRef.current = currentPayload
      setPayload(currentPayload)
    }
    let requestedBlocks: ResearchBlockName[] | undefined
    if (currentPayload) {
      requestedBlocks = MAIN_RESEARCH_BLOCK_NAMES.filter((name) => {
        const oldBlock = currentPayload.blocks[name]
        const failed = oldBlock?.status === 'error'
        const selectorsChanged = BLOCK_SELECTOR_FIELDS[name].some((field) => (
          currentPayload.selectors?.[field] !== targetSelectors[field]
        ))
        return !oldBlock || failed || selectorsChanged
      })
      if (reason === 'refresh' && requestedBlocks.length === 0) {
        requestedBlocks = [...MAIN_RESEARCH_BLOCK_NAMES]
      }
      if (requestedBlocks.length === 0) {
        setMainLoading(false)
        return
      }
    }
    setMainLoading(true)
    setMainError('')
    try {
      const result = await researchApi.taiwan(symbol, {
        blocks: requestedBlocks || [...MAIN_RESEARCH_BLOCK_NAMES],
      }, { signal: controller.signal })
      if (sequence === mainRequestSequence.current) {
        const merged = mergeResearchPayload(currentPayload, result)
        payloadRef.current = merged
        payloadInputRef.current = requestScope
        setPayload(merged)
      }
    } catch (cause) {
      if (sequence === mainRequestSequence.current && !controller.signal.aborted) {
        setMainError(cause instanceof Error ? cause.message : '研究資料載入失敗。')
      }
    } finally {
      if (sequence === mainRequestSequence.current) setMainLoading(false)
    }
  }, [market, symbol])

  useEffect(() => {
    if (open && market === 'TW') void loadMain('auto')
    else {
      mainRequestSequence.current++
      mainControllerRef.current?.abort()
      mainControllerRef.current = null
      payloadRef.current = null
      payloadInputRef.current = null
      setPayload(null)
      setMainError('')
      setMainLoading(false)
    }
    return () => {
      mainRequestSequence.current++
      mainControllerRef.current?.abort()
    }
  }, [open, market, loadMain])

  const loadFinancial = useCallback(async (
    scope: { year: number; quarter: number },
    indexResult: TaiwanFinancialPeriodIndexResult | null = financialPeriodIndex,
  ) => {
    if (!symbol || market !== 'TW') return
    const symbolScope = `${market}:${symbol}`
    const requestScope = `${symbolScope}:${scope.year}:${scope.quarter}`
    const sequence = ++financialRequestSequence.current
    financialControllerRef.current?.abort()
    financialControllerRef.current = null

    const previousRequestScope = financialInputScopeRef.current
    financialInputScopeRef.current = requestScope
    setFinancialError('')
    if (!financialBlockRef.current || previousRequestScope !== requestScope) {
      financialBlockRef.current = null
      setFinancialBlock(null)
    }

    if (symbol.toUpperCase().startsWith('TPEX:')) {
      setFinancialUnsupported({ symbolScope, reason: 'financial_statements_twse_only' })
      setFinancialLoading(false)
      return
    }

    const unsupportedIndex = indexResult?.index_status === 'unsupported'
      || indexResult?.index?.qualification.status === 'unsupported'
    if (unsupportedIndex) {
      setFinancialBlock(null)
      setFinancialUnsupported({
        symbolScope,
        reason: indexResult?.index?.qualification.reason || indexResult?.reason || 'unsupported',
      })
      setFinancialLoading(false)
      return
    }

    setFinancialUnsupported(null)
    setFinancialLoading(true)
    const controller = new AbortController()
    financialControllerRef.current = controller
    try {
      const result = await researchApi.taiwan(symbol, {
        fiscal_year: scope.year,
        fiscal_quarter: scope.quarter,
        blocks: ['financial_statements'],
      }, { signal: controller.signal })
      if (sequence === financialRequestSequence.current) {
        const resultBlock = result.blocks?.financial_statements as AnyBlock | undefined
        if (!resultBlock) throw new Error('財報回應未包含所選期別。')
        const data = resultBlock.data as any
        const evidenceSelectors = resultBlock.evidence?.selectors as Record<string, unknown> | undefined
        const requestedSymbol = symbol.trim().toUpperCase()
        const identities = [result.instrument_id, data?.instrument_id, resultBlock.evidence?.instrument_id, evidenceSelectors?.instrument_id]
          .filter((value) => value != null)
        const instrumentMatches = identities.length > 0 && identities.every((value) => {
          const identity = String(value).toUpperCase()
          const canonicalParts = /^(TWSE|TPEX):(\d{4,6})$/.exec(identity)
          return Boolean(canonicalParts && (
            requestedSymbol.includes(':') ? identity === requestedSymbol : canonicalParts[2] === requestedSymbol
          ))
        })
        const years = [data?.fiscal_year, evidenceSelectors?.fiscal_year, result.selectors?.fiscal_year].filter((value) => value != null)
        const quarters = [data?.fiscal_quarter, evidenceSelectors?.fiscal_quarter, result.selectors?.fiscal_quarter].filter((value) => value != null)
        if (!instrumentMatches || !years.length || !quarters.length
          || !years.every((value) => Number(value) === scope.year)
          || !quarters.every((value) => Number(value) === scope.quarter)) {
          throw new Error('財報回應標的或期別與所選查詢不符。')
        }
        financialBlockRef.current = resultBlock
        setFinancialBlock(resultBlock)
      }
    } catch (cause) {
      if (sequence === financialRequestSequence.current && !controller.signal.aborted) {
        setFinancialError(cause instanceof Error ? cause.message : '財報讀取失敗。')
      }
    } finally {
      if (sequence === financialRequestSequence.current) setFinancialLoading(false)
    }
  }, [financialPeriodIndex, market, symbol])

  loadFinancialRef.current = loadFinancial

  const loadFinancialPeriodIndex = useCallback(async () => {
    if (!symbol || market !== 'TW') return
    const symbolScope = `${market}:${symbol}`
    const sequence = ++financialIndexRequestSequence.current
    financialIndexControllerRef.current?.abort()
    financialIndexControllerRef.current = null
    financialIndexInputScopeRef.current = symbolScope
    setFinancialIndexError('')
    setFinancialPeriodIndex(null)

    const previousFinancialScope = financialInputScopeRef.current
    if (previousFinancialScope && !previousFinancialScope.startsWith(`${symbolScope}:`)) {
      financialRequestSequence.current++
      financialControllerRef.current?.abort()
      financialControllerRef.current = null
      financialInputScopeRef.current = null
      financialBlockRef.current = null
      setFinancialBlock(null)
      setFinancialError('')
      setFinancialLoading(false)
    } else if (financialSelectionModeRef.current === 'latest') {
      financialRequestSequence.current++
      financialControllerRef.current?.abort()
      financialControllerRef.current = null
      financialInputScopeRef.current = null
      financialBlockRef.current = null
      setFinancialBlock(null)
      setFinancialError('')
      setFinancialLoading(false)
    }

    if (symbol.toUpperCase().startsWith('TPEX:')) {
      setFinancialUnsupported({ symbolScope, reason: 'financial_statements_twse_only' })
      setFinancialPeriodIndex(null)
      setFinancialIndexLoading(false)
      return
    }

    setFinancialUnsupported(null)
    setFinancialIndexLoading(true)
    const controller = new AbortController()
    financialIndexControllerRef.current = controller
    try {
      const result = await researchApi.financialPeriods(symbol, {
        report_scope: 'consolidated', limit: 40,
      }, { signal: controller.signal })
      if (sequence !== financialIndexRequestSequence.current) return
      const requestedSymbol = symbol.trim().toUpperCase()
      const identities = [result.instrument_id, result.selectors?.instrument_id, result.index?.instrument_id]
        .filter((value) => value != null)
      const instrumentMatches = identities.length > 0 && identities.every((value) => {
        const identity = String(value).toUpperCase()
        const canonicalParts = /^(TWSE|TPEX):([0-9A-Z]{4,6})$/.exec(identity)
        return Boolean(canonicalParts && (
          requestedSymbol.includes(':') ? identity === requestedSymbol : canonicalParts[2] === requestedSymbol
        ))
      })
      const index = result.index
      const indexMatches = !index || (
        index.contract_version === 'twmd.financial-statement-periods/v1'
        && index.instrument_id === result.instrument_id
        && index.report_scope === 'consolidated'
        && index.statement === null
        && index.limit === 40
        && index.source === 'mops_financial_statements'
        && result.selectors.report_scope === 'consolidated'
        && result.selectors.statement === null
        && result.selectors.limit === 40
      )
      if (!instrumentMatches || !indexMatches) throw new Error('期別索引標的或選擇條件與本次查詢不符。')
      setFinancialPeriodIndex(result)
      if (result.index_status === 'unsupported' || index?.qualification.status === 'unsupported') {
        setFinancialUnsupported({
          symbolScope,
          reason: index?.qualification.reason || result.reason || 'unsupported',
        })
      }
      const latest = result.index_status === 'available'
        && index?.coverage.status === 'complete'
        && index.qualification.status === 'qualified'
        && index.latest_readable_period?.presence === 'present_readable'
        && index.latest_readable_period.authority
        ? index.latest_readable_period
        : null
      if (financialSelectionModeRef.current === 'latest') {
        if (latest) {
          const latestScope = { year: latest.fiscal_year, quarter: latest.fiscal_quarter }
          setFiscalScope(latestScope)
          void loadFinancialRef.current?.(latestScope, result)
        } else {
          financialRequestSequence.current++
          financialControllerRef.current?.abort()
          financialControllerRef.current = null
          financialInputScopeRef.current = null
          financialBlockRef.current = null
          setFinancialBlock(null)
          setFinancialError('')
          setFinancialLoading(false)
        }
      }
    } catch (cause) {
      if (sequence === financialIndexRequestSequence.current && !controller.signal.aborted) {
        setFinancialPeriodIndex(null)
        setFinancialIndexError(cause instanceof Error ? cause.message : '留存期別索引讀取失敗。')
        if (financialSelectionModeRef.current === 'latest') {
          financialRequestSequence.current++
          financialControllerRef.current?.abort()
          financialControllerRef.current = null
          financialInputScopeRef.current = null
          financialBlockRef.current = null
          setFinancialBlock(null)
          setFinancialLoading(false)
        }
      }
    } finally {
      if (sequence === financialIndexRequestSequence.current) setFinancialIndexLoading(false)
    }
  }, [market, symbol])

  const changeFinancialSelectionMode = useCallback((mode: 'latest' | 'manual') => {
    financialSelectionModeRef.current = mode
    setFinancialSelectionMode(mode)
    if (mode === 'manual') {
      financialRequestSequence.current++
      financialControllerRef.current?.abort()
      financialControllerRef.current = null
      setFinancialLoading(false)
      return
    }
    const index = financialPeriodIndex?.index
    const latest = financialPeriodIndex?.index_status === 'available'
      && index?.coverage.status === 'complete'
      && index.qualification.status === 'qualified'
      && index.latest_readable_period?.presence === 'present_readable'
      && index.latest_readable_period.authority
      ? index.latest_readable_period
      : null
    if (latest) {
      const latestScope = { year: latest.fiscal_year, quarter: latest.fiscal_quarter }
      setFiscalScope(latestScope)
      void loadFinancialRef.current?.(latestScope, financialPeriodIndex)
    } else {
      financialRequestSequence.current++
      financialControllerRef.current?.abort()
      financialControllerRef.current = null
      financialInputScopeRef.current = null
      financialBlockRef.current = null
      setFinancialBlock(null)
      setFinancialError('')
      setFinancialLoading(false)
    }
  }, [financialPeriodIndex])

  const submitManualFinancialQuery = useCallback(() => {
    void loadFinancial(fiscalScope, financialPeriodIndex)
  }, [financialPeriodIndex, fiscalScope, loadFinancial])

  const retryFinancialQuery = useCallback(() => {
    void loadFinancial(fiscalScope, financialPeriodIndex)
  }, [financialPeriodIndex, fiscalScope, loadFinancial])

  useEffect(() => {
    if (open && market === 'TW') {
      financialSelectionModeRef.current = 'latest'
      setFinancialSelectionMode('latest')
      setFiscalScope(defaultFiscalScope())
      void loadFinancialPeriodIndex()
    }
    else {
      financialRequestSequence.current++
      financialControllerRef.current?.abort()
      financialControllerRef.current = null
      financialIndexRequestSequence.current++
      financialIndexControllerRef.current?.abort()
      financialIndexControllerRef.current = null
      financialBlockRef.current = null
      financialInputScopeRef.current = null
      financialIndexInputScopeRef.current = null
      setFinancialBlock(null)
      setFinancialError('')
      setFinancialUnsupported(null)
      setFinancialLoading(false)
      setFinancialPeriodIndex(null)
      setFinancialIndexError('')
      setFinancialIndexLoading(false)
    }
    return () => {
      financialRequestSequence.current++
      financialControllerRef.current?.abort()
      financialIndexRequestSequence.current++
      financialIndexControllerRef.current?.abort()
    }
  }, [open, market, loadFinancialPeriodIndex])

  if (market !== 'TW') return null
  const visiblePayload = payloadInputRef.current === inputScope ? payload : null
  const blocks = visiblePayload?.blocks
  const financialScopeKey = `${inputScope}:${fiscalScope.year}:${fiscalScope.quarter}`
  const visibleFinancialBlock = financialInputScopeRef.current === financialScopeKey ? financialBlock : null
  const visibleFinancialUnsupportedReason = symbol.toUpperCase().startsWith('TPEX:')
    ? 'financial_statements_twse_only'
    : financialUnsupported?.symbolScope === inputScope ? financialUnsupported.reason : ''
  const visibleFinancialLoading = financialInputScopeRef.current === financialScopeKey && financialLoading
  const visibleFinancialError = financialInputScopeRef.current === financialScopeKey ? financialError : ''
  const visibleFinancialPeriodIndex = financialIndexInputScopeRef.current === inputScope ? financialPeriodIndex : null
  const visibleFinancialIndexLoading = financialIndexInputScopeRef.current === inputScope && financialIndexLoading
  const visibleFinancialIndexError = financialIndexInputScopeRef.current === inputScope ? financialIndexError : ''
  const manualQueryDisabled = Boolean(visibleFinancialUnsupportedReason)
    || visibleFinancialIndexLoading
    || visibleFinancialPeriodIndex?.index_status === 'unsupported'
    || visibleFinancialPeriodIndex?.index?.qualification.status === 'unsupported'
  const visibleMainLoading = payloadInputRef.current === inputScope && mainLoading
  const visibleMainError = payloadInputRef.current === inputScope ? mainError : ''
  return (
    <section className="card p-4 space-y-3" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">官方台股研究</h3>
          <p className="text-[11px] text-muted-foreground">估值、法人、公司、營收、籌碼、券商分點、公司行動與 raw 大盤比較保留來源期間、單位和證據；財報獨立載入</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void loadMain('refresh')} disabled={visibleMainLoading} aria-label="重新載入官方研究資料">
          <RefreshCw className={`h-3.5 w-3.5 ${visibleMainLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>
      {visibleMainLoading && visiblePayload ? <div className="text-[10px] text-muted-foreground">正在更新研究資料；目前顯示相容的上一版結果。</div> : null}
      {visibleMainLoading && !visiblePayload ? <div className="text-xs text-muted-foreground py-3">正在載入其他官方研究資料…</div> : null}
      {visibleMainError ? <div className="rounded border border-destructive/30 p-3 text-xs text-destructive">研究資料讀取失敗：{visibleMainError}</div> : null}
      <FinancialStatementsPanel
        block={visibleFinancialBlock || undefined}
        fiscalScope={fiscalScope}
        onFiscalScopeChange={setFiscalScope}
        selectionMode={financialSelectionMode}
        onSelectionModeChange={changeFinancialSelectionMode}
        periodIndex={visibleFinancialPeriodIndex}
        indexLoading={visibleFinancialIndexLoading}
        indexError={visibleFinancialIndexError}
        onRetryIndex={() => void loadFinancialPeriodIndex()}
        onManualQuery={submitManualFinancialQuery}
        manualQueryDisabled={manualQueryDisabled}
        loading={visibleFinancialLoading}
        error={visibleFinancialError}
        unsupportedReason={visibleFinancialUnsupportedReason}
        onRetry={retryFinancialQuery}
      />
      {blocks ? <>
        <div className="text-[11px] text-muted-foreground">{visiblePayload?.instrument_id} · {visiblePayload?.instrument?.security_type || '標的類型未知'} · 區間 {visiblePayload?.selectors.start_date} 至 {visiblePayload?.selectors.end_date}</div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {blocks.valuation ? <ValuationBlock block={blocks.valuation as AnyBlock} /> : null}
          {blocks.institutional_flows ? <FlowBlock block={blocks.institutional_flows as AnyBlock} /> : null}
          {blocks.company_profile ? <ProfileBlock block={blocks.company_profile as AnyBlock} securityType={visiblePayload?.instrument?.security_type} /> : null}
          {blocks.monthly_revenues ? <RevenueBlock block={blocks.monthly_revenues as AnyBlock} securityType={visiblePayload?.instrument?.security_type} /> : null}
          {blocks.margin_short_sale ? <MarginBlock block={blocks.margin_short_sale as AnyBlock} /> : null}
          {blocks.shareholder_distribution ? <ShareholderDistributionBlock block={blocks.shareholder_distribution as AnyBlock} /> : null}
          {blocks.broker_flow ? <BrokerFlowPanel block={blocks.broker_flow as AnyBlock} /> : null}
          {blocks.corporate_actions ? <CorporateActionsBlock block={blocks.corporate_actions as AnyBlock} /> : null}
          {blocks.benchmark_comparison ? <BenchmarkComparisonBlock block={blocks.benchmark_comparison as AnyBlock} /> : null}
        </div>
      </> : null}
      {!visibleMainLoading && !visibleMainError && !visiblePayload ? <div className="text-xs text-muted-foreground">尚未載入其他研究資料。</div> : null}
    </section>
  )
}
