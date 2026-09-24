/**
 * 深度分析彈跳視窗(TradingAgents)。
 *
 * 三種狀態:
 * 1. 觸發中 — 顯示「分析需 3-5 分鐘,確認開始?」+ 成本預估
 * 2. 執行中 — polling /agents/runs/{trace_id}/progress,顯示階段進度
 * 3. 完成 — 頂層摘要 + Markdown 推理 + 可展開 4 分析師報告 + 辯論
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { buildAnalysisSections, type AnalysisSection } from '../analysis-sections'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@panwatch/base-ui/components/ui/tabs'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import {
  subscribeSSE,
  tradingAgentsApi,
  type BudgetInfo,
  type DeepAnalysisResult,
  type ProgressResponse,
  type ProgressDataSource,
  type ProgressStage,
} from '@panwatch/api'
import {
  isTerminalProgressStatus,
  shouldContinueProgressWatch,
} from '../../../../src/lib/tradingagents-progress'

const STAGE_LABEL: Record<string, string> = {
  data_collection: '資料準備',
  market_analyst: '技術分析師',
  social_analyst: '情緒分析師',
  news_analyst: '新聞分析師',
  fundamentals_analyst: '基本面分析師',
  bull_bear_debate: '看多看空辯論',
  research_manager: '研究主管',
  trader: '交易員決策',
  risk_judge: '風控判定',
  final_decision: 'PM 整合',
}

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const POLL_INTERVAL_MS = 2000

/** localStorage 裡記錄某隻股票最近一次觸發的 trace_id;關閉重開彈跳視窗時恢復 polling */
const STORAGE_KEY_PREFIX = 'panwatch:tradingagents:running:'
/** trace_id 持續多久後認為可能已不再執行(避免顯示過期 trace 的 idle) */
const TRACE_MAX_AGE_MS = 60 * 60 * 1000  // 與後端 running 生命週期視窗保持一致並留出恢復餘量

function loadRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > TRACE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      STORAGE_KEY_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* 忽略 quota 等錯誤 */
  }
}

function clearRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

