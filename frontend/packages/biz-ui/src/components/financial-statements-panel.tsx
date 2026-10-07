import type { FinancialStatementFact, FinancialStatementName, ResearchDataBlock } from '@panwatch/api'

type AnyBlock = ResearchDataBlock<Record<string, any>>

const STATEMENTS: Array<{ key: FinancialStatementName; title: string }> = [
  { key: 'balance_sheet', title: '資產負債表' },
  { key: 'comprehensive_income', title: '綜合損益表' },
  { key: 'cash_flows', title: '現金流量表' },
]

export function taipeiTodayParts(): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: 'numeric', day: 'numeric',
  }).formatToParts(new Date())
  return {
    year: Number(parts.find((part) => part.type === 'year')?.value),
    month: Number(parts.find((part) => part.type === 'month')?.value),
    day: Number(parts.find((part) => part.type === 'day')?.value),
  }
}

export function maxCompletedQuarter(year: number): number {
  const today = taipeiTodayParts()
  let completed = 0
  for (let quarter = 1; quarter <= 4; quarter++) {
    const end = new Date(Date.UTC(year, quarter * 3, 0))
    if (end.getUTCFullYear() < today.year || (
      end.getUTCFullYear() === today.year && (
        end.getUTCMonth() + 1 < today.month ||
        (end.getUTCMonth() + 1 === today.month && end.getUTCDate() < today.day)
      )
    )) completed = quarter
  }
  return completed
}

export function defaultFiscalScope(): { year: number; quarter: number } {
  const today = taipeiTodayParts()
  let year = today.year
  let quarter = maxCompletedQuarter(year)
  if (!quarter) {
    year--
    quarter = 4
  }
  return { year, quarter }
}

function exactValue(value: unknown): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function qnameLocalName(qname: string): string {
  const delimiter = qname.lastIndexOf('}')
  return delimiter < 0 ? qname : qname.slice(delimiter + 1)
}

function unitLabel(fact: FinancialStatementFact): string {
  const numerator = (fact.unit?.numerator || []).map(qnameLocalName).join(' × ') || '單位未提供'
  const denominator = (fact.unit?.denominator || []).map(qnameLocalName).join(' × ')
  return denominator ? `${numerator} / ${denominator}` : numerator
}

function periodLabel(fact: FinancialStatementFact, fiscalYear: number): string {
  const period = fact.context?.period
  const date = period?.kind === 'instant' ? period.instant : period?.end_date
  const comparison = typeof date === 'string' && Number(date.slice(0, 4)) < fiscalYear
  const prefix = comparison ? '比較期' : '本期'
  if (period?.kind === 'instant') return `${prefix} · 時點 ${period.instant}`
  const start = period?.start_date || '期間起點未知'
  const end = period?.end_date || '期間終點未知'
  const endYear = typeof period?.end_date === 'string' ? Number(period.end_date.slice(0, 4)) : fiscalYear
  let duration = '來源期間'
  if (typeof period?.start_date === 'string' && /^\d{4}-01-01$/.test(period.start_date)) {
    const fullYear = period.end_date === `${period.start_date.slice(0, 4)}-12-31`
    duration = fullYear ? '全年期間' : '年初至今（YTD）'
    if (endYear < fiscalYear) duration = fullYear ? '比較全年期間' : '比較期年初至今（YTD）'
  }
  return `${prefix} · ${duration} ${start} 至 ${end}`
}

function blockHeading(block: AnyBlock) {
  const labels: Record<string, string> = { available: '可用', partial: '部分可用', missing: '缺少資料', unsupported: '不適用', error: '讀取失敗' }
  return <div className="flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">台股財報（來源原始事實）</h4><span className="rounded-full border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground">{labels[block.status] || block.status}</span></div>
}

