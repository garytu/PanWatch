import type { ResearchDataBlock } from '@panwatch/api'

type BrokerBlock = ResearchDataBlock<Record<string, any>>

const STATUS_LABELS: Record<string, string> = {
  available: '可用',
  partial: '部分可用',
  unsupported: '不支援',
  missing: '未取得覆蓋',
  empty: '來源回報無分點列',
  closed: '來源回報休市',
  error: '讀取失敗',
  failed: '來源處理失敗',
  unknown: '狀態未知',
  not_materialized: '尚未建立明細',
}

const REASONS: Record<string, string> = {
  quantity_range_exceeds_31_calendar_days: '分點數量查詢最多 31 個日曆日。請縮短研究日期範圍後再看分點排行。',
  detail_projection_not_materialized: '來源尚未保留此標的在該日的成交價格明細。',
  materialized_no_rows_status_unknown: '來源沒有回傳成交價格列，目前無法確認是無成交明細（EMPTY）或處理失敗（FAILED）。',
  unsupported_before_twse_bsr_cutover: 'TWSE 價格明細自 2026-07-24 起提供。',
  twse_four_digit_only: '券商分點目前只支援四位數上市標的；上櫃標的尚不支援。',
  timeout: '來源讀取逾時。',
  provider_error: '來源讀取失敗。',
  transport_error: '來源連線失敗。',
  invalid_response: '來源回應格式不符預期。',
  http_503: '來源服務暫時不可用。',
}

function label(value: unknown): string {
  const key = String(value || 'unknown')
  return STATUS_LABELS[key.toLowerCase()] || key
}

function exact(value: unknown): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function providerLabel(provider: string): string {
  return provider === 'capital' ? 'Capital' : provider === 'twse' ? 'TWSE BSR' : provider
}

function stateText(reason: unknown, status: unknown): string {
  const key = String(reason || '')
  return REASONS[key] || label(status)
}

function ConcentrationList({ title, rows, denominator, concentration }: {
  title: string
  rows: any[]
  denominator: unknown
  concentration: unknown
}) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium">{title} · 前五合計占觀察分母 {exact(concentration)}%</div>
      <div className="text-[10px] text-muted-foreground">分母 {exact(denominator)} {rows[0]?.native_unit || ''}</div>
      {rows.length ? rows.map((row) => (
        <div key={row.source_branch_key} className="grid grid-cols-[1fr_auto_auto] gap-x-2 text-[11px]">
          <span>{row.branch_name}（{row.branch_code}）</span>
          <span className="font-mono">{exact(title.includes('買入') ? row.buy_native : row.sell_native)}</span>
          <span className="font-mono text-muted-foreground">{exact(row.share_of_observed_group_pct)}%</span>
        </div>
      )) : <div className="text-[10px] text-muted-foreground">沒有可排序的分點列。</div>}
    </div>
  )
}