export interface DeepAnalysisModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: number
  stockName: string
  stockSymbol: string
  /** 歷史分析(若有,直接展示) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const { toast } = useToast()
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const [budget, setBudget] = useState<BudgetInfo | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // SSE 訂閱取消函式(進度優先走 SSE,失敗降級 polling)
  const sseCloseRef = useRef<(() => void) | null>(null)

  /** 停止一切進度監聽(SSE + polling) */
  const stopWatching = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (sseCloseRef.current) {
      sseCloseRef.current()
      sseCloseRef.current = null
    }
  }, [])

  // 彈跳視窗關閉時清理進度監聽
  useEffect(() => {
    if (!open) stopWatching()
  }, [open, stopWatching])

  // 重置初始狀態 + 後端查詢是否有正在跑/已完成的任務
  useEffect(() => {
    if (!open) return

    if (initialResult) {
      setResult(initialResult)
      setStage('done')
      return
    }

    // 先重置為 idle (避免上次 state 殘留),然後非同步查後端
    setStage('idle')
    setResult(null)
    setError('')
    setProgress(null)
    setTraceId(null)

    // 併發查 3 個資料:
    //   - findRunning:這隻股票最近 30 分鐘有沒有執行中的任務
    //   - getLatestForStock:有沒有當日已完成的結果(過 30 分鐘也算)
    //   - getBudget:本月預算(idle 狀態展示)
    // 優先順序:running > done(已有結果)> idle
    Promise.all([
      tradingAgentsApi.findRunning(stockSymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getLatestForStock(stockSymbol).catch(() => null),
      tradingAgentsApi.getBudget().catch(() => null),
    ]).then(([runningInfo, latestResult, budgetInfo]) => {
      setBudget(budgetInfo)

      // 優先順序:running(真在跑) > done(當日快取,允許重新分析) > idle
      //   - stale / failed / success / none 都視為"不在跑"
      //   - 任何狀態下,只要有當日快取就展示 DoneView(含「忽略快取重新分析」按鈕)
      //   - 任何狀態下,IdleView 的「開始分析」按鈕永遠可用,後端會做冪等去重

      // 1) 真正在跑(後端權威源)→ 進入 running
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        const tid = runningInfo.trace_id
        setTraceId(tid)
        setStage('running')
        // 後端確認在跑；即使採集階段暫時沒有日誌，也繼續由 SSE/polling 接力
        tradingAgentsApi.getProgress(tid).then(resp => setProgress(resp))
        startWatching(tid)
        return
      }

      // 2) 後端 stale/failed → 老任務死掉/失敗,清掉本地痕跡,繼續走快取判斷
      //    不再回到 running,允許使用者重新觸發
      if (runningInfo.status === 'stale' || runningInfo.status === 'failed') {
        clearRunningTrace(stockSymbol)
      }

      // 3) localStorage 兜底(剛觸發後端還沒寫 log)— 僅在後端 'none' 時嘗試
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(stockSymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          tradingAgentsApi.getProgress(localTrace).then(resp => setProgress(resp))
          startWatching(localTrace)
          return
        }
      }

      // 4) 有當日已完成結果 → done 檢視(使用者可點「忽略快取重新分析」)
      if (latestResult) {
        latestResult.raw_data.from_cache = true
        setResult(latestResult)
        setStage('done')
        clearRunningTrace(stockSymbol)
        return
      }

      // 5) 都沒有 → idle(開始分析按鈕可用,後端冪等保護)
      clearRunningTrace(stockSymbol)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialResult, stockSymbol])

  /** 處理一次進度快照(SSE 推送與輪詢共用同一套狀態機) */
  const handleProgressResponse = useCallback(
    async (resp: ProgressResponse) => {
      setProgress(resp)
      if (resp.status === 'success') {
        // 完成,拉歷史結果
        stopWatching()
        clearRunningTrace(stockSymbol)
        const latest = await tradingAgentsApi.getLatestForStock(stockSymbol)
        if (latest) {
          setResult(latest)
          setStage('done')
        } else {
          setError('結果未落庫,請稍後到「AI 歷史」檢視')
          setStage('error')
        }
      } else if (resp.status === 'failed') {
        stopWatching()
        clearRunningTrace(stockSymbol)
        setError(resp.run?.error || '分析失敗')
        setStage('error')
      } else if (resp.status === 'stale') {
        // 後端檢測到殭屍 running（超過整個任務生命週期視窗）
        // → 自動重置到 idle,使用者可以重新觸發
        stopWatching()
        clearRunningTrace(stockSymbol)
        setTraceId('')
        setProgress(null)
        setStage('idle')
      } else if (resp.status === 'not_found') {
        // SSE/輪詢暫時沒有快照不等於任務不存在；後端 running 記錄可能還在採集。
        // 保留 trace，讓下一輪 polling 或重新整理頁面繼續接管。
        return
      }
    },
    [stockSymbol, stopWatching],
  )

  const pollProgress = useCallback(
    async (tid: string) => {
      try {
        const resp = await tradingAgentsApi.getProgress(tid)
        await handleProgressResponse(resp)
      } catch (e) {
        // polling 失敗不立即終止,記一次錯誤
        console.warn('progress poll error:', e)
      }
    },
    [handleProgressResponse],
  )

  /** 降級方案:setInterval 輪詢(SSE 不可用時) */
  const startPolling = useCallback(
    (tid: string) => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = setInterval(() => pollProgress(tid), POLL_INTERVAL_MS)
      void pollProgress(tid)
    },
    [pollProgress],
  )

  /** 開始監聽進度:優先 SSE(伺服器端推送),失敗/關流降級輪詢(輪詢程式碼保留兜底) */
  const startWatching = useCallback(
    (tid: string) => {
      stopWatching()
      let terminal = false
      sseCloseRef.current = subscribeSSE(`/agents/runs/${tid}/progress/stream`, {
        onEvent: (ev) => {
          if (ev.event === 'progress' && ev.data && typeof ev.data === 'object') {
            const resp = ev.data as ProgressResponse
            if (isTerminalProgressStatus(resp.status)) terminal = true
            void handleProgressResponse(resp)
          } else if (ev.event === 'done' && ev.data?.status && ev.data.status !== 'timeout') {
            terminal = !shouldContinueProgressWatch(ev.data.status, 'done')
            // done 事件只攜帶狀態，不帶完整 run/result；終態也要補拉一次快照，
            // 避免最後一條 progress 被代理丟棄時彈跳視窗停在 running。
            if (terminal) void pollProgress(tid)
          }
        },
        onClosed: () => {
          // 伺服器端正常關流:終態則結束;非終態(如流超時)降級輪詢接力
          if (!terminal) startPolling(tid)
        },
        onFailed: () => {
          // SSE 不可用(舊代理緩衝/網路問題)→ 降級輪詢
          startPolling(tid)
        },
      })
    },
    [handleProgressResponse, pollProgress, startPolling, stopWatching],
  )

  const handleStart = useCallback(async (force = false) => {
    setStage('running')
    setError('')
    setProgress(null)
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force })
      const tid = triggerResp.trace_id || ''
      setTraceId(tid)
      if (!tid) {
        // 後端未返回 trace_id,只顯示 message
        setStage('done')
        toast(triggerResp.message || '已觸發', 'success')
        return
      }
      // 持久化 trace_id 讓關閉重開能恢復進度
      saveRunningTrace(stockSymbol, tid)
      // 啟動進度監聽(SSE 優先,失敗降級輪詢)
      startWatching(tid)
      // 立即拉一次,儘快渲染初始進度
      pollProgress(tid)
    } catch (e) {
      setStage('error')
      setError(e instanceof Error ? e.message : '觸發失敗')
    }
  }, [stockId, stockSymbol, startWatching, pollProgress, toast])

  const handleClose = useCallback(() => {
    stopWatching()
    onOpenChange(false)
  }, [onOpenChange, stopWatching])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-6xl max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            🧠 深度分析 · {stockName} ({stockSymbol})
          </DialogTitle>
          <DialogDescription>
            TradingAgents 多 Agent 決策框架 · 僅供學習研究參考,不構成投資建議
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            budget={budget}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          stockSymbol={stockSymbol}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">分析失敗</div>
              <div className="text-[12px]">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>關閉</Button>
              <Button onClick={() => handleStart(false)}>重試</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  budget,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  budget: BudgetInfo | null
  onStart: () => void
  onCancel: () => void
}) {
  const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
  const est = budget?.estimate_next_run
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">即將分析:{stockSymbol}</div>
        <div className="text-muted-foreground">
          呼叫 4 類分析師(技術 / 情緒 / 新聞 / 基本面) + 看多看空辯論 + 風控 + PM 整合
        </div>
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>⏱ 預計耗時:3-8 分鐘</div>
          {est ? (
            <div>💰 預估成本:${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
          ) : (
            <div>💰 預估成本:載入中...</div>
          )}
          <div>ℹ️ 非同步執行,可關閉彈跳視窗,完成時透過通知管道推送</div>
        </div>
      </div>

      {/* 本月預算 */}
      {budget && (
        <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
          <div className="flex items-center justify-between">
            <span className="font-medium">本月預算</span>
            <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
              ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
              {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} 次`}
            </span>
          </div>
          {overBudget && (
            <div className="text-[11px] text-rose-600 mt-1">
              ⚠️ 本月預算已用盡。如需繼續,請到「設定 → Agent → TradingAgents」調高 `monthly_budget_usd`。
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>取消</Button>
        <Button onClick={onStart} disabled={overBudget}>開始分析</Button>
      </div>
    </div>
  )
}

function RunningView({
  progress,
  traceId,
  onClose,
}: {
  progress: ProgressResponse | null
  traceId: string
  onClose: () => void
}) {
  const elapsed = progress?.elapsed_sec ?? 0
  const cost = progress?.total_cost_usd ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">分析進行中...</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            已用 {formatElapsed(elapsed)} · ${cost.toFixed(4)}
          </span>
        </div>
        {progress?.active_operation && (
          <div className="text-[11px] text-muted-foreground">
            {progress.active_operation.agent && (
              <>
                當前 Agent：<span className="font-mono">{progress.active_operation.agent}</span> ·{' '}
              </>
            )}
            當前操作：{progress.active_operation.kind === 'tool' ? '資料工具 ' : ''}
            <span className="font-mono">{progress.active_operation.name}</span>
          </div>
        )}
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">準備中...</div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground/70 mt-3 font-mono">
          trace_id: {traceId.slice(0, 16)}...
        </div>
      </div>

      <ToolkitDiagnostics
        summary={progress?.toolkit_summary}
        recent={progress?.toolkit_recent || []}
      />

      {progress?.data_sources && progress.data_sources.length > 0 && (
        <DataCollectionDiagnostics sources={progress.data_sources} />
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          後臺執行 (完成時推送通知)
        </Button>
      </div>
    </div>
  )
}

function DataCollectionDiagnostics({ sources }: { sources: ProgressDataSource[] }) {
  const labels: Record<string, string> = {
    quote: '行情',
    klines: 'K 線',
    capital_flow: '資金流',
    events: '事件',
    financial: '財報',
    technical: '技術指標',
  }
  const statusLabels: Record<ProgressDataSource['status'], string> = {
    pending: '等待',
    running: '請求中',
    done: '完成',
    error: '失敗降級',
  }
  const statusClasses: Record<ProgressDataSource['status'], string> = {
    pending: 'text-muted-foreground',
    running: 'text-sky-600 dark:text-sky-400',
    done: 'text-emerald-600 dark:text-emerald-400',
    error: 'text-amber-600 dark:text-amber-400',
  }

  return (
    <div className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]">
      <div className="font-medium mb-1">資料準備明細</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {sources.map((source) => (
          <span key={source.name} className={statusClasses[source.status]} title={source.error}>
            {labels[source.name] || source.name}: {statusLabels[source.status]}
          </span>
        ))}
      </div>
    </div>
  )
}

interface ToolkitDiagItem {
  action?: string
  method?: string
  symbol?: string
  chars?: number
  snippet?: string
  source?: string
  reason?: string
}
interface ToolkitDiagSummary {
  hit: number
  miss: number
  passthrough: number
  fallthrough?: number
  error: number
}

export function ToolkitDiagnostics({
  summary,
  recent,
  defaultOpen = false,
}: {
  summary: ToolkitDiagSummary | undefined
  recent: ToolkitDiagItem[]
  defaultOpen?: boolean
}) {
  if (!summary && recent.length === 0) return null

  const hit = summary?.hit ?? 0
  const miss = summary?.miss ?? 0
  const pass = summary?.passthrough ?? 0
  const fall = summary?.fallthrough ?? 0
  const err = summary?.error ?? 0
  const total = hit + miss + pass + fall + err

  const ACTION_CLS: Record<string, string> = {
    HIT: 'text-emerald-600 dark:text-emerald-400',
    MISS: 'text-amber-600 dark:text-amber-400',
    PASSTHROUGH: 'text-sky-600 dark:text-sky-400',
    FALLTHROUGH: 'text-orange-600 dark:text-orange-400',
    ERROR: 'text-rose-600',
  }

  return (
    <details className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]" open={defaultOpen}>
      <summary className="cursor-pointer flex items-center gap-2 flex-wrap">
        <span className="font-medium">資料注入診斷</span>
        <span className="text-[11px] text-muted-foreground">
          (PanWatch 資料 → TradingAgents 工具)
        </span>
        <span className="ml-auto text-[11px] whitespace-nowrap">
          <span className={ACTION_CLS.HIT}>HIT {hit}</span>
          <span className="text-muted-foreground"> · MISS {miss}</span>
          <span className={ACTION_CLS.PASSTHROUGH}> · 透傳 {pass}</span>
          {fall > 0 && <span className={ACTION_CLS.FALLTHROUGH}> · 兜底 {fall}</span>}
          {err > 0 && <span className="text-rose-600"> · 錯誤 {err}</span>}
        </span>
      </summary>
      <div className="text-[10.5px] text-muted-foreground/80 mt-2 leading-relaxed">
        <span className={ACTION_CLS.HIT}>HIT</span>: 用 PanWatch 資料 ·{' '}
        <span className={ACTION_CLS.MISS}>MISS</span>: 命中但 PanWatch 未實現 ·{' '}
        <span className={ACTION_CLS.PASSTHROUGH}>透傳</span>: 非 A 股直接走上游 vendor ·{' '}
        <span className={ACTION_CLS.FALLTHROUGH}>兜底</span>: A 股但 cache 為空,走了上游
      </div>
      {total === 0 ? (
        <div className="text-[11px] text-muted-foreground mt-2">
          ⚠️ 還沒有任何工具呼叫記錄(可能 TradingAgents 還在準備階段)。
        </div>
      ) : (
        <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {recent.map((h, i) => {
            const action = (h.action || '').toUpperCase()
            const row = (
              <div className="font-mono text-[10.5px] flex items-center gap-2 hover:bg-accent/30 px-1 rounded cursor-help w-full">
                <span className={`${ACTION_CLS[action] || 'text-muted-foreground'} w-20 shrink-0`}>
                  {action}
                </span>
                <span className="text-foreground/80 truncate flex-1 text-left">
                  {h.method} ({h.symbol || '-'})
                  {h.reason && <span className="text-muted-foreground"> · {h.reason}</span>}
                  {h.chars != null && <span className="text-muted-foreground"> · {h.chars} 字元</span>}
                  {h.source && <span className="text-muted-foreground/70"> · {h.source}</span>}
                </span>
              </div>
            )
            const hasDetail = !!(h.snippet || h.reason)
            if (!hasDetail) return <div key={i}>{row}</div>
            return (
              <HoverPopover
                key={i}
                className="block w-full"
                trigger={row}
                title={
                  <span>
                    <span className={ACTION_CLS[action] || 'text-muted-foreground'}>{action}</span>
                    <span className="text-muted-foreground"> · {h.method}({h.symbol || '-'})</span>
                    {h.source && (
                      <span className="text-muted-foreground/70"> · {h.source}</span>
                    )}
                  </span>
                }
                content={
                  <div className="space-y-2">
                    {h.reason && (
                      <div className="text-[11px] text-amber-600 dark:text-amber-400">
                        {h.reason}
                      </div>
                    )}
                    {h.snippet && (
                      <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug bg-accent/30 rounded p-2 text-foreground/85 max-h-[60vh] overflow-y-auto">
                        {h.snippet}
                        {h.chars != null && h.chars > h.snippet.length && (
                          <span className="text-muted-foreground/60">
                            {'\n\n'}...(共 {h.chars} 字元,僅展示前 {h.snippet.length})
                          </span>
                        )}
                      </pre>
                    )}
                  </div>
                }
                popoverClassName="w-[44rem] max-w-[90vw]"
                side="top"
                align="start"
              />
            )
          })}
        </div>
      )}
    </details>
  )
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const label = STAGE_LABEL[stage.name] || stage.name
  const icon =
    stage.status === 'done' ? '✓' : stage.status === 'running' ? '🔄' : '⏸'
  const cls =
    stage.status === 'done'
      ? 'text-emerald-600 dark:text-emerald-400'
      : stage.status === 'running'
      ? 'text-primary'
      : 'text-muted-foreground/60'
  return (
    <div className={`flex items-center gap-2 text-[12px] ${cls}`}>
      <span className="w-4">{icon}</span>
      <span>{label}</span>
      {stage.cost_usd ? (
        <span className="ml-auto text-[10px] opacity-70 font-mono">
          ${stage.cost_usd.toFixed(4)}
        </span>
      ) : null}
    </div>
  )
}

function DoneView({
  result,
  stockSymbol,
  onRerun,
}: {
  result: DeepAnalysisResult
  stockSymbol: string
  onRerun: () => void
}) {
  // 防禦性預設值:後端拉歷史時可能 raw_data 缺失,這裡給完整 fallback 避免白屏
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion || {
    action: 'hold' as const,
    action_label: '持有',
    signal: '',
    reason: '',
    should_alert: false,
    agent_name: 'tradingagents',
    agent_label: 'TradingAgents 深度',
    confidence: 5.0,
  }
  const fromCache = rawData.from_cache
  const costUsd = rawData.cost_usd
  const sections = buildAnalysisSections(rawData)
  const analysisDate = result.timestamp
    ? String(result.timestamp).slice(0, 10)
    : new Date().toISOString().slice(0, 10)

  return (
    <div className="space-y-4 text-[13px]">
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>ℹ️ 當日快取:今天已經分析過這隻股票,展示快取結果(無新成本)</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            忽略快取重新分析
          </Button>
        </div>
      )}

      {/* 頂層摘要(精簡成一行:決策 + 置信度 + 成本;完整理由在"最終決策" tab) */}
      <div className="rounded-lg bg-accent/30 px-4 py-2.5 flex items-center gap-3 flex-wrap">
        <span className={`text-[18px] font-bold ${DECISION_COLOR[sug.action] || ''}`}>
          {sug.action_label}
        </span>
        <span className="text-[12px] text-muted-foreground">
          置信度 {sug.confidence?.toFixed(1) ?? '-'} / 10
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-[11px] ml-auto"
          onClick={() => window.open(`/analysis/${stockSymbol}/${analysisDate}`, '_blank')}
        >
          檢視詳細頁
        </Button>
        <span className="text-[10px] text-muted-foreground">
          成本:${costUsd?.toFixed(4) ?? '-'}
        </span>
      </div>

      {/* 統一 tab:最終決策 + 四位分析師 + 看多看空辯論 + 風控辯論(完整 + GFM 表格) */}
      <AnalysisTabs sections={sections} />

      {/* 資料注入診斷(歷史報告):從 raw_data.toolkit_diagnostic 拿 */}
      {rawData.toolkit_diagnostic && (
        <ToolkitDiagnostics
          summary={rawData.toolkit_diagnostic.summary}
          recent={rawData.toolkit_diagnostic.recent || []}
        />
      )}

      {/* 免責宣告 */}
      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        本分析由 AI 多 Agent 框架生成,僅供學習研究參考,不構成任何投資建議。
        投資有風險,決策需自主判斷。
      </div>
    </div>
  )
}

/** 決策與分析統一 tab。內容由 buildAnalysisSections 組裝(彈跳視窗與詳細頁共用),只渲染有內容的 tab。 */
function AnalysisTabs({ sections }: { sections: AnalysisSection[] }) {
  if (sections.length === 0) return null
  return (
    <div className="rounded-lg border border-border/50 p-4">
      <Tabs defaultValue={sections[0].id}>
        <TabsList>
          {sections.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {sections.map((s) => (
          <TabsContent key={s.id} value={s.id}>
            <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-table:my-3 prose-th:px-3 prose-th:py-1.5 prose-td:px-3 prose-td:py-1.5 prose-table:text-[12px] prose-strong:text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.markdown}</ReactMarkdown>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}
