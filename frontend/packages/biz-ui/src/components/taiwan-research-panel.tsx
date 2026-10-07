import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { researchApi, type ResearchDataBlock, type ResearchFreshness, type TaiwanResearchPayload } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

type AnyBlock = ResearchDataBlock<Record<string, any>>

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
}

function statusLabel(status?: string): string {
  return STATUS_LABELS[status || ''] || status || '狀態未知'
}

function sourceLabel(block: AnyBlock): string {
  const evidence = block.evidence || {}
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
  ] as any[]
  const received = [...new Set([
    evidence.source_received_at_utc,
    ...provenance.map((row) => row.received_at_utc || row.source_received_at_utc),
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const requestSequence = useRef(0)

  const load = useCallback(async () => {
    if (!symbol || market !== 'TW') return
    const sequence = ++requestSequence.current
    setLoading(true)
    setError('')
    setPayload(null)
    try {
      const result = await researchApi.taiwan(symbol)
      if (sequence === requestSequence.current) setPayload(result)
    } catch (cause) {
      if (sequence === requestSequence.current) {
        setError(cause instanceof Error ? cause.message : '研究資料載入失敗。')
      }
    } finally {
      if (sequence === requestSequence.current) setLoading(false)
    }
  }, [market, symbol])

  useEffect(() => {
    if (open && market === 'TW') void load()
    else {
      requestSequence.current++
      setPayload(null)
      setError('')
      setLoading(false)
    }
    return () => { requestSequence.current++ }
  }, [open, market, load])

  if (market !== 'TW') return null
  const blocks = payload?.blocks
  return (
    <section className="card p-4 space-y-3" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">官方台股研究</h3>
          <p className="text-[11px] text-muted-foreground">估值、法人、公司資料與月營收各自標示資料期間和來源</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading} aria-label="重新載入官方研究資料">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">完整台股財報尚未接入；月營收與報價不是損益表、資產負債表或現金流量表。</p>
      {loading && !payload ? <div className="text-xs text-muted-foreground py-3">正在載入官方研究資料…</div> : null}
      {error ? <div className="rounded border border-destructive/30 p-3 text-xs text-destructive">{error}</div> : null}
      {blocks ? <>
        <div className="text-[11px] text-muted-foreground">{payload?.instrument_id} · {payload?.instrument?.security_type || '標的類型未知'} · 區間 {payload?.selectors.start_date} 至 {payload?.selectors.end_date}</div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <ValuationBlock block={blocks.valuation as AnyBlock} />
          <FlowBlock block={blocks.institutional_flows as AnyBlock} />
          <ProfileBlock block={blocks.company_profile as AnyBlock} securityType={payload?.instrument?.security_type} />
          <RevenueBlock block={blocks.monthly_revenues as AnyBlock} securityType={payload?.instrument?.security_type} />
        </div>
      </> : null}
      {!loading && !error && !payload ? <div className="text-xs text-muted-foreground">尚未載入資料。</div> : null}
    </section>
  )
}