export function BrokerFlowPanel({ block }: { block: BrokerBlock }) {
  const data = block.data || {}
  const quantityRange = data.quantity_range || {}
  const coverageRange = data.coverage_range || {}
  const priceLevels = data.price_levels || {}
  const groups = data.quantity_groups || []
  const coverage = data.coverage_observations || []
  const vwapRows = (data.quantity_observations || [])
    .filter((row: any) => row.buy_vwap != null || row.sell_vwap != null)
    .sort((a: any, b: any) => String(b.trade_date).localeCompare(String(a.trade_date)) || String(a.source_branch_key).localeCompare(String(b.source_branch_key)))
  const statusCounts = data.coverage_status_counts_by_provider || {}

  return (
    <section className="rounded-lg border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">券商分點</h4>
        <span className="rounded-full border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground">{label(block.status)}</span>
      </div>
      {block.data ? <>
        <div className="text-[10px] text-muted-foreground">所選資料中最新日期 {String((block.evidence?.freshness as any)?.data_period || '未提供')}；發布與來源接收時間未提供。</div>
        <div className="text-[10px] text-muted-foreground">來源切換日 2026-07-24 · Capital 原生張數與 TWSE 精確股數分開排行；分點代碼依來源保留。</div>
        <div className="rounded bg-muted/30 px-2 py-1.5 text-[10px]">
          數量 {quantityRange.start_date || '—'} 至 {quantityRange.end_date || '—'} · {label(quantityRange.status)} · {stateText(quantityRange.reason, quantityRange.status)}
        </div>
        {groups.map((group: any) => {
          const buyTop = group.top_buy || []
          const sellTop = group.top_sell || []
          const buyRows = buyTop.map((row: any) => ({ ...row, native_unit: group.native_unit }))
          const sellRows = sellTop.map((row: any) => ({ ...row, native_unit: group.native_unit }))
          return (
            <div key={`${group.provider}:${group.native_unit}:${group.precision_shares}`} className="rounded border border-border/40 p-2 space-y-2">
              <div className="flex flex-wrap justify-between gap-1 text-[11px] font-medium">
                <span>{providerLabel(group.provider)} · {group.native_unit === 'lots' ? '張（原生單位）' : '股（精確股數）'}</span>
                <span>{group.coverage_complete_for_source_dates ? '所選來源日期覆蓋完整' : '來源日期覆蓋不完整'}</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <ConcentrationList title="買入排行" rows={buyRows} denominator={group.observed_buy_denominator_native} concentration={group.top_n_buy_concentration_pct} />
                <ConcentrationList title="賣出排行" rows={sellRows} denominator={group.observed_sell_denominator_native} concentration={group.top_n_sell_concentration_pct} />
              </div>
              <p className="text-[10px] text-muted-foreground">分母為此來源在所選期間已回傳分點的買入量或賣出量，兩側分開計算；不代表全市場集中度。 {group.coverage_missing_dates?.length ? `未取得覆蓋日期：${group.coverage_missing_dates.join(', ')}` : ''}</p>
              {group.revision_ids?.length ? <p className="text-[10px] text-muted-foreground break-all">數量修訂：{group.revision_ids.join(', ')}</p> : null}
            </div>
          )
        })}
        {!groups.length ? <p className="text-[11px] text-muted-foreground">此區間沒有可計算排行的數量列；單位或資料缺口請看覆蓋狀態。</p> : null}
        <div className="rounded bg-muted/30 px-2 py-1.5 text-[10px] space-y-1">
          <div>覆蓋 {coverageRange.start_date || '—'} 至 {coverageRange.end_date || '—'} · {label(coverageRange.status)} · {stateText(coverageRange.reason, coverageRange.status)}</div>
          {Object.entries(statusCounts).map(([provider, counts]: [string, any]) => (
            <div key={provider}>{providerLabel(provider)}：{Object.entries(counts).map(([status, count]) => `${label(status)} ${count} 日`).join(' · ')}</div>
          ))}
          {coverage.filter((row: any) => row.status === 'FAILED').map((row: any) => (
            <div key={`${row.provider}:${row.trade_date}`} className="text-destructive">{providerLabel(row.provider)} {row.trade_date} 來源失敗：{row.failure_reason || '未提供原因'}</div>
          ))}
        </div>
        {vwapRows.length ? <div className="rounded border border-border/40 p-2 space-y-1">
          <div className="text-[11px] font-medium">來源買賣成交均價（VWAP）</div>
          <p className="text-[10px] text-muted-foreground">依日期由新到舊顯示前 {Math.min(8, vwapRows.length)} 列，共 {vwapRows.length} 列。</p>
          <p className="text-[10px] text-muted-foreground">買賣 VWAP 是來源成交均價（TWD），不是分點持倉成本。</p>
          {vwapRows.slice(0, 8).map((row: any) => (
            <div key={`${row.trade_date}:${row.source_branch_key}`} className="grid grid-cols-[1fr_auto_auto] gap-x-2 text-[10px]">
              <span>{row.trade_date} · {providerLabel(row.provider)} {row.branch_name}（{row.branch_code}）</span>
              <span>買 {exact(row.buy_vwap)}</span><span>賣 {exact(row.sell_vwap)}</span>
            </div>
          ))}
        </div> : null}
        <div className="rounded border border-border/40 p-2 space-y-1">
          <div className="text-[11px] font-medium">單日成交價格明細 · {priceLevels.trade_date || '—'} · {label(priceLevels.status)}</div>
          <p className="text-[10px] text-muted-foreground">{stateText(priceLevels.reason, priceLevels.status)}</p>
          {priceLevels.revision_consistency_with_same_date_quantities === 'conflict'
            ? <p className="text-[10px] text-destructive">明細與同日數量修訂版本不同；不可視為同一版本。</p>
            : null}
          {(priceLevels.observations || []).slice(0, 12).map((row: any) => (
            <div key={`${row.price}:${row.source_branch_key}`} className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 text-[10px]">
              <span>{row.branch_name}（{row.branch_code}）</span>
              <span>{exact(row.price)} TWD</span><span>買 {exact(row.buy_native)} 股</span><span>賣 {exact(row.sell_native)} 股</span>
            </div>
          ))}
          {(priceLevels.observations || []).length ? <p className="text-[10px] text-muted-foreground break-all">明細修訂：{[...new Set(priceLevels.observations.map((row: any) => row.revision_id))].join(', ')}</p> : null}

        </div>
        {data.revision_consistency_warnings?.length ? <p className="text-[10px] text-destructive">分點數量、覆蓋或明細的筆數／修訂不一致，結果僅部分可用；詳情見來源證據。</p> : null}
        <details className="text-[10px] text-muted-foreground">
          <summary className="cursor-pointer">券商分點來源證據</summary>
          <div className="mt-1 space-y-1 break-all">
            <div>狀態原因：{block.reason || '未提供'}</div>
            <div>來源切換日：{String(block.evidence?.cutover_date || '2026-07-24')}</div>
            <div>VWAP 語意：{String(block.evidence?.vwap_meaning || '來源買賣成交均價；不是持倉成本。')}</div>
            <div>分母限制：{String(block.evidence?.denominator_limit || '只用同一來源與原生單位下的已回傳分點列。')}</div>
            <div>一致性限制：{JSON.stringify(data.revision_consistency_warnings || [])}</div>
            <div>修訂對照：{JSON.stringify(block.evidence?.revision_consistency_by_provider_date || {})}</div>
            <div>來源證據限制：{String(block.evidence?.provenance_limit || '來源沒有回報接收時間。')}</div>
          </div>
        </details>
      </> : <p className="text-[11px] text-muted-foreground">{stateText(block.reason, block.status)}</p>}
    </section>
  )
}
