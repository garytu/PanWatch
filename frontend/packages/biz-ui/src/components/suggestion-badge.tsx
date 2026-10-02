import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { KlineSummaryDialog } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { fetchAPI } from '@panwatch/api'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { AiSuggestionBadge } from '@panwatch/biz-ui/components/ai-suggestion-badge'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'

export interface SuggestionInfo {
  id?: number
  action: string  // buy/add/reduce/sell/hold/watch
  action_label: string
  signal: string
  reason: string
  should_alert: boolean
  raw?: string
  // 建議池新增欄位
  agent_name?: string     // intraday_monitor/daily_report/premarket_outlook
  agent_label?: string    // 盤中監測/盤後日報/盤前分析
  created_at?: string     // ISO 時間戳
  is_expired?: boolean    // 是否已過期
  prompt_context?: string // Prompt 上下文
  ai_response?: string    // AI 原始回應
  meta?: Record<string, any>
}

export interface KlineSummary {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  trend: string
  macd_status: string
  macd_cross?: string
  macd_cross_days?: number
  recent_5_up: number
  change_5d: number | null
  change_20d: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60?: number | null
  // RSI
  rsi6?: number | null
  rsi_status?: string
  // KDJ
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  // 布林帶
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_status?: string
  // 量能
  volume_ratio?: number | null
  volume_trend?: string
  // 振幅
  amplitude?: number | null
  // 多級支撐壓力
  support: number | null
  resistance: number | null
  support_s?: number | null
  support_m?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  // K線形態
  kline_pattern?: string
}

interface SuggestionBadgeProps {
  suggestion: SuggestionInfo | null
  stockName?: string
  stockSymbol?: string
  kline?: KlineSummary | null
  showFullInline?: boolean  // 是否在行內顯示完整資訊（Dashboard 模式）
  market?: string           // 市場（用於技術指標彈跳視窗）
  hasPosition?: boolean     // 是否持倉（用於技術指標彈跳視窗）
  showTechnicalCompanion?: boolean // 是否展示技術指標對照徽章
}

// 格式化建議時間（自動轉換為本地時區，只顯示時:分）
function formatSuggestionTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    // 檢查日期是否有效
    if (isNaN(date.getTime())) return ''
    // 使用本地時區顯示
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

// 格式化完整日期時間（本地時區）
function formatSuggestionDateTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

function formatKlineMeta(meta?: Record<string, any>): string {
  if (!meta) return ''
  const computedAt = meta?.kline_meta?.computed_at
  const asof = meta?.kline_meta?.asof
  const parts: string[] = []
  if (asof) parts.push(`K線截止 ${asof}`)
  if (computedAt) parts.push(`計算 ${formatSuggestionTime(computedAt)}`)
  return parts.join(' · ')
}

