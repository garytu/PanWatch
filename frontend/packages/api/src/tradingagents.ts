/**
 * TradingAgents 深度分析 API。
 * 複用現有 /api/stocks/:id/agents/:name/trigger,只是 agent_name = "tradingagents"。
 * 進度走新增的 /api/agents/runs/:trace_id/progress。
 */
import { fetchAPI, getToken } from './client'

export interface TradingAgentsTriggerResult {
  ok: boolean
  queued?: boolean
  trace_id?: string
  message?: string
  /** 後端冪等命中:已有在跑任務,trace_id 是現有任務的,不是新啟的 */
  deduplicated?: boolean
}

export interface AnalystReports {
  market: string
  social: string
  news: string
  fundamentals: string
}

export interface DebateHistory {
  history: string
  current_response: string
  judge_decision: string
}

export interface DeepAnalysisSuggestion {
  action: 'buy' | 'hold' | 'sell'
  action_label: string
  /** 上游五檔評級；review 表示無法安全解析，需要人工複核而不是普通持有。 */
  rating_raw?: 'buy' | 'overweight' | 'hold' | 'underweight' | 'sell' | 'review'
  review_required?: boolean
  /** 上游 propagate 的原始輸出，便於展示與排查對映差異。 */
  upstream_decision?: string
  signal: string
  reason: string
  should_alert: boolean
  agent_name: string
  agent_label: string
  confidence: number
}

export interface DeepAnalysisResult {
  agent_name: string
  title: string
  content: string
  raw_data: {
    suggestion: DeepAnalysisSuggestion
    cost_usd: number
    should_alert: boolean
    decision: string
    upstream_decision?: string
    confidence: number
    debate_history: DebateHistory
    risk_judgment: string
    risk_debate?: { history: string; judge_decision: string }
    analyst_reports: AnalystReports
    final_decision: string
    trader_plan: string
    from_cache?: boolean
    notified?: boolean
    toolkit_diagnostic?: {
      summary: { hit: number; miss: number; passthrough: number; fallthrough?: number; error: number }
      recent: Array<{
        action?: string
        method?: string
        symbol?: string
        chars?: number
        snippet?: string
        source?: string
        reason?: string
      }>
    }
  }
  timestamp?: string
}

export interface ProgressStage {
  name: string
  status: 'pending' | 'running' | 'done'
  started_at?: string
  duration_sec?: number
  cost_usd?: number
}

export interface ProgressDataSource {
  name: string
  status: 'pending' | 'running' | 'done' | 'error'
  error?: string
}

export interface ProgressActiveOperation {
  kind: 'llm' | 'tool'
  name: string
  /** TradingAgents LangGraph 節點名；舊後端快照可能沒有該欄位。 */
  agent?: string
}

export interface ToolkitHit {
  timestamp: string
  action: string  // HIT / MISS / PASSTHROUGH / ERROR
  method: string
  symbol: string
  reason?: string
  chars?: number
}

export interface ProgressResponse {
  trace_id: string
  status: 'not_found' | 'running' | 'success' | 'failed' | 'stale'
  current_stage?: string | null
  completed_stages: string[]
  started_at?: string | null
  elapsed_sec: number
  total_cost_usd: number
  active_operation?: ProgressActiveOperation | null
  stages: ProgressStage[]
  data_sources?: ProgressDataSource[]
  toolkit_summary?: { hit: number; miss: number; passthrough: number; fallthrough?: number; error: number }
  toolkit_recent?: ToolkitHit[]
  run?: {
    agent_name: string
    status: string
    result: string
    error: string
    duration_ms: number
    model_label: string
    notify_sent: boolean
  }
}

export interface BudgetInfo {
  used: number
  remaining: number
  limit: number
  exceeded: boolean
  runs_this_month: number
  estimate_next_run: {
    cost_low_usd: number
    cost_high_usd: number
    model: string
  }
  over_budget_action: 'reject' | 'warn' | 'continue'
  enabled: boolean
}

export interface HistoryComparisonItem {
  trace_id: string
  analysis_date: string
  action: 'buy' | 'hold' | 'sell'
  action_label: string
  confidence: number | null
  cost_usd: number | null
  price_at_analysis: number | null
  return_1d_pct: number | null
  return_5d_pct: number | null
  return_20d_pct: number | null
  hit_20d: boolean | null
}

export interface HistoryComparisonStats {
  total: number
  buy_count: number
  sell_count: number
  hold_count: number
  buy_hit_rate: number | null
  sell_hit_rate: number | null
  hold_hit_rate: number | null
  overall_hit_rate: number | null
  avg_return_20d_pct: number | null
}

