import { type PortfolioBenchmark } from '@panwatch/api'
import ShareCardDialog from './ShareCardDialog'

interface BenchmarkShareCardProps {
  open: boolean
  onClose: () => void
  bench: PortfolioBenchmark
}

// A股配色:紅=漲/正、綠=跌/負、中性=琥珀
const UP = '#e11d48'
const DOWN = '#059669'
const NEUTRAL = '#d97706'

function signColor(v?: number | null): string {
  if (v == null || !isFinite(v)) return NEUTRAL
  if (v > 0) return UP
  if (v < 0) return DOWN
  return NEUTRAL
}

/** 百分比展示(帶 +/- 號),數值已是百分比口徑。 */
function pct(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function num(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return v.toFixed(digits)
}

/**
 * 淨值 vs 基準雙線 sparkline(純 SVG,無外部依賴)。
 * curve 裡 portfolio/benchmark 為歸一化淨值(起點≈1 或 0,後端歸一)。
 * 這裡對兩條線統一做 min-max 歸一,等比對映到畫布,保證兩線同坐標系可比。
 */
function Sparkline({
  curve,
  width = 568,
  height = 96,
}: {
  curve: { date: string; portfolio: number; benchmark: number }[]
  width?: number
  height?: number
}) {
  const pad = 6
  const xs = curve.length
  if (xs < 2) return null
  const allVals: number[] = []
  for (const p of curve) {
    if (isFinite(p.portfolio)) allVals.push(p.portfolio)
    if (isFinite(p.benchmark)) allVals.push(p.benchmark)
  }
  if (allVals.length < 2) return null
  let min = Math.min(...allVals)
  let max = Math.max(...allVals)
  if (max - min < 1e-9) {
    max += 1
    min -= 1
  }
  const innerW = width - pad * 2
  const innerH = height - pad * 2
  const xAt = (i: number) => pad + (innerW * i) / (xs - 1)
  const yAt = (v: number) => pad + innerH - (innerH * (v - min)) / (max - min)
  const path = (key: 'portfolio' | 'benchmark') =>
    curve
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i).toFixed(1)} ${yAt(p[key]).toFixed(1)}`)
      .join(' ')

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      {/* 基準:灰色 */}
      <path d={path('benchmark')} fill="none" stroke="#94a3b8" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {/* 組合:品牌紅(看多色,突出主角) */}
      <path d={path('portfolio')} fill="none" stroke={UP} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

function StatBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div
      style={{
        flex: 1,
        background: '#ffffff',
        border: '1px solid #e2e8f0',
        borderRadius: 12,
        padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color: color ?? '#0f172a', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
    </div>
  )
}

/**
 * 模擬交易成績單卡(vs 基準)。脫敏:全程只展示百分比 / 比率,絕不出現任何金額(¥)。
 */
export default function BenchmarkShareCard({ open, onClose, bench }: BenchmarkShareCardProps) {
  const days = bench.days ?? 60
  const benchLabel = bench.benchmark_label || '滬深300'
  const excess = bench.excess_return
  const heroColor = signColor(excess)
  const curve = (bench.curve || []).filter((p) => isFinite(p.portfolio) && isFinite(p.benchmark))

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`AI模擬交易成績單-近${days}天`}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>
          AI 模擬交易成績單
        </div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>近 {days} 天</div>
      </div>

      {/* Hero:超額收益 */}
      <div
        style={{
          marginTop: 18,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${heroColor} 0%, ${heroColor}cc 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${heroColor}66`,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: 1, opacity: 0.92 }}>
          超額收益(vs {benchLabel})
        </div>
        <div style={{ fontSize: 48, fontWeight: 900, lineHeight: 1.05, letterSpacing: 1, marginTop: 6 }}>
          {pct(excess, 1)}
        </div>
      </div>

      {/* 關鍵指標四宮格 */}
      <div style={{ marginTop: 16, display: 'flex', gap: 12 }}>
        <StatBox label="組合收益" value={pct(bench.portfolio_return, 1)} color={signColor(bench.portfolio_return)} />
        <StatBox label={`${benchLabel}`} value={pct(bench.benchmark_return, 1)} color={signColor(bench.benchmark_return)} />
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 12 }}>
        <StatBox label="資訊比率" value={num(bench.information_ratio, 2)} />
        <StatBox label="相對回檔" value={pct(bench.relative_drawdown, 1)} color={signColor(bench.relative_drawdown)} />
      </div>

      {/* 淨值 vs 基準 sparkline */}
      {curve.length >= 2 && (
        <div
          style={{
            marginTop: 16,
            background: '#ffffff',
            border: '1px solid #e2e8f0',
            borderRadius: 12,
            padding: '14px 14px 10px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 6 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#64748b' }}>
              <span style={{ width: 14, height: 3, borderRadius: 2, background: UP, display: 'inline-block' }} />
              組合淨值
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#64748b' }}>
              <span style={{ width: 14, height: 3, borderRadius: 2, background: '#94a3b8', display: 'inline-block' }} />
              {benchLabel}
            </span>
          </div>
          <Sparkline curve={curve} />
        </div>
      )}
    </ShareCardDialog>
  )
}
