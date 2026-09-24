import { useCallback, useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'

export interface KlineSummaryData {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  last_close?: number | null
  recent_5_up?: number | null
  trend?: string
  macd_status?: string
  macd_cross?: string | null
  macd_cross_days?: number | null
  macd_hist?: number | null
  rsi6?: number | null
  rsi_status?: string
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  volume_ratio?: number | null
  volume_trend?: string
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_width?: number | null
  boll_status?: string
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  kline_pattern?: string | null
  support?: number | null
  resistance?: number | null
  support_s?: number | null
  support_m?: number | null
  support_l?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  resistance_l?: number | null
  change_5d?: number | null
  change_20d?: number | null
  amplitude?: number | null
  amplitude_avg5?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummaryData
}

interface KlineSummaryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
  initialSummary?: KlineSummaryData | null
}

function formatLocalDateTime(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

export function KlineSummaryDialog({
  open,
  onOpenChange,
  symbol,
  market,
  stockName,
  hasPosition,
  initialSummary = null,
}: KlineSummaryDialogProps) {
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<KlineSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const buildSuggestion = (s: KlineSummaryData, holding?: boolean) => {
    const scored = buildKlineSuggestion(s, holding)
    const items: Array<{ text: string; delta: number }> = []
    let localScore = 0

    const add = (text: string, delta: number) => { items.push({ text, delta }); localScore += delta }

    if (s.trend?.includes('多頭')) add('均線多頭排列，趨勢偏強', 2)
    else if (s.trend?.includes('空頭')) add('均線空頭排列，趨勢偏弱', -2)

    if (s.macd_status?.includes('金叉')) add('MACD 金叉，短線動能偏強', 2)
    if (s.macd_status?.includes('死叉')) add('MACD 死叉，短線動能轉弱', -2)
    if (typeof s.macd_hist === 'number') add(`MACD 柱體${s.macd_hist > 0 ? '為正' : s.macd_hist < 0 ? '為負' : '接近0'}`, s.macd_hist > 0 ? 1 : s.macd_hist < 0 ? -1 : 0)

    if (s.rsi_status?.includes('超賣')) add('RSI 超賣，可能存在反彈', 1)
    else if (s.rsi_status?.includes('偏強')) add('RSI 偏強，買盤佔優', 1)
    else if (s.rsi_status?.includes('超買')) add('RSI 超買，注意回撥風險', -1)
    else if (s.rsi_status?.includes('偏弱')) add('RSI 偏弱，短線承壓', -1)

    if (s.kdj_status?.includes('金叉')) add('KDJ 金叉，短線轉強', 1)
    if (s.kdj_status?.includes('死叉')) add('KDJ 死叉，短線轉弱', -1)

    if (s.boll_status?.includes('突破上軌')) add('突破布林上軌，趨勢強勢', 1)
    else if (s.boll_status?.includes('跌破下軌')) add('跌破布林下軌，走勢偏弱', -1)

    if (s.volume_trend?.includes('放量')) add('放量配合，資金參與度提升', 1)
    else if (s.volume_trend?.includes('縮量')) add('縮量，動能不足', -1)

    if (s.last_close != null && s.support != null && s.support > 0 && s.last_close <= s.support * 1.02) add('價格接近支撐位，止跌反彈機率提升', 1)
    if (s.last_close != null && s.resistance != null && s.resistance > 0 && s.last_close >= s.resistance * 0.98) add('價格接近壓力位，上行空間受限', -1)

    return { ...scored, score: localScore, items }
  }

  useEffect(() => {
    if (!open || !symbol) return

    // If we already have preloaded summary, use it without refetch
    if (initialSummary) {
      setSummary(initialSummary)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    setSummary(null)

    const m = market || 'CN'
    fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(m)}`)
      .then((data) => setSummary(data.summary || null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [open, symbol, market, initialSummary])

  const effectiveSummary = initialSummary || summary
  const suggestion = effectiveSummary ? buildSuggestion(effectiveSummary, hasPosition) : null

  const handleAskAI = useCallback(() => {
    if (!effectiveSummary) return
    const s = effectiveSummary
    const parts: string[] = []
    const items = []
    if (s.trend) items.push(`趨勢${s.trend}`)
    if (s.macd_status) items.push(`MACD${s.macd_status}${s.macd_hist != null ? `(hist=${s.macd_hist.toFixed(3)})` : ''}`)
    if (s.rsi_status) items.push(`RSI${s.rsi_status}${s.rsi6 != null ? `(${s.rsi6.toFixed(0)})` : ''}`)
    if (s.kdj_status) items.push(`KDJ${s.kdj_status}${s.kdj_k != null ? `(K=${s.kdj_k.toFixed(1)},D=${s.kdj_d?.toFixed(1)},J=${s.kdj_j?.toFixed(1)})` : ''}`)
    if (s.boll_status) items.push(`布林${s.boll_status}${s.boll_width != null ? `(頻寬${s.boll_width.toFixed(1)}%)` : ''}`)
    if (s.volume_trend) items.push(`量能${s.volume_trend}${s.volume_ratio != null ? `(${s.volume_ratio.toFixed(1)}x)` : ''}`)
    if (items.length) parts.push(`技術指標：${items.join('，')}`)
    if (s.support != null) parts.push(`支撐位：${s.support.toFixed(2)}`)
    if (s.resistance != null) parts.push(`壓力位：${s.resistance.toFixed(2)}`)
    if (s.last_close != null) parts.push(`收盤價：${s.last_close.toFixed(2)}`)
    if (s.change_5d != null) parts.push(`5日漲跌：${s.change_5d.toFixed(2)}%`)
    if (s.change_20d != null) parts.push(`20日漲跌：${s.change_20d.toFixed(2)}%`)
    if (s.ma5 != null) parts.push(`均線：MA5=${s.ma5.toFixed(2)} MA10=${s.ma10?.toFixed(2)} MA20=${s.ma20?.toFixed(2)} MA60=${s.ma60?.toFixed(2)}`)
    if (suggestion) {
      parts.push(`技術評分：${suggestion.action_label}(score=${suggestion.score})，訊號：${suggestion.signal || '中性'}`)
      if (suggestion.items.length) {
        parts.push(`評分依據：${suggestion.items.map(e => `${e.text}(${e.delta > 0 ? '+' : ''}${e.delta})`).join('；')}`)
      }
    }
    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
      detail: { symbol, market, stockName: stockName || symbol, pageContext: parts.join('\n') }
    }))
    onOpenChange(false)
  }, [effectiveSummary, suggestion, symbol, market, stockName, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>K線 / 技術指標</DialogTitle>
          <DialogDescription>
            <div className="space-y-0.5">
              <div>{stockName ? `${stockName} (${symbol})` : symbol}</div>
              {(effectiveSummary?.timeframe || effectiveSummary?.computed_at || effectiveSummary?.asof) && (
                <div className="text-[11px] text-muted-foreground/70">
                  {effectiveSummary?.timeframe ? `週期: ${effectiveSummary.timeframe}` : '週期: 1d'}
                  {effectiveSummary?.asof ? ` · 資料截至: ${effectiveSummary.asof}` : ''}
                  {effectiveSummary?.computed_at ? ` · 計算時間: ${formatLocalDateTime(effectiveSummary.computed_at)}` : ''}
                </div>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        {!initialSummary && loading ? (
          <div className="text-[12px] text-muted-foreground">載入中...</div>
        ) : error ? (
          <div className="text-[12px] text-rose-500">{error}</div>
        ) : !effectiveSummary ? (
          <div className="text-[12px] text-muted-foreground">暫無資料</div>
        ) : (
          <div className="space-y-3">
            {suggestion && (
              <div className="p-3 rounded-lg bg-accent/20 border border-border/30">
                <div className="flex items-center justify-between gap-2">
                  <TechnicalBadge
                    label={suggestion.action_label}
                    tone={technicalToneFromSuggestionAction(suggestion.action, suggestion.action_label)}
                    size="sm"
                  />
                  <span className="text-[10px] text-muted-foreground">
                    {hasPosition ? '已持倉' : '未持倉'} · score {suggestion.score}
                  </span>
                </div>
                <div className="mt-2 text-[12px] text-foreground font-medium">
                  {suggestion.signal}
                </div>

                {suggestion.items.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {suggestion.items.map((it, idx) => {
                      const color =
                        it.delta > 0 ? 'text-rose-500' :
                        it.delta < 0 ? 'text-emerald-500' :
                        'text-muted-foreground'
                      return (
                        <div key={`${it.text}-${idx}`} className="flex items-center justify-between gap-3 text-[11px]">
                          <span className="text-muted-foreground">{it.text}</span>
                          <span className={`font-mono ${color}`}>
                            {it.delta > 0 ? '+' : ''}{it.delta}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                <div className="mt-2 text-[10px] text-muted-foreground/70">
                  僅基於技術指標規則生成，非投資建議
                </div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground/60">
              提示：懸停指標標籤可檢視詳細說明
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary.trend && (
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
                      <div className="text-[10px] text-muted-foreground/70">當前：{effectiveSummary.trend}</div>
                      {(effectiveSummary.ma5 != null || effectiveSummary.ma10 != null || effectiveSummary.ma20 != null || effectiveSummary.ma60 != null) && (
                        <div className="text-[10px] text-muted-foreground/70">
                          均線：MA5≈{effectiveSummary.ma5 != null ? effectiveSummary.ma5.toFixed(2) : '—'}；MA10≈{effectiveSummary.ma10 != null ? effectiveSummary.ma10.toFixed(2) : '—'}；MA20≈{effectiveSummary.ma20 != null ? effectiveSummary.ma20.toFixed(2) : '—'}；MA60≈{effectiveSummary.ma60 != null ? effectiveSummary.ma60.toFixed(2) : '—'}
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：均線屬於滯後指標，更適合“過濾趨勢”，不建議單獨作為進出場依據。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.trend} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.macd_status && (
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
                      <div className="text-[10px] text-muted-foreground/70">
                        當前：{effectiveSummary.macd_status}{effectiveSummary.macd_hist != null ? `，柱體${effectiveSummary.macd_hist > 0 ? '為正' : effectiveSummary.macd_hist < 0 ? '為負' : '接近0'} (hist≈${effectiveSummary.macd_hist.toFixed(3)})` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：MACD 在震盪區間容易頻繁“假交叉”，通常需要結合趨勢（均線）與量價確認。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`MACD ${effectiveSummary.macd_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.rsi_status && (
                <HoverPopover
                  title="RSI（相對強弱）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        RSI 用於衡量一段時間內上漲與下跌力度的相對強弱（0-100）。這裡展示的是 RSI6（近6個交易日）。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">專案內閾值：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>RSI6 &gt; 80：超買（回檔風險更高）</li>
                          <li>RSI6 70-80：偏強（動能偏多）</li>
                          <li>RSI6 &lt; 20：超賣（可能反彈，但下跌趨勢中可長期超賣）</li>
                          <li>RSI6 20-30：偏弱（動能偏空）</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        當前：{effectiveSummary.rsi_status}{effectiveSummary.rsi6 != null ? `，RSI6≈${effectiveSummary.rsi6.toFixed(0)}` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：超買不等於立刻下跌、超賣不等於立刻反彈；更可靠的用法是結合趨勢和關鍵位看“背離/衰竭”。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`RSI ${effectiveSummary.rsi_status}${effectiveSummary.rsi6 != null ? ` (${effectiveSummary.rsi6.toFixed(0)})` : ''}`}
                      tone={effectiveSummary.rsi_status === '超買' ? 'bullish' : effectiveSummary.rsi_status === '超賣' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kdj_status && (
                <HoverPopover
                  title="KDJ（隨機指標）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        KDJ 屬於動量類指標，反映價格在一段區間內所處位置（類似隨機振盪器）。常用訊號是 K 與 D 的金叉/死叉。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什麼：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">金叉</span>：短線轉強的提示，配合上升趨勢更有效。</li>
                          <li><span className="font-medium text-foreground">死叉</span>：短線轉弱的提示，配合下降趨勢更有效。</li>
                          <li>J 值極端（&gt;100 或 &lt;0）時，常被視為“超買/超賣”，但在強趨勢裡可能失真。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>當前：{effectiveSummary.kdj_status}</div>
                        {(effectiveSummary.kdj_k != null || effectiveSummary.kdj_d != null || effectiveSummary.kdj_j != null) && (
                          <div>
                            K≈{effectiveSummary.kdj_k != null ? effectiveSummary.kdj_k.toFixed(1) : '—'}{' '}
                            D≈{effectiveSummary.kdj_d != null ? effectiveSummary.kdj_d.toFixed(1) : '—'}{' '}
                            J≈{effectiveSummary.kdj_j != null ? effectiveSummary.kdj_j.toFixed(1) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：震盪行情裡 KDJ 可能頻繁反覆，建議與支撐/壓力位結合使用。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`KDJ ${effectiveSummary.kdj_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary?.volume_trend && (
                <HoverPopover
                  title="量能（放量/縮量）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        量能用於判斷行情“是否有成交支撐”。這裡的量能趨勢來自 volume_ratio（當日量 / 近5日均量）。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">怎麼解讀：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">放量</span>：通常表示參與度提升；若上漲放量更利於趨勢延續。</li>
                          <li><span className="font-medium text-foreground">縮量</span>：可能表示觀望/衰竭；若下跌縮量，有時是拋壓減弱的訊號。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        當前：{effectiveSummary.volume_trend}{effectiveSummary.volume_ratio != null ? `，量比≈${effectiveSummary.volume_ratio.toFixed(1)}x` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：量能的意義需要結合價格方向（價漲量增/價漲量縮/價跌量增/價跌量縮）綜合判斷。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`${effectiveSummary.volume_trend}${effectiveSummary.volume_ratio != null ? ` (${effectiveSummary.volume_ratio.toFixed(1)}x)` : ''}`}
                      tone={effectiveSummary.volume_trend === '放量' ? 'warning' : effectiveSummary.volume_trend === '縮量' ? 'info' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.boll_status && (
                <HoverPopover
                  title="布林帶（波動/通道）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        布林帶由中軌（通常是 MA20）和上下軌（中軌±2倍標準差）組成，用於刻畫價格通道與波動變化。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什麼：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">突破上軌</span>：短期偏強，但也可能“衝高回落”，需結合量能確認。</li>
                          <li><span className="font-medium text-foreground">跌破下軌</span>：短期偏弱，但在恐慌下跌時也可能出現超跌反彈。</li>
                          <li>頻寬收口常見於波動收斂，之後容易出現方向選擇；頻寬開口表示波動放大。</li>
                        </ul>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">專案內頻寬閾值：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>頻寬 &lt; 5：收口窄幅（更偏盤整/醞釀）</li>
                          <li>頻寬 &gt; 15：開口放大（波動擴張）</li>
                          <li>其他：正常波動</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>
                          當前：{effectiveSummary.boll_status}{effectiveSummary.boll_width != null ? `，頻寬≈${effectiveSummary.boll_width.toFixed(1)}%` : ''}
                        </div>
                        {(effectiveSummary.boll_upper != null || effectiveSummary.boll_mid != null || effectiveSummary.boll_lower != null) && (
                          <div>
                            上軌≈{effectiveSummary.boll_upper != null ? effectiveSummary.boll_upper.toFixed(2) : '—'}；中軌≈{effectiveSummary.boll_mid != null ? effectiveSummary.boll_mid.toFixed(2) : '—'}；下軌≈{effectiveSummary.boll_lower != null ? effectiveSummary.boll_lower.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`布林 ${effectiveSummary.boll_status}`}
                      tone={effectiveSummary.boll_status === '突破上軌' ? 'bullish' : effectiveSummary.boll_status === '跌破下軌' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kline_pattern && (
                <HoverPopover
                  title="K線形態（區域性結構）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        形態來自對最近1-2根K線的形狀識別（如十字星、錘子線、吞沒等），屬於“區域性訊號”。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">代表什麼：</span>
                        多數形態需要結合趨勢、量能與關鍵位確認。比如錘子線出現在下跌末端更有意義；吞沒形態更看重“前後兩根K線對比”。
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">當前：{effectiveSummary.kline_pattern}</div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：單根K線形態誤判率較高，建議僅作提示，不建議孤立決策。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.kline_pattern} tone="warning" help />
                  }
                />
              )}
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary && effectiveSummary.support != null && (
                <HoverPopover
                  title="支撐位（關鍵支撐區）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        支撐位可以理解為“買盤更容易出現”的價格區域。接近支撐時，價格更可能出現止跌、反彈或盤整。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">本專案如何算：</span>
                        當前彈跳視窗裡的支撐（support）來自最近20個交易日區間內的最低價（min low），屬於中期級別的參考位。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">怎麼用：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>更偏向“區域”而不是精確到1分錢的一條線，常見做法是允許一定誤差（例如±1%~2%）。</li>
                          <li>接近支撐時，若出現縮量止跌/放量反彈，訊號通常更可靠；若放量跌破，則支撐可能失效並轉為壓力。</li>
                          <li>適合用於設定停損/停利/加減碼區間：用關鍵位去約束風險，而不是預測最高點最低點。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>當前：支撐≈{effectiveSummary.support.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.support > 0 && (
                          <div>
                            距離（以收盤價計）≈{(((effectiveSummary.last_close - effectiveSummary.support) / effectiveSummary.support) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close <= effectiveSummary.support * 1.02 ? '（接近支撐，評分規則會加分）' : ''}
                          </div>
                        )}
                        {(effectiveSummary.support_s != null || effectiveSummary.support_m != null || effectiveSummary.support_l != null) && (
                          <div>
                            多級別：短期(5日)≈{effectiveSummary.support_s != null ? effectiveSummary.support_s.toFixed(2) : '—'}；中期(20日)≈{effectiveSummary.support_m != null ? effectiveSummary.support_m.toFixed(2) : '—'}；長期(60日)≈{effectiveSummary.support_l != null ? effectiveSummary.support_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：支撐/壓力是“統計出的關鍵位”，不是必然會反轉的點位；趨勢很強時可直接擊穿。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`支撐 ${effectiveSummary.support.toFixed(2)}`}
                      tone="bearish"
                      help
                    />
                  }
                />
              )}
              {effectiveSummary && effectiveSummary.resistance != null && (
                <HoverPopover
                  title="壓力位（關鍵壓力區）"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">是什麼：</span>
                        壓力位可以理解為“賣盤更容易出現”的價格區域。接近壓力時，上行更容易受阻、回落或進入震盪。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">本專案如何算：</span>
                        當前彈跳視窗裡的壓力（resistance）來自最近20個交易日區間內的最高價（max high），屬於中期級別的參考位。
                      </div>
                      <div>
                        <span className="font-medium text-foreground">怎麼用：</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>越接近壓力，追漲的價效比越低；更常見的策略是等“放量突破後回踩不破”再考慮。</li>
                          <li>若放量突破壓力並站穩，原壓力往往會“角色互換”變成新的支撐。</li>
                          <li>壓力附近可用來規劃分批停利/減碼，或觀察是否出現量價背離、衝高回落等風險訊號。</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>當前：壓力≈{effectiveSummary.resistance.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.resistance > 0 && (
                          <div>
                            距離（以收盤價計）≈{(((effectiveSummary.resistance - effectiveSummary.last_close) / effectiveSummary.resistance) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close >= effectiveSummary.resistance * 0.98 ? '（接近壓力，評分規則會扣分）' : ''}
                          </div>
                        )}
                        {(effectiveSummary.resistance_s != null || effectiveSummary.resistance_m != null || effectiveSummary.resistance_l != null) && (
                          <div>
                            多級別：短期(5日)≈{effectiveSummary.resistance_s != null ? effectiveSummary.resistance_s.toFixed(2) : '—'}；中期(20日)≈{effectiveSummary.resistance_m != null ? effectiveSummary.resistance_m.toFixed(2) : '—'}；長期(60日)≈{effectiveSummary.resistance_l != null ? effectiveSummary.resistance_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        注意：突破是否有效，往往取決於“是否放量 + 是否能站穩/回踩確認”。單靠刺穿一瞬間容易假突破。
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`壓力 ${effectiveSummary.resistance.toFixed(2)}`}
                      tone="bullish"
                      help
                    />
                  }
                />
              )}
            </div>

            {(effectiveSummary.change_5d != null || effectiveSummary.change_20d != null || effectiveSummary.amplitude != null) && (
              <div className="flex gap-4 text-[11px] text-muted-foreground">
                {effectiveSummary.change_5d != null && (
                  <HoverPopover
                    title="5日漲跌幅（短期動量）"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">是什麼：</span>
                          5日漲跌幅表示最近5個交易日的整體報酬率，用來快速觀察短期動量強弱。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">本專案如何算：</span>
                          使用“今日收盤”對比“5個交易日前收盤”的變化：（Close[t]-Close[t-5]) / Close[t-5]。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">怎麼解讀：</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>正值：短期偏強；配合放量/多頭趨勢時，更可能延續。</li>
                            <li>負值：短期偏弱；若同時均線空頭、MACD死叉，風險更大。</li>
                            <li>過大的正漲幅也可能意味著“短期過熱”，要防回檔；更建議結合支撐/壓力位設定風控。</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          當前：{effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        5日{' '}
                        <span className={effectiveSummary.change_5d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.change_20d != null && (
                  <HoverPopover
                    title="20日漲跌幅（波段/一月動量）"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">是什麼：</span>
                          20日漲跌幅接近一個交易月的整體報酬率，更偏向“波段趨勢”的表現。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">本專案如何算：</span>
                          使用“今日收盤”對比“20個交易日前收盤”的變化：（Close[t]-Close[t-20]) / Close[t-20]。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">怎麼解讀：</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>正值且趨勢多頭：通常更偏順勢；回撥時更關注支撐位與成交量。</li>
                            <li>負值且趨勢空頭：通常更偏逆風；反彈到壓力位附近更容易受阻。</li>
                            <li>5日與20日分歧：可能代表“短期反彈/回撥”發生在更大的趨勢裡，需謹慎辨別是否反轉。</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          當前：{effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        20日{' '}
                        <span className={effectiveSummary.change_20d >= 0 ? 'text-rose-500' : 'text-emerald-500'}>
                          {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.amplitude != null && (
                  <HoverPopover
                    title="振幅（波動強度）"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">是什麼：</span>
                          振幅描述當天高低價之間的波動範圍，用來衡量“波動有多大”。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">本專案如何算：</span>
                          今日振幅≈(High-Low)/Low。數值越大，表示盤中波動越劇烈、風險與機會都更大。
                        </div>
                        <div>
                          <span className="font-medium text-foreground">怎麼解讀：</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>高振幅常見於放量突破、恐慌下跌、訊息驅動等；需要配合量能和趨勢判斷“是擴張還是崩盤”。</li>
                            <li>低振幅常見於盤整/收斂期；若布林帶同時收口，後續更可能出現方向選擇。</li>
                            <li>振幅高時更建議降低倉位/更嚴格停損；避免用“同一套停損距離”應對不同波動。</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70 space-y-1">
                          <div>當前：{effectiveSummary.amplitude.toFixed(2)}%</div>
                          {effectiveSummary.amplitude_avg5 != null && (
                            <div>近5日均值：{effectiveSummary.amplitude_avg5.toFixed(2)}%</div>
                          )}
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        振幅: {effectiveSummary.amplitude.toFixed(2)}%
                      </span>
                    }
                  />
                )}
              </div>
            )}

            <details className="group">
              <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                建議/評分規則說明 <span className="text-[10px]">(點選展開)</span>
              </summary>
              <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 space-y-2">
                <div className="font-medium text-foreground">建議規則（按是否持倉）</div>
                <div className="space-y-1">
                  <div>未持倉：score ≥ 3 → 買入；score ≤ -2 → 迴避；其他 → 觀望</div>
                  <div>已持倉：score ≥ 3 → 加碼；score ≥ 1 → 持有；score ≤ -3 → 賣出；score ≤ -1 → 減碼；其他 → 觀望</div>
                </div>
                <div className="font-medium text-foreground">評分規則（各項累加，0 為中性）</div>
                <div className="space-y-1">
                  <div>趨勢（均線）：多頭排列 +2；空頭排列 -2</div>
                  <div>MACD：金叉 +2；死叉 -2；柱體為正 +1；柱體為負 -1</div>
                  <div>RSI：超賣 +1；偏強 +1；超買 -1；偏弱 -1</div>
                  <div>KDJ：金叉 +1；死叉 -1</div>
                  <div>布林：突破上軌 +1；跌破下軌 -1</div>
                  <div>量能：放量 +1；縮量 -1</div>
                  <div>支撐/壓力：收盤價 ≤ 支撐×1.02 → +1；收盤價 ≥ 壓力×0.98 → -1</div>
                </div>
              </div>
            </details>

            <Button variant="secondary" size="sm" className="w-full mt-1" onClick={handleAskAI}>
              <Sparkles className="w-3.5 h-3.5 mr-1" /> 問 AI 分析這些指標
            </Button>

          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
