import { useLayoutEffect, useRef, useState } from 'react'
import type { BenchmarkCurvePoint } from '@panwatch/api'

interface BenchChartProps {
  curve: BenchmarkCurvePoint[]
  height?: number
  className?: string
}

// 3 條內部參考線(均勻分佈,非頂/底邊框線)
const GRID_FRACS = [0.2, 0.5, 0.8]

function BenchChartSvg({
  points,
  width,
  height,
}: {
  points: BenchmarkCurvePoint[]
  width: number
  height: number
}) {
  const padLeft = 2
  const padRight = 40 // 預留右側 % 刻度文字
  const padTop = 12
  const padBottom = 12
  const innerW = Math.max(10, width - padLeft - padRight)
  const innerH = Math.max(10, height - padTop - padBottom)

  const allVals: number[] = []
  for (const p of points) {
    allVals.push(p.portfolio, p.benchmark)
  }
  let min = Math.min(...allVals)
  let max = Math.max(...allVals)
  if (max - min < 1e-6) {
    max += 1
    min -= 1
  }

  const n = points.length
  const xAt = (i: number) => padLeft + (innerW * i) / (n - 1)
  const yAt = (v: number) => padTop + innerH - (innerH * (v - min)) / (max - min)

  const portfolioPts = points.map((p, i) => [xAt(i), yAt(p.portfolio)] as const)
  const benchmarkPts = points.map((p, i) => [xAt(i), yAt(p.benchmark)] as const)
  const portfolioAttr = portfolioPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const benchmarkAttr = benchmarkPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [x0, y0] = portfolioPts[0]
  const [xN, yN] = portfolioPts[n - 1]
  const baseline = padTop + innerH
  const areaAttr = `${xAt(0).toFixed(1)},${baseline.toFixed(1)} ${portfolioAttr} ${xAt(n - 1).toFixed(1)},${baseline.toFixed(1)}`

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="組合淨值 vs 基準走勢圖">
      <defs>
        <linearGradient id="benchchart-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.22} />
          <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* 網格線 + 右側 % 刻度(以曲線起點=100 為基準的累計報酬率) */}
      {GRID_FRACS.map((frac) => {
        const y = padTop + innerH * frac
        const v = min + (max - min) * (1 - frac)
        const pctVal = v - 100
        const label = `${pctVal >= 0 ? '+' : ''}${pctVal.toFixed(1)}%`
        return (
          <g key={frac}>
            <line
              x1={padLeft}
              x2={padLeft + innerW}
              y1={y}
              y2={y}
              stroke="hsl(var(--border))"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text
              x={padLeft + innerW + 6}
              y={y}
              dominantBaseline="middle"
              fontSize={10}
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              fill="hsl(var(--muted-foreground))"
            >
              {label}
            </text>
          </g>
        )
      })}

      {/* 基準:虛線(中性色) */}
      <polyline
        points={benchmarkAttr}
        fill="none"
        stroke="hsl(var(--muted-foreground))"
        strokeWidth={1.5}
        strokeDasharray="5 4"
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* 組合:實線 + 淺面積(主角) */}
      <polygon points={areaAttr} fill="url(#benchchart-area)" stroke="none" />
      <polyline
        points={portfolioAttr}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* 兩端點圓(組合線起止) */}
      <circle cx={x0} cy={y0} r={4} fill="hsl(var(--card))" />
      <circle cx={x0} cy={y0} r={2.5} fill="hsl(var(--primary))" />
      <circle cx={xN} cy={yN} r={4} fill="hsl(var(--card))" />
      <circle cx={xN} cy={yN} r={2.5} fill="hsl(var(--primary))" />
    </svg>
  )
}

/**
 * 組合淨值 vs 基準 雙線圖,無第三方圖表庫依賴。
 * 組合(primary)實線+淺面積、基準虛線(中性色)、3條虛網格線 + 右側 % 刻度、組合線兩端點圓。
 * 用容器 clientWidth(ResizeObserver 實測)而非 CSS 縮放渲染 SVG,保證軸文字/線寬不隨寬度變化而變形。
 * curve 為空/有效點數 < 2 時不渲染(由上層負責展示"計算中"等佔位文案)。
 */
export default function BenchChart({ curve, height = 150, className }: BenchChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const points = (curve || []).filter(
    (p) => Number.isFinite(p.portfolio) && Number.isFinite(p.benchmark),
  )
  if (points.length < 2) return null

  return (
    <div ref={containerRef} className={className} style={{ width: '100%', height }}>
      {width > 0 && <BenchChartSvg points={points} width={width} height={height} />}
    </div>
  )
}
