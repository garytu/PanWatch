import { type StrategySignalItem } from '@panwatch/api'
import ShareCardDialog from './ShareCardDialog'

interface SignalScoreShareCardProps {
  open: boolean
  onClose: () => void
  item: StrategySignalItem
}

// A股配色:看多=紅、看空/中性等
const UP = '#e11d48'
const NEUTRAL = '#d97706'
const SLATE = '#475569'

const MARKET_LABEL: Record<string, string> = { CN: 'A股', HK: '港股', US: '美股' }
const marketLabel = (m?: string) => (m ? MARKET_LABEL[m] || m : '')

/**
 * action → 展示標籤 + 配色(與機會頁一致:buy/add 看多偏紅,hold 中性琥珀,其餘灰)。
 * 非持倉的 hold → 觀望、非持倉的 add → 建倉(與 Opportunities 的 displayActionLabel 對齊)。
 */
function actionVisual(item: StrategySignalItem): { label: string; color: string } {
  const key = (item.action || '').toLowerCase()
  let label = item.action_label || item.action || '觀望'
  if (!item.is_holding_snapshot && key === 'hold') label = '觀望'
  if (!item.is_holding_snapshot && key === 'add') label = '建倉'
  if (key === 'buy' || key === 'add') return { label, color: UP }
  if (key === 'hold') return { label, color: NEUTRAL }
  return { label, color: SLATE }
}

/** AI 評分(1~10)分檔配色:≥8 紅(強)、6~8 琥珀、<6 灰。 */
function scoreColor(score: number): string {
  if (score >= 8) return UP
  if (score >= 6) return NEUTRAL
  return SLATE
}

function FactorList({
  title,
  color,
  bg,
  border,
  items,
  sign,
}: {
  title: string
  color: string
  bg: string
  border: string
  items: { label: string; contribution: number }[]
  sign: '+' | '-'
}) {
  if (!items.length) return null
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color, marginBottom: 8 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {items.map((f, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              background: bg,
              border: `1px solid ${border}`,
              borderRadius: 8,
              padding: '7px 10px',
              fontSize: 12.5,
            }}
          >
            <span style={{ color: '#334155', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {f.label}
            </span>
            <span style={{ color, fontWeight: 800, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
              {sign}
              {Math.abs(f.contribution).toFixed(1)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * 個股 AI 評分卡。Hero=AI 評分 X/10 + 操作標籤 + 標的;下方利好/風險因子來自 factor_explain。
 */
export default function SignalScoreShareCard({ open, onClose, item }: SignalScoreShareCardProps) {
  const av = actionVisual(item)
  const aiScore = typeof item.ai_score === 'number' ? item.ai_score : null
  const heroColor = aiScore != null ? scoreColor(aiScore) : SLATE
  const name = item.stock_name || item.stock_symbol
  const positive = (item.factor_explain?.positive ?? []).slice(0, 4)
  const negative = (item.factor_explain?.negative ?? []).slice(0, 4)
  const summary = (item.signal || item.reason || '').replace(/\s+/g, ' ').trim()

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`AI選股評分-${name}`}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>AI 選股評分</div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>
          {marketLabel(item.stock_market)}
        </div>
      </div>

      {/* Hero:AI 評分 + 操作標籤 + 標的 */}
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 22,
                fontWeight: 800,
                lineHeight: 1.2,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {name}
            </div>
            <div style={{ fontSize: 13, opacity: 0.9, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>
              {item.stock_market}:{item.stock_symbol}
            </div>
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right', flexShrink: 0 }}>
            <div style={{ fontSize: 12, opacity: 0.92, fontWeight: 600, letterSpacing: 1 }}>AI 評分</div>
            <div style={{ fontSize: 44, fontWeight: 900, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
              {aiScore != null ? aiScore : '--'}
              <span style={{ fontSize: 20, opacity: 0.85 }}> / 10</span>
            </div>
          </div>
        </div>
        <div
          style={{
            marginTop: 14,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            background: 'rgba(255,255,255,0.22)',
            borderRadius: 999,
            padding: '5px 14px',
            fontSize: 14,
            fontWeight: 700,
          }}
        >
          {av.label}
        </div>
      </div>

      {/* 一句話訊號 */}
      {summary && (
        <div
          style={{
            marginTop: 16,
            fontSize: 14,
            lineHeight: 1.6,
            color: '#334155',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {summary}
        </div>
      )}

      {/* 因子拆解:利好(綠) / 風險(紅) */}
      {(positive.length > 0 || negative.length > 0) && (
        <div style={{ marginTop: 18, display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          <FactorList
            title="利好因子"
            color="#059669"
            bg="#ecfdf5"
            border="#a7f3d0"
            items={positive}
            sign="+"
          />
          <FactorList title="風險因子" color="#e11d48" bg="#fff1f2" border="#fecdd3" items={negative} sign="-" />
        </div>
      )}
    </ShareCardDialog>
  )
}
