import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'

interface KlineIndicatorsProps {
  summary: KlineSummaryData
}

export function KlineIndicators({ summary: s }: KlineIndicatorsProps) {
  return (
    <div className="space-y-3">
      {/* 趨勢與形態（帶說明）*/}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.trend && (
          <HoverPopover
            title="趨勢（均線排列）"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">是什麼：</span>
                  趨勢標籤來自均線（MA5/MA10/MA20）的相對位置，基於日K收盤價計算。MA越短越敏感，越長越平滑。
                </div>
                <div>
                  <span className="font-medium text-foreground">常見解讀：</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li><span className="font-medium text-foreground">多頭排列</span>（MA5 &gt; MA10 &gt; MA20）：上升趨勢更“順”，回撥通常先看 MA5/MA10 的支撐。</li>
                    <li><span className="font-medium text-foreground">空頭排列</span>（MA5 &lt; MA10 &lt; MA20）：下降趨勢佔優，反彈到 MA10/MA20 往往遇到壓力。</li>
                    <li><span className="font-medium text-foreground">均線交織</span>：震盪/周轉期，訊號更依賴成交量與關鍵價位。</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<TechnicalBadge label={s.trend} tone="neutral" help />}
          />
        )}

        {s.macd_status && (
          <HoverPopover
            title="MACD（趨勢/動能）"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">是什麼：</span>
                  MACD 由兩條線（DIF/DEA）與柱體（hist）組成。常見口徑：DIF=EMA12-EMA26，DEA=EMA(DIF,9)，hist≈(DIF-DEA)*2。
                </div>
                <div>
                  <span className="font-medium text-foreground">代表什麼：</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li><span className="font-medium text-foreground">金叉</span>：DIF 上穿 DEA，短線動能由弱轉強。</li>
                    <li><span className="font-medium text-foreground">死叉</span>：DIF 下穿 DEA，短線動能由強轉弱。</li>
                    <li><span className="font-medium text-foreground">柱體正/負</span>：正值通常表示多頭動能佔優；負值通常表示空頭動能佔優。</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<TechnicalBadge label={`MACD ${s.macd_status}`} tone="neutral" help />}
          />
        )}

        {s.rsi_status && (
          <HoverPopover
            title="RSI（相對強弱）"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">是什麼：</span>
                  RSI 衡量一段時間內上漲與下跌力度的相對強弱（0-100）。這裡展示的是 RSI6（近6個交易日）。
                </div>
                <div>
                  <span className="font-medium text-foreground">閾值參考：</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li>RSI6 &gt; 80：超買（回檔風險更高）</li>
                    <li>RSI6 70-80：偏強（動能偏多）</li>
                    <li>RSI6 &lt; 20：超賣（反彈機率提升）</li>
                  </ul>
                </div>
              </div>
            }
            trigger={
              <TechnicalBadge
                label={`RSI ${s.rsi_status}${s.rsi6 != null ? ` (${s.rsi6.toFixed(0)})` : ''}`}
                tone={s.rsi_status === '超買' ? 'bullish' : s.rsi_status === '超賣' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kdj_status && (
          <HoverPopover
            title="KDJ（轉折/超買超賣）"
            content={<div>J 值更敏感，金叉/死叉用於觀察短期轉折，但容易受震盪幹擾，需結合趨勢與量價。</div>}
            trigger={<TechnicalBadge label={`KDJ ${s.kdj_status}`} tone="neutral" help />}
          />
        )}

        {s.volume_trend && (
          <HoverPopover
            title="量能（成交量配合）"
            content={<div>放量常用於確認突破或反彈有效性；縮量上衝/下跌容易“虛”。與趨勢、關鍵位結合更可靠。</div>}
            trigger={
              <TechnicalBadge
                label={`${s.volume_trend}${s.volume_ratio != null ? ` (${s.volume_ratio.toFixed(1)}x)` : ''}`}
                tone={s.volume_trend === '放量' ? 'warning' : s.volume_trend === '縮量' ? 'info' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.boll_status && (
          <HoverPopover
            title="布林帶（波動/偏離）"
            content={<div>上軌/下軌的突破/跌破常見於趨勢階段或極端波動。配合量能與回踩/站穩確認有效性。</div>}
            trigger={
              <TechnicalBadge
                label={`布林 ${s.boll_status}`}
                tone={s.boll_status === '突破上軌' ? 'bullish' : s.boll_status === '跌破下軌' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kline_pattern && (
          <HoverPopover
            title="K線形態（區域性結構）"
            content={<div>單根形態提示意義有限，更看重所處位置（趨勢/支撐壓力附近）與量能配合。</div>}
            trigger={<TechnicalBadge label={s.kline_pattern} tone="warning" help />}
          />
        )}
      </div>

      {/* 支撐壓力（帶說明）*/}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.support != null && (
          <HoverPopover
            title="支撐位（關鍵支撐區）"
            content={<div>接近支撐更容易止跌反彈；放量跌破可能轉為壓力。更偏向“區域”而非一點。</div>}
            trigger={<TechnicalBadge label={`支撐 ${s.support.toFixed(2)}`} tone="bearish" help />}
          />
        )}
        {s.resistance != null && (
          <HoverPopover
            title="壓力位（關鍵壓力區）"
            content={<div>越接近壓力上行越難；放量突破並站穩後，原壓力往往會角色互換變為支撐。</div>}
            trigger={<TechnicalBadge label={`壓力 ${s.resistance.toFixed(2)}`} tone="bullish" help />}
          />
        )}
      </div>
    </div>
  )
}