export function SuggestionBadge({
  suggestion,
  stockName,
  stockSymbol,
  kline,
  showFullInline = false,
  market = 'CN',
  hasPosition = false,
  showTechnicalCompanion = true,
}: SuggestionBadgeProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)
  const [feedback, setFeedback] = useState<'useful' | 'useless' | null>(null)
  const { toast } = useToast()

  useEffect(() => {
    setFeedback(null)
  }, [suggestion?.id])

  const canFeedback = !!suggestion?.id && suggestion?.agent_label !== '技術指標'
  const submitFeedback = async (useful: boolean) => {
    if (!suggestion?.id) return
    try {
      await fetchAPI('/feedback', {
        method: 'POST',
        body: JSON.stringify({ suggestion_id: suggestion.id, useful }),
      })
      setFeedback(useful ? 'useful' : 'useless')
      toast('回饋已提交', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '回饋失敗', 'error')
    }
  }

  const onDialogOpenChange = (open: boolean) => {
    setDialogOpen(open)
    if (!open) {
      try {
        ;(window as any).__panwatch_suppress_card_click_until = Date.now() + 600
      } catch {
        // ignore
      }
    }
  }

  if (!suggestion && !kline) return null

  // Dashboard 模式：行內顯示完整資訊（僅建議 badge）
  if (showFullInline) {
    if (!suggestion) return null
    const isAI = !!suggestion.agent_name && suggestion.agent_label !== '技術指標'
    const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
    const timeStr = formatSuggestionTime(suggestion.created_at)
    const klineMetaStr = formatKlineMeta(suggestion.meta)
    return (
      <>
        <div className="pt-3 border-t border-border/30">
          <div className="flex items-start gap-3">
            <div className="shrink-0 flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="lg"
                onClick={(e) => {
                  e.stopPropagation()
                  if (suggestion.agent_label === '技術指標') setKlineDialogOpen(true)
                  else setDialogOpen(true)
                }}
                title="點選檢視建議詳細資訊"
              />
              {isAI && showTechnicalCompanion && (
                <TechnicalBadge
                  label={tech ? tech.action_label : '觀望'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="lg"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="點選檢視技術面詳細資訊"
                />
              )}
            </div>
            <div className="flex-1 min-w-0">
              {suggestion.signal && (
                <p className="text-[12px] font-medium text-foreground mb-0.5">{suggestion.signal}</p>
              )}
              {suggestion.reason ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.reason}</p>
              ) : suggestion.raw && !suggestion.signal ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.raw}</p>
              ) : null}

              {(suggestion.agent_label || timeStr) && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  來源: {suggestion.agent_label || (isAI ? 'AI' : '未知')}
                  {timeStr && ` · ${timeStr}`}
                  {suggestion.is_expired && <span className="ml-1 text-amber-600">(已過期)</span>}
                </div>
              )}

              {klineMetaStr && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  {klineMetaStr}
                </div>
              )}
            </div>
          </div>
        </div>

        <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
          <DialogContent
            className="max-w-md"
            onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <AiSuggestionBadge
                  action={suggestion.action}
                  actionLabel={suggestion.action_label}
                  isAI={isAI}
                  isExpired={!!suggestion.is_expired}
                  size="lg"
                />
                {/* AI 標籤已前置到按鈕文案，不再重複 */}
                {stockName && (
                  <span className="text-[14px] font-normal text-muted-foreground">
                    {stockName} {stockSymbol && `(${stockSymbol})`}
                  </span>
                )}
              </DialogTitle>
              {/* 來源資訊 */}
              {(suggestion.agent_label || suggestion.created_at) && (
                <div className="text-[11px] text-muted-foreground/70 mt-1">
                  來源: {suggestion.agent_label || '未知'}
                  {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                  {suggestion.is_expired && <span className="ml-2 text-amber-500">(已過期)</span>}
                </div>
              )}
            </DialogHeader>

            <div className="space-y-4">
              {/* Feedback */}
              {canFeedback && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">這條建議是否有用？</div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => submitFeedback(true)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useful'
                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      有用
                    </button>
                    <button
                      onClick={() => submitFeedback(false)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useless'
                          ? 'bg-rose-500/10 border-rose-500/30 text-rose-700'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      沒用
                    </button>
                    {feedback && (
                      <span className="text-[11px] text-muted-foreground">已記錄，感謝回饋</span>
                    )}
                  </div>
                </div>
              )}

              {/* 訊號 */}
              {suggestion.signal && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">訊號</div>
                  <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
                </div>
              )}

              {/* 理由 */}
              {(suggestion.reason || suggestion.raw) && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                  <p className="text-[13px] text-foreground">
                    {suggestion.reason || suggestion.raw}
                  </p>
                </div>
              )}

              {/* 技術指標 */}
              {kline && (
                <div className="space-y-3">
                  <div className="text-[11px] text-muted-foreground">技術指標</div>
                  <KlineIndicators summary={kline as any} />
                </div>
              )}

              {/* AI 原始回應 */}
              {suggestion.ai_response && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">AI 回應</div>
                  <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto scrollbar">
                    {suggestion.ai_response}
                  </div>
                </div>
              )}

              {/* Prompt 上下文 */}
              {suggestion.prompt_context && (
                <details className="group">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                    Prompt 上下文 <span className="text-[10px]">(點選展開)</span>
                  </summary>
                  <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto scrollbar">
                    {suggestion.prompt_context}
                  </div>
                </details>
              )}
            </div>
          </DialogContent>
        </Dialog>
        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  // 僅展示技術指標（無建議）
  if (!suggestion && kline) {
    return (
      <>
        <div className="inline-flex flex-col items-start gap-0.5">
          <TechnicalBadge
            label="指標"
            tone="neutral"
            size="xs"
            onClick={(e) => {
              e.stopPropagation()
              setKlineDialogOpen(true)
            }}
            title="點選檢視技術指標"
          />
        </div>

        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market || 'CN'}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  if (!suggestion) return null
  const isAI = !!suggestion.agent_name && suggestion.agent_label !== '技術指標'

  // 持倉頁模式：小徽章 + 點選彈跳視窗
  const timeStr = formatSuggestionTime(suggestion.created_at)
  const sourceInfo = ''

  return (
    <>
      <div className="inline-flex flex-col items-start gap-0.5">
        <div className="inline-flex items-center gap-1">
          <AiSuggestionBadge
            action={suggestion.action}
            actionLabel={suggestion.action_label}
            isAI={isAI}
            isExpired={!!suggestion.is_expired}
            size="md"
            onClick={(e) => {
              e.stopPropagation()
              if (suggestion.agent_label === '技術指標') setKlineDialogOpen(true)
              else setDialogOpen(true)
            }}
            title={sourceInfo ? `${sourceInfo} - 點選檢視詳細資訊` : '點選檢視建議詳細資訊'}
          />
          {showTechnicalCompanion && suggestion.agent_label !== '技術指標' && (
            (() => {
              const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
              return (
                <TechnicalBadge
                  label={tech ? tech.action_label : '觀望'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="md"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="點選檢視技術面詳細資訊"
                />
              )
            })()
          )}
        </div>
        {/* 來源和時間（顯示在徽章下方，僅 AI 建議以增強區分）*/}
        {isAI && (
          <div className="mt-1 text-[10px] text-muted-foreground/70">
            來源: {suggestion.agent_label || 'AI'}{timeStr && ` · ${timeStr}`}
            {suggestion.is_expired && <span className="ml-1 text-amber-600">(已過期)</span>}
          </div>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
        <DialogContent
          className="max-w-md"
          onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onClick={(e) => e.stopPropagation()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="md"
              />
              {stockName && (
                <span className="text-[14px] font-normal text-muted-foreground">
                  {stockName} {stockSymbol && `(${stockSymbol})`}
                </span>
              )}
            </DialogTitle>
            {/* 來源資訊 */}
            {(suggestion.agent_label || suggestion.created_at) && (
              <div className="text-[11px] text-muted-foreground/70 mt-1">
                來源: {suggestion.agent_label || '未知'}
                {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                {suggestion.is_expired && <span className="ml-2 text-amber-500">(已過期)</span>}
              </div>
            )}
          </DialogHeader>

          <div className="space-y-4">
            {/* Feedback */}
            {canFeedback && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">這條建議是否有用？</div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => submitFeedback(true)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useful'
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    有用
                  </button>
                  <button
                    onClick={() => submitFeedback(false)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useless'
                        ? 'bg-rose-500/10 border-rose-500/30 text-rose-700'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    沒用
                  </button>
                  {feedback && (
                    <span className="text-[11px] text-muted-foreground">已記錄，感謝回饋</span>
                  )}
                </div>
              </div>
            )}

            {/* 訊號 */}
            {suggestion.signal && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">訊號</div>
                <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
              </div>
            )}

            {/* 理由 */}
            {(suggestion.reason || suggestion.raw) && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">理由</div>
                <p className="text-[13px] text-foreground">
                  {suggestion.reason || suggestion.raw}
                </p>
              </div>
            )}

            {/* 技術指標 */}
            {kline && (
              <div className="space-y-3">
                <div className="text-[11px] text-muted-foreground">技術指標</div>
                <KlineIndicators summary={kline as any} />
              </div>
            )}

            {/* AI 原始回應 */}
            {suggestion.ai_response && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">AI 回應</div>
                <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto">
                  {suggestion.ai_response}
                </div>
              </div>
            )}

            {/* Prompt 上下文 */}
            {suggestion.prompt_context && (
              <details className="group">
                <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                  Prompt 上下文 <span className="text-[10px]">(點選展開)</span>
                </summary>
                <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto">
                  {suggestion.prompt_context}
                </div>
              </details>
            )}
          </div>
        </DialogContent>
      </Dialog>
      {/* Always mount K-line dialog for technical details */}
      <KlineSummaryDialog
        open={klineDialogOpen}
        onOpenChange={setKlineDialogOpen}
        symbol={stockSymbol || ''}
        market={market}
        stockName={stockName}
        hasPosition={hasPosition}
        initialSummary={kline as any}
      />
    </>
  )
}