export interface HistoryComparisonResponse {
  items: HistoryComparisonItem[]
  stats: HistoryComparisonStats
}

export const tradingAgentsApi = {
  /** 觸發深度分析(非同步排隊)。force=true 跳過同日快取。
   *  TradingAgents 不要求 StockAgent 繫結 — 始終帶 allow_unbound=true。 */
  trigger(stockId: number, opts: { force?: boolean } = {}): Promise<TradingAgentsTriggerResult> {
    const qsParts = ['allow_unbound=true']
    if (opts.force) qsParts.push('force_refresh=true')
    return fetchAPI(
      `/stocks/${stockId}/agents/tradingagents/trigger?${qsParts.join('&')}`,
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
    )
  },

  /** 讀取本月預算 + 單次預估成本(用於觸發前確認彈跳視窗)。 */
  getBudget(): Promise<BudgetInfo> {
    return fetchAPI('/agents/tradingagents/budget')
  },

  /** 把某次深度分析報告匯出為 PDF 檔案並觸發下載(後臺直出,不走列印對話方塊)。 */
  async downloadAnalysisPdf(symbol: string, date: string): Promise<void> {
    const token = getToken()
    const qs = new URLSearchParams({ stock_symbol: symbol, analysis_date: date })
    const resp = await fetch(`/api/agents/tradingagents/analysis/pdf?${qs.toString()}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok) {
      let msg = `匯出失敗 (${resp.status})`
      try {
        const j = await resp.json()
        msg = (j && (j.message || j.detail)) || msg
      } catch {
        /* 非 JSON 錯誤體,用預設提示 */
      }
      throw new Error(msg)
    }
    const blob = await resp.blob()
    let filename = `深度分析-${date}.pdf`
    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*=UTF-8''([^;]+)/i)
    if (m) {
      try {
        filename = decodeURIComponent(m[1])
      } catch {
        /* 保留預設檔名 */
      }
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  /** 查某隻股票最近 30 分鐘有沒有在跑或剛完成的 TA 任務(後端權威源)。
   *  返回 status: running | success | failed | stale | none
   *  stale = 5 分鐘無新進度日誌,前端可據此 reset 到 idle 允許重新觸發 */
  findRunning(symbol: string): Promise<{
    trace_id: string | null
    status: 'running' | 'success' | 'failed' | 'stale' | 'none'
    last_activity_at?: string
  }> {
    return fetchAPI(`/agents/tradingagents/running?stock_symbol=${encodeURIComponent(symbol)}`)
  },

  /** 拉取進度(前端輪詢)。 */
  getProgress(traceId: string): Promise<ProgressResponse> {
    return fetchAPI(`/agents/runs/${encodeURIComponent(traceId)}/progress`)
  },

  /** 歷史決策 vs 實際漲跌對比。 */
  getHistoryComparison(
    symbol: string,
    market: string,
    days = 90,
  ): Promise<HistoryComparisonResponse> {
    const qs = new URLSearchParams({
      stock_symbol: symbol,
      market,
      days: String(days),
    })
    return fetchAPI(`/agents/tradingagents/history-comparison?${qs.toString()}`)
  },

  /** 拉取某隻股票最近一次深度分析結果(含完整 raw_data)。 */
  getLatestForStock(symbol: string): Promise<DeepAnalysisResult | null> {
    return fetchAPI(
      `/agents/tradingagents/latest?stock_symbol=${encodeURIComponent(symbol)}`,
    ).then((item: unknown) => {
      if (!item || typeof item !== 'object') return null
      const rec = item as { content?: string; title?: string; raw_data?: unknown; analysis_date?: string }
      if (!rec.content) return null
      return {
        agent_name: 'tradingagents',
        title: rec.title || '',
        content: rec.content || '',
        raw_data: (rec.raw_data || {}) as DeepAnalysisResult['raw_data'],
        timestamp: rec.analysis_date,
      }
    })
  },

  /** 按 symbol + date 拉某次深度分析完整結果(詳細閱讀頁用)。 */
  getAnalysisByDate(symbol: string, date: string): Promise<DeepAnalysisResult | null> {
    const qs = new URLSearchParams({ stock_symbol: symbol, analysis_date: date })
    return fetchAPI(`/agents/tradingagents/analysis?${qs.toString()}`).then((item: unknown) => {
      if (!item || typeof item !== 'object') return null
      const rec = item as { content?: string; title?: string; raw_data?: unknown; analysis_date?: string }
      if (!rec.content) return null
      return {
        agent_name: 'tradingagents',
        title: rec.title || '',
        content: rec.content || '',
        raw_data: (rec.raw_data || {}) as DeepAnalysisResult['raw_data'],
        timestamp: rec.analysis_date,
      }
    })
  },
}