export function FinancialStatementsPanel({ block }: { block?: AnyBlock }) {
  if (!block) return null
  const data = block.data as any
  const report = data?.report
  const qualification = data?.qualification
  const coverage = data?.coverage
  const facts = Array.isArray(data?.facts) ? data.facts as FinancialStatementFact[] : []
  const year = Number(data?.fiscal_year)
  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2 lg:col-span-2">
      {blockHeading(block)}
      {report ? <>
        <div className="text-[11px] text-muted-foreground">{data.fiscal_year}Q{data.fiscal_quarter} · 合併 · {report.member_filename} · 修訂 {report.semantic_revision_id}</div>
        <div className="text-[11px] text-muted-foreground">報表接收 {report.original_received_at_utc || '未知'} · 最新發現 {coverage?.latest_discovery_presence || '未知'}{coverage?.original_received_at_utc ? `（${coverage.original_received_at_utc}）` : ''} · 發布時間未知</div>
        <div className="text-[11px] text-muted-foreground">
          精確值已依來源 scale 保留；來源字串與 scale 另列。期間照來源日期顯示，沒有推導單季值或財務比率。
          {data.truncated ? ` 結果已截斷，只返回 ${data.returned_fact_count}/${data.total_fact_count} 筆，屬部分報表。` : ''}
        </div>
        <div className="space-y-1">
          {STATEMENTS.map(({ key, title }) => {
            const rows = facts.filter((fact) => fact.statement === key)
            return (
              <details key={key} className="rounded border border-border/50 px-2 py-1">
                <summary className="cursor-pointer text-xs font-medium">{title} · {rows.length} 筆回傳事實</summary>
                {rows.length ? <div className="mt-2 max-h-72 overflow-auto">
                  <table className="w-full min-w-[720px] table-fixed text-[11px]">
                    <colgroup><col className="w-[32%]" /><col className="w-[28%]" /><col className="w-[20%]" /><col className="w-[20%]" /></colgroup>
                    <thead className="sticky top-0 bg-background text-left text-muted-foreground"><tr><th className="px-1 py-1">來源概念</th><th className="px-1 py-1">來源期間</th><th className="px-1 py-1 text-right">精確值／單位</th><th className="px-1 py-1">來源字串與尺度</th></tr></thead>
                    <tbody>{rows.map((fact) => <tr key={`${fact.statement}:${fact.occurrence_ordinal}`} className="border-t border-border/40 align-top">
                      <td className="px-1 py-1"><code className="break-all" title={fact.concept_qname}>{qnameLocalName(fact.concept_qname)}</code><div title={fact.concept_qname} className="truncate text-[9px] text-muted-foreground">{fact.concept_qname}</div></td>
                      <td className="px-1 py-1">{periodLabel(fact, year)}</td>
                      <td className="whitespace-nowrap px-1 py-1 text-right font-mono">{fact.is_nil ? 'nil' : exactValue(fact.value)}<div className="font-sans text-[9px] text-muted-foreground">{unitLabel(fact)}</div></td>
                      <td className="px-1 py-1 font-mono">{fact.lexical_value || '—'}<div className="font-sans text-[9px] text-muted-foreground">scale={fact.scale === null ? '未提供' : fact.scale}{fact.sign ? ` · sign=${fact.sign}` : ''} · decimals={fact.decimals ?? '未提供'}</div></td>
                    </tr>)}</tbody>
                  </table>
                </div> : <div className="py-2 text-[11px] text-muted-foreground">此報表沒有回傳此表的事實。</div>}
              </details>
            )
          })}
        </div>
      </> : <div className="text-xs text-muted-foreground">
        {block.status === 'unsupported' || qualification?.status === 'unsupported'
          ? `不支援：${qualification?.reason || block.reason}`
          : block.status === 'error'
            ? `來源讀取失敗（${block.reason}）。`
            : qualification?.status === 'pending'
              ? `資格證據尚未完整（${qualification.reason}）；目前不宣稱沒有報表。`
              : coverage?.latest_discovery_presence === 'not_advertised'
                ? '最新成功發現未列出此報表；這不代表公司從未申報。'
                : coverage?.reason === 'never_collected'
                  ? '此期尚無留存報表或有效發現證據。'
                  : `此期沒有留存報表（${block.reason || coverage?.reason || 'coverage unknown'}）。`}
      </div>}
      {data ? <div className="text-[10px] text-muted-foreground">資格 {qualification?.status || '未知'} · 報表覆蓋 {coverage?.status || '未知'} · 最新發現 {coverage?.latest_discovery_presence || '未知'} · {data.returned_fact_count ?? 0}/{data.total_fact_count ?? 0} 筆</div> : null}
      <details className="text-[10px] text-muted-foreground"><summary className="cursor-pointer">財報來源 evidence</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(block.evidence, null, 2)}</pre></details>
    </section>
  )
}
