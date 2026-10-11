import type { DashboardMarketIndex } from '@panwatch/api'
import Sparkline from '@/components/Sparkline'

function pct(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function moveColor(value?: number | null): string {
  if (value == null) return 'text-muted-foreground'
  return value > 0 ? 'text-rose-500' : value < 0 ? 'text-emerald-500' : 'text-muted-foreground'
}

function chipColor(value?: number | null): string {
  if (value == null) return 'bg-accent text-muted-foreground'
  return value > 0 ? 'bg-rose-500/10 text-rose-500' : value < 0 ? 'bg-emerald-500/10 text-emerald-500' : 'bg-accent text-muted-foreground'
}

export function MarketIndexCards({ indices }: { indices: DashboardMarketIndex[] }) {
  return (
    <div className="mb-3 grid grid-cols-2 gap-2.5 md:grid-cols-3 lg:grid-cols-5">
      {indices.map((index) => (
        <div key={`${index.market}:${index.symbol}`} className="card-subtle relative p-2.5">
          <div className="flex items-start justify-between gap-1">
            <div className="min-w-0">
              <div className="truncate text-[11px] text-muted-foreground">{index.name}</div>
              {index.price_kind === 'eod' ? <div className="text-[9px] text-muted-foreground">日線收盤 · {index.trade_date || '日期未知'}</div> : null}
              <div className="font-mono text-[15px] text-foreground">
                {index.current_price != null ? index.current_price.toFixed(2) : '--'}{index.unit === 'index_points' ? ' 點' : ''}
              </div>
              {index.price_kind === 'eod' && index.change_start_date
                ? <div className="text-[9px] text-muted-foreground">較 {index.change_start_date}</div>
                : null}
            </div>
            <span className={`shrink-0 rounded px-1 py-0.5 font-mono text-[10px] ${chipColor(index.change_pct)}`}>
              {pct(index.change_pct)}
            </span>
          </div>
          {index.spark && index.spark.length >= 2 ? (
            <div className="mt-1.5">
              <Sparkline data={index.spark} height={26} className={moveColor(index.change_pct)} />
            </div>
          ) : null}
          {index.price_kind === 'eod' && (index.source_partial || index.source_truncated)
            ? <div className="mt-1 text-[9px] text-amber-600">來源回傳部分資料</div>
            : null}
        </div>
      ))}
    </div>
  )
}

export default MarketIndexCards
