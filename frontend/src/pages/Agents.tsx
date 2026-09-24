import { useState, useEffect } from 'react'
import { Play, Power, Clock, Cpu, Bot, Bell, Settings2 } from 'lucide-react'
import { fetchAPI, type AIService, type NotifyChannel } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface AgentConfig {
  id: number
  name: string
  display_name: string
  description: string
  enabled: boolean
  schedule: string
  execution_mode: string
  ai_model_id: number | null
  notify_channel_ids: number[]
  config: Record<string, unknown>
}

interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

interface StockConfig {
  id: number
  symbol: string
  name: string
  market: string
  agents: StockAgentInfo[]
}

interface SchedulePreview {
  schedule: string
  timezone: string
  next_runs: string[]
}

interface AgentRun {
  id: number
  agent_name: string
  status: string
  result: string
  error: string
  duration_ms: number
  created_at: string
}

interface AgentsHealth {
  timezone: string
  summary: {
    next_24h_count: number
    recent_failed_count: number
  }
  agents: Array<{
    name: string
    display_name: string
    enabled: boolean
    schedule: string
    execution_mode: string
    next_runs: string[]
    last_run: null | {
      status: string
      created_at: string
      duration_ms: number
      error: string
    }
  }>
}

// 排程型別
type ScheduleType = 'daily' | 'weekdays' | 'interval' | 'cron'

interface ScheduleConfig {
  type: ScheduleType
  time?: string      // HH:MM 格式
  interval?: number  // 分鐘數
  cron?: string      // 自定義 cron
}

// cron 轉友好配置
function parseCronToConfig(cron: string): ScheduleConfig {
  if (!cron) return { type: 'daily', time: '15:30' }

  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return { type: 'cron', cron }

  const [minute, hour, , , dayOfWeek] = parts

  // 檢測間隔模式 */N
  if (minute.startsWith('*/')) {
    const interval = parseInt(minute.slice(2))
    if (!isNaN(interval)) return { type: 'interval', interval }
  }

  // 檢測每天或工作日
  const m = parseInt(minute)
  const h = parseInt(hour)
  if (!isNaN(m) && !isNaN(h)) {
    const time = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`
    if (dayOfWeek === '1-5') return { type: 'weekdays', time }
    if (dayOfWeek === '*') return { type: 'daily', time }
  }

  return { type: 'cron', cron }
}

// 友好配置轉 cron
function configToCron(config: ScheduleConfig): string {
  switch (config.type) {
    case 'daily': {
      const [h, m] = (config.time || '15:30').split(':')
      return `${parseInt(m)} ${parseInt(h)} * * *`
    }
    case 'weekdays': {
      const [h, m] = (config.time || '15:30').split(':')
      return `${parseInt(m)} ${parseInt(h)} * * 1-5`
    }
    case 'interval':
      return `*/${config.interval || 30} * * * *`
    case 'cron':
      return config.cron || '0 15 * * *'
    default:
      return '0 15 * * *'
  }
}

// 友好顯示排程
function formatSchedule(cron: string): string {
  const config = parseCronToConfig(cron)
  switch (config.type) {
    case 'daily':
      return `每天 ${config.time}`
    case 'weekdays':
      return `工作日 ${config.time}`
    case 'interval':
      return `每 ${config.interval} 分鐘`
    case 'cron':
      return cron
    default:
      return cron
  }
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [stocks, setStocks] = useState<StockConfig[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState<string | null>(null)

  const [bindDialogAgent, setBindDialogAgent] = useState<AgentConfig | null>(null)
  const [bindKeyword, setBindKeyword] = useState('')
  const [bindFilter, setBindFilter] = useState<'all' | 'bound' | 'unbound'>('all')
  const [bindSavingStockIds, setBindSavingStockIds] = useState<Set<number>>(new Set())

  const [health, setHealth] = useState<AgentsHealth | null>(null)
  const [healthLoading, setHealthLoading] = useState(false)

  const [previews, setPreviews] = useState<Record<string, SchedulePreview | { error: string }>>({})

  // 排程編輯彈跳視窗
  const [scheduleDialogAgent, setScheduleDialogAgent] = useState<AgentConfig | null>(null)
  // TradingAgents 深度配置彈跳視窗(雙模型 / 預算 / 超時 / 模擬交易對接)
  const [taConfigAgent, setTaConfigAgent] = useState<AgentConfig | null>(null)
  const [taConfigForm, setTaConfigForm] = useState<Record<string, unknown>>({})
  const [scheduleConfig, setScheduleConfig] = useState<ScheduleConfig>({ type: 'daily', time: '15:30' })
  const [schedulePreview, setSchedulePreview] = useState<SchedulePreview | { error: string } | null>(null)
  const [schedulePreviewLoading, setSchedulePreviewLoading] = useState(false)

  const [runsOpen, setRunsOpen] = useState<Record<string, boolean>>({})
  const [runsLoading, setRunsLoading] = useState<Record<string, boolean>>({})
  const [runs, setRuns] = useState<Record<string, AgentRun[] | { error: string }>>({})

  const { toast } = useToast()

  const formatPreviewTime = (iso: string, tz?: string): string => {
    try {
      const d = new Date(iso)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('zh-CN', {
        timeZone: tz || undefined,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    } catch {
      return iso
    }
  }

  const load = async () => {
    try {
      const [agentData, stockData, servicesData, channelData] = await Promise.all([
        fetchAPI<AgentConfig[]>('/agents'),
        fetchAPI<StockConfig[]>('/stocks'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setAgents(agentData)
      setStocks(stockData)
      setServices(servicesData)
      setChannels(channelData)

      // 預載入未來觸發時間（避免“工作日/週末”語義誤解）
      const previewPairs = await Promise.all(agentData.map(async a => {
        if (!a.schedule) return [a.name, { schedule: '', timezone: '', next_runs: [] }] as const
        try {
          const p = await fetchAPI<SchedulePreview>(`/agents/${a.name}/schedule/preview?count=3`)
          return [a.name, p] as const
        } catch (e) {
          const msg = e instanceof Error ? e.message : '預覽失敗'
          return [a.name, { error: msg }] as const
        }
      }))
      setPreviews(Object.fromEntries(previewPairs))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const loadHealth = async () => {
    setHealthLoading(true)
    try {
      const h = await fetchAPI<AgentsHealth>('/agents/health')
      setHealth(h)
    } catch (e) {
      console.error(e)
      setHealth(null)
    } finally {
      setHealthLoading(false)
    }
  }

  useEffect(() => { load(); loadHealth() }, [])

  // 排程編輯彈跳視窗：即時預覽未來觸發時間（防止工作日/週末語義誤解）
  useEffect(() => {
    if (!scheduleDialogAgent) {
      setSchedulePreview(null)
      return
    }

    const cron = configToCron(scheduleConfig)
    const timer = setTimeout(async () => {
      setSchedulePreviewLoading(true)
      try {
        const p = await fetchAPI<SchedulePreview>(`/agents/schedule/preview?schedule=${encodeURIComponent(cron)}&count=5`)
        setSchedulePreview(p)
      } catch (e) {
        const msg = e instanceof Error ? e.message : '預覽失敗'
        setSchedulePreview({ error: msg })
      } finally {
        setSchedulePreviewLoading(false)
      }
    }, 350)

    return () => clearTimeout(timer)
  }, [scheduleDialogAgent, scheduleConfig])

  const toggleAgent = async (agent: AgentConfig) => {
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !agent.enabled }),
    })
    load()
  }

  const openBindDialog = (agent: AgentConfig) => {
    setBindDialogAgent(agent)
    setBindKeyword('')
    setBindFilter('all')
  }

  const hasAgentBound = (stock: StockConfig, agentName: string) =>
    (stock.agents || []).some(a => a.agent_name === agentName)

  const getAgentBoundCount = (agentName: string) =>
    stocks.filter(s => hasAgentBound(s, agentName)).length

  const getBoundStocks = (agentName: string) =>
    stocks.filter(s => hasAgentBound(s, agentName))

  const filteredBindStocks = stocks
    .filter(s => {
      const q = bindKeyword.trim().toLowerCase()
      if (!q) return true
      return s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)
    })
    .filter(s => {
      if (!bindDialogAgent) return true
      const bound = hasAgentBound(s, bindDialogAgent.name)
      if (bindFilter === 'bound') return bound
      if (bindFilter === 'unbound') return !bound
      return true
    })

  const updateBindSaving = (stockId: number, saving: boolean) => {
    setBindSavingStockIds(prev => {
      const next = new Set(prev)
      if (saving) next.add(stockId)
      else next.delete(stockId)
      return next
    })
  }

  const buildNextAgents = (stock: StockConfig, agentName: string, shouldBind: boolean) => {
    const current = stock.agents || []
    const exists = current.some(a => a.agent_name === agentName)
    if (shouldBind && !exists) {
      return [...current, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
    }
    if (!shouldBind && exists) {
      return current.filter(a => a.agent_name !== agentName)
    }
    return current
  }

  const toggleStockBindingForAgent = async (stock: StockConfig, agentName: string) => {
    if (!agentName) return
    if (bindSavingStockIds.has(stock.id)) return
    updateBindSaving(stock.id, true)
    try {
      const exists = (stock.agents || []).some(a => a.agent_name === agentName)
      const nextAgents = buildNextAgents(stock, agentName, !exists)

      const updated = await fetchAPI<StockConfig>(`/stocks/${stock.id}/agents`, {
        method: 'PUT',
        // 保留該股票已有 Agent 的 schedule/模型/通知覆蓋，僅切換當前 Agent 繫結狀態
        body: JSON.stringify({
          agents: nextAgents.map(a => ({
            agent_name: a.agent_name,
            schedule: a.schedule || '',
            ai_model_id: a.ai_model_id ?? null,
            notify_channel_ids: a.notify_channel_ids || [],
          })),
        }),
      })
      setStocks(prev => prev.map(s => (s.id === stock.id ? updated : s)))
    } catch (e) {
      toast(e instanceof Error ? e.message : '切換繫結失敗', 'error')
    } finally {
      updateBindSaving(stock.id, false)
    }
  }

  const applyBulkBindingForAgent = async (shouldBind: boolean) => {
    if (!bindDialogAgent) return
    const target = filteredBindStocks.filter(s => hasAgentBound(s, bindDialogAgent.name) !== shouldBind)
    if (target.length === 0) {
      toast(shouldBind ? '當前篩選已全部繫結' : '當前篩選已全部解綁', 'info')
      return
    }

    setBindSavingStockIds(new Set(target.map(s => s.id)))
    try {
      const tasks = target.map(async (stock) => {
        const nextAgents = buildNextAgents(stock, bindDialogAgent.name, shouldBind)
        const updated = await fetchAPI<StockConfig>(`/stocks/${stock.id}/agents`, {
          method: 'PUT',
          body: JSON.stringify({
            agents: nextAgents.map(a => ({
              agent_name: a.agent_name,
              schedule: a.schedule || '',
              ai_model_id: a.ai_model_id ?? null,
              notify_channel_ids: a.notify_channel_ids || [],
            })),
          }),
        })
        return updated
      })
      const updatedList = await Promise.all(tasks)
      const map = new Map(updatedList.map(s => [s.id, s]))
      setStocks(prev => prev.map(s => map.get(s.id) || s))
      toast(shouldBind ? `已繫結 ${updatedList.length} 只` : `已解綁 ${updatedList.length} 只`, 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '批次操作失敗', 'error')
    } finally {
      setBindSavingStockIds(new Set())
    }
  }

  const triggerAgent = async (name: string) => {
    setTriggering(name)
    try {
      const res = await fetchAPI<{ queued?: boolean; message?: string }>(`/agents/${name}/trigger`, { method: 'POST' })
      toast(res?.queued ? 'Agent 已提交後臺執行' : (res?.message || 'Agent 已觸發'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '觸發失敗', 'error')
    } finally {
      setTriggering(null)
    }
  }

  const toggleRuns = async (agentName: string) => {
    const nextOpen = !runsOpen[agentName]
    setRunsOpen(prev => ({ ...prev, [agentName]: nextOpen }))
    if (!nextOpen) return

    if (runs[agentName] || runsLoading[agentName]) return
    setRunsLoading(prev => ({ ...prev, [agentName]: true }))
    try {
      const data = await fetchAPI<AgentRun[]>(`/agents/${agentName}/history?limit=5`)
      setRuns(prev => ({ ...prev, [agentName]: data }))
    } catch (e) {
      const msg = e instanceof Error ? e.message : '載入失敗'
      setRuns(prev => ({ ...prev, [agentName]: { error: msg } }))
    } finally {
      setRunsLoading(prev => ({ ...prev, [agentName]: false }))
    }
  }

  const updateAgentModel = async (agent: AgentConfig, modelId: number | null) => {
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ ai_model_id: modelId }),
    })
    load()
  }

  const toggleAgentChannel = async (agent: AgentConfig, channelId: number) => {
    const current = agent.notify_channel_ids || []
    const newIds = current.includes(channelId)
      ? current.filter(id => id !== channelId)
      : [...current, channelId]
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ notify_channel_ids: newIds }),
    })
    load()
  }

  // 當 taConfigAgent 切換時,把它的 config 拷到表單
  useEffect(() => {
    if (taConfigAgent) {
      setTaConfigForm({ ...(taConfigAgent.config || {}) })
    }
  }, [taConfigAgent])

  const saveTaConfig = async () => {
    if (!taConfigAgent) return
    try {
      await fetchAPI(`/agents/${taConfigAgent.name}`, {
        method: 'PUT',
        body: JSON.stringify({ config: taConfigForm }),
      })
      toast('TradingAgents 配置已儲存', 'success')
      setTaConfigAgent(null)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '儲存失敗', 'error')
    }
  }

  const openScheduleDialog = (agent: AgentConfig) => {
    setScheduleDialogAgent(agent)
    setScheduleConfig(parseCronToConfig(agent.schedule))
  }

  const saveSchedule = async () => {
    if (!scheduleDialogAgent) return
    const cron = configToCron(scheduleConfig)
    await fetchAPI(`/agents/${scheduleDialogAgent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ schedule: cron }),
    })
    setScheduleDialogAgent(null)
    load()
    toast('排程已更新', 'success')
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      <div className="mb-4 md:mb-8">
        <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">Agent</h1>
        <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">自動化任務管理與排程</p>
      </div>

      {/* Scheduler Health */}
      <div className="card p-4 mb-4">
        <div className="flex items-center justify-between">
          <div className="text-[13px] font-semibold text-foreground">排程健康</div>
          <Button variant="secondary" size="sm" className="h-8" onClick={loadHealth} disabled={healthLoading}>
            {healthLoading ? (
              <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
            ) : (
              <span className="text-[12px]">重新整理</span>
            )}
          </Button>
        </div>
        {health ? (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
            <span>時區: <span className="font-mono text-foreground/90">{health.timezone}</span></span>
            <span className="opacity-50">|</span>
            <span>未來 24h 將觸發: <span className="font-mono text-foreground/90">{health.summary.next_24h_count}</span></span>
            <span className="opacity-50">|</span>
            <span>最近失敗: <span className={`font-mono ${health.summary.recent_failed_count > 0 ? 'text-rose-600' : 'text-foreground/90'}`}>{health.summary.recent_failed_count}</span></span>
          </div>
        ) : (
          <div className="mt-2 text-[12px] text-muted-foreground">—</div>
        )}
      </div>

      {agents.length === 0 ? (
        <div className="card flex flex-col items-center justify-center py-20">
          <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
            <Bot className="w-6 h-6 text-primary" />
          </div>
          <p className="text-[15px] font-semibold text-foreground">暫無 Agent</p>
          <p className="text-[13px] text-muted-foreground mt-1.5">啟動後臺服務後 Agent 會自動註冊</p>
        </div>
      ) : (
        <div className="space-y-4">
          {agents.map(agent => {
            const modeLabel = agent.execution_mode === 'single' ? '逐只分析' : '批次分析'
            const preview = previews[agent.name]
            const boundStocks = getBoundStocks(agent.name)
            const boundSummary = boundStocks.length > 0
              ? `${boundStocks.slice(0, 3).map(s => s.name || s.symbol).join('、')}${boundStocks.length > 3 ? '、...更多' : ''}`
              : '未繫結股票'
            return (
              <div key={agent.name} className="card-hover p-4 md:p-6">
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 sm:gap-6">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${agent.enabled ? 'bg-emerald-500' : 'bg-border'}`} />
                      <h3 className="text-[15px] font-semibold text-foreground">{agent.display_name}</h3>
                      <Badge variant="secondary" className="text-[10px]">{modeLabel}</Badge>
                      <button
                        type="button"
                        onClick={() => openBindDialog(agent)}
                        className={`max-w-[320px] truncate px-2 py-0.5 rounded-md border text-[11px] transition-colors ${
                          boundStocks.length > 0
                            ? 'bg-primary/12 border-primary/35 text-primary hover:bg-primary/18'
                            : 'bg-accent/30 border-border/60 text-muted-foreground hover:border-primary/30'
                        }`}
                        title={`${boundSummary}（已繫結 ${getAgentBoundCount(agent.name)} / ${stocks.length}）`}
                      >
                        {boundSummary}
                      </button>
                    </div>
                    <p className="text-[13px] text-muted-foreground mt-2.5 ml-[22px] leading-relaxed">{agent.description}</p>

                    {/* 執行週期 - 可點選編輯 */}
                    <div className="flex items-center gap-2.5 mt-3.5 ml-[22px] flex-wrap">
                      <button
                        onClick={() => openScheduleDialog(agent)}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-accent/50 hover:bg-accent transition-colors"
                      >
                        <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                        <span className="text-[12px] text-foreground">{formatSchedule(agent.schedule)}</span>
                        <Settings2 className="w-3 h-3 text-muted-foreground/50" />
                      </button>
                      {agent.name === 'tradingagents' && (
                        <button
                          onClick={() => setTaConfigAgent(agent)}
                          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-primary/10 hover:bg-primary/20 transition-colors text-primary"
                          title="編輯 TradingAgents 雙模型/預算/超時/模擬交易等高階配置"
                        >
                          <Settings2 className="w-3.5 h-3.5" />
                          <span className="text-[12px]">深度配置</span>
                        </button>
                      )}
                    </div>

                    {/* 未來觸發時間（按排程時區） */}
                    {'error' in (preview || {}) ? (
                      <div className="mt-2 ml-[22px] text-[11px] text-muted-foreground">
                        未來觸發時間：{(preview as { error: string }).error}
                      </div>
                    ) : (preview as SchedulePreview | undefined)?.next_runs?.length ? (
                      <div className="mt-2 ml-[22px] flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span className="opacity-80">未來 3 次：</span>
                        {(preview as SchedulePreview).next_runs.map((t, i) => (
                          <span
                            key={i}
                            className="px-1.5 py-0.5 rounded border border-border/60 bg-accent/30 font-mono"
                            title={t}
                          >
                            {formatPreviewTime(t, (preview as SchedulePreview).timezone)}
                          </span>
                        ))}
                        {(preview as SchedulePreview).timezone ? (
                          <span className="opacity-60">({(preview as SchedulePreview).timezone})</span>
                        ) : null}
                      </div>
                    ) : null}

                    <div className="mt-4 ml-[22px] space-y-3">
                      {/* AI Model select */}
                      <div className="flex items-center gap-2">
                        <Cpu className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                        <Select
                          value={agent.ai_model_id?.toString() ?? '__default__'}
                          onValueChange={val => updateAgentModel(agent, val === '__default__' ? null : parseInt(val))}
                        >
                          <SelectTrigger className="h-7 text-[12px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__default__">系統預設</SelectItem>
                            {services.map(svc => (
                              <SelectGroup key={svc.id}>
                                <SelectLabel>{svc.name}</SelectLabel>
                                {svc.models.map(m => (
                                  <SelectItem key={m.id} value={m.id.toString()}>
                                    {m.name}{m.name !== m.model ? ` (${m.model})` : ''}
                                  </SelectItem>
                                ))}
                              </SelectGroup>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      {/* Notify Channel multi-select */}
                      {channels.length > 0 && (
                        <div className="flex items-center gap-2 flex-wrap">
                          <Bell className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          {channels.map(ch => {
                            const isSelected = (agent.notify_channel_ids || []).includes(ch.id)
                            return (
                              <button
                                key={ch.id}
                                onClick={() => toggleAgentChannel(agent, ch.id)}
                                className={`text-[11px] px-2 py-0.5 rounded-md border transition-colors ${
                                  isSelected
                                    ? 'bg-primary/10 border-primary/30 text-primary font-medium'
                                    : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-primary/30'
                                }`}
                              >
                                {ch.name}
                              </button>
                            )
                          })}
                          {(agent.notify_channel_ids || []).length === 0 && (
                            <span className="text-[11px] text-muted-foreground">系統預設</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 ml-[22px] sm:ml-0">
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8"
                      onClick={() => triggerAgent(agent.name)}
                      disabled={!agent.enabled || triggering === agent.name}
                    >
                      {triggering === agent.name ? (
                        <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5" />
                      )}
                      <span className="hidden sm:inline">{triggering === agent.name ? '執行中' : '觸發'}</span>
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8"
                      onClick={() => toggleRuns(agent.name)}
                    >
                      <span className="text-[12px]">最近執行</span>
                    </Button>
                    <Button
                      variant={agent.enabled ? 'destructive' : 'default'}
                      size="sm"
                      className="h-8"
                      onClick={() => toggleAgent(agent)}
                    >
                      <Power className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">{agent.enabled ? '停用' : '啟用'}</span>
                    </Button>
                  </div>
                </div>

                {runsOpen[agent.name] && (
                  <div className="mt-4 ml-[22px] sm:ml-0 rounded-lg border border-border/40 bg-accent/20 p-3">
                    <div className="flex items-center justify-between">
                      <div className="text-[12px] font-medium text-foreground">最近 5 次執行</div>
                      {runsLoading[agent.name] && (
                        <span className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                      )}
                    </div>
                    {(() => {
                      const data = runs[agent.name]
                      if (!data) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">載入中…</div>
                      }
                      if ('error' in data) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">{data.error}</div>
                      }
                      if (data.length === 0) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">暫無記錄</div>
                      }
                      return (
                        <div className="mt-2 space-y-2">
                          {data.map(r => (
                            <div key={r.id} className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-[11px] text-muted-foreground">
                                  <span className={`inline-block w-1.5 h-1.5 rounded-full mr-2 ${r.status === 'failed' ? 'bg-rose-500' : 'bg-emerald-500'}`} />
                                  <span className="font-mono">{r.created_at}</span>
                                  <span className="ml-2 font-mono opacity-70">{Math.round((r.duration_ms || 0) / 1000)}s</span>
                                </div>
                                {r.error ? (
                                  <div className="mt-0.5 text-[11px] text-rose-600 break-words">{r.error}</div>
                                ) : null}
                              </div>
                              <div className="text-[10px] text-muted-foreground/70 font-mono">{r.status}</div>
                            </div>
                          ))}
                        </div>
                      )
                    })()}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 排程設定彈跳視窗 */}
      <Dialog open={!!scheduleDialogAgent} onOpenChange={open => !open && setScheduleDialogAgent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>設定執行週期</DialogTitle>
            <DialogDescription>{scheduleDialogAgent?.display_name}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>排程型別</Label>
              <Select
                value={scheduleConfig.type}
                onValueChange={val => setScheduleConfig({ ...scheduleConfig, type: val as ScheduleType })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">每天定時</SelectItem>
                  <SelectItem value="weekdays">工作日定時</SelectItem>
                  <SelectItem value="interval">固定間隔</SelectItem>
                  <SelectItem value="cron">自定義 Cron</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {(scheduleConfig.type === 'daily' || scheduleConfig.type === 'weekdays') && (
              <div>
                <Label>執行時間</Label>
                <Input
                  type="time"
                  value={scheduleConfig.time || '15:30'}
                  onChange={e => setScheduleConfig({ ...scheduleConfig, time: e.target.value })}
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  {scheduleConfig.type === 'weekdays' ? '週一至週五' : '每天'}在此時間執行
                </p>
              </div>
            )}

            {scheduleConfig.type === 'interval' && (
              <div>
                <Label>執行間隔（分鐘）</Label>
                <Select
                  value={(scheduleConfig.interval || 30).toString()}
                  onValueChange={val => setScheduleConfig({ ...scheduleConfig, interval: parseInt(val) })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="5">每 5 分鐘</SelectItem>
                    <SelectItem value="10">每 10 分鐘</SelectItem>
                    <SelectItem value="15">每 15 分鐘</SelectItem>
                    <SelectItem value="30">每 30 分鐘</SelectItem>
                    <SelectItem value="60">每小時</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {scheduleConfig.type === 'cron' && (
              <div>
                <Label>Cron 表示式</Label>
                <Input
                  value={scheduleConfig.cron || ''}
                  onChange={e => setScheduleConfig({ ...scheduleConfig, cron: e.target.value })}
                  placeholder="0 15 * * 1-5"
                  className="font-mono"
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  格式：分 時 日 月 周（如 0 15 * * 1-5 表示工作日 15:00）
                </p>
              </div>
            )}

            {/* Preview */}
            <div className="rounded-lg border border-border/50 bg-accent/20 p-3">
              <div className="flex items-center justify-between">
                <div className="text-[12px] font-medium text-foreground">未來觸發時間預覽</div>
                {schedulePreviewLoading && (
                  <span className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                )}
              </div>
              {'error' in (schedulePreview || {}) ? (
                <div className="mt-2 text-[11px] text-muted-foreground">
                  {(schedulePreview as { error: string }).error}
                </div>
              ) : (schedulePreview as SchedulePreview | null)?.next_runs?.length ? (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  {(schedulePreview as SchedulePreview).next_runs.map((t, i) => (
                    <span
                      key={i}
                      className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 font-mono"
                      title={t}
                    >
                      {formatPreviewTime(t, (schedulePreview as SchedulePreview).timezone)}
                    </span>
                  ))}
                  {(schedulePreview as SchedulePreview | null)?.timezone ? (
                    <span className="opacity-60">({(schedulePreview as SchedulePreview).timezone})</span>
                  ) : null}
                </div>
              ) : (
                <div className="mt-2 text-[11px] text-muted-foreground">—</div>
              )}
              <div className="mt-2 text-[11px] text-muted-foreground/70 font-mono">
                schedule: {configToCron(scheduleConfig)}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setScheduleDialogAgent(null)}>取消</Button>
              <Button onClick={saveSchedule}>儲存</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!bindDialogAgent} onOpenChange={(open) => !open && setBindDialogAgent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {bindDialogAgent ? `${bindDialogAgent.display_name} 股票繫結` : '股票繫結'}
            </DialogTitle>
            <DialogDescription>點選即可切換繫結/不繫結，不會覆蓋該股票的其它 Agent 個性化配置</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <Label>篩選股票</Label>
              <Input
                value={bindKeyword}
                onChange={(e) => setBindKeyword(e.target.value)}
                placeholder="按程式碼或名稱篩選"
              />
            </div>

            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-1.5">
                <Button variant={bindFilter === 'all' ? 'default' : 'secondary'} size="sm" className="h-7 text-[11px]" onClick={() => setBindFilter('all')}>全部</Button>
                <Button variant={bindFilter === 'bound' ? 'default' : 'secondary'} size="sm" className="h-7 text-[11px]" onClick={() => setBindFilter('bound')}>已繫結</Button>
                <Button variant={bindFilter === 'unbound' ? 'default' : 'secondary'} size="sm" className="h-7 text-[11px]" onClick={() => setBindFilter('unbound')}>未繫結</Button>
              </div>
              <div className="flex items-center gap-1.5">
                <Button variant="secondary" size="sm" className="h-7 text-[11px]" disabled={!bindDialogAgent || bindSavingStockIds.size > 0} onClick={() => applyBulkBindingForAgent(true)}>批次繫結</Button>
                <Button variant="secondary" size="sm" className="h-7 text-[11px]" disabled={!bindDialogAgent || bindSavingStockIds.size > 0} onClick={() => applyBulkBindingForAgent(false)}>批次解綁</Button>
              </div>
            </div>

            <div className="max-h-[40vh] overflow-y-auto rounded border border-border/50 p-3">
              {filteredBindStocks.length === 0 ? (
                <div className="p-4 text-[12px] text-muted-foreground text-center">無可選股票</div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {filteredBindStocks.map((s) => {
                    const bound = bindDialogAgent ? hasAgentBound(s, bindDialogAgent.name) : false
                    const saving = bindSavingStockIds.has(s.id)
                    return (
                      <button
                        key={s.id}
                        type="button"
                        disabled={!bindDialogAgent || saving}
                        onClick={() => bindDialogAgent && toggleStockBindingForAgent(s, bindDialogAgent.name)}
                        className={`h-8 px-3 rounded-full text-[12px] border transition-colors disabled:opacity-60 ${
                          bound
                            ? 'bg-primary/12 border-primary/35 text-primary hover:bg-primary/18'
                            : 'bg-accent/30 border-border/60 text-muted-foreground hover:border-primary/30'
                        }`}
                        title={`${s.name} (${s.symbol})`}
                      >
                        {saving ? '處理中...' : `${s.name || s.symbol}`}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setBindDialogAgent(null)}>關閉</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* TradingAgents 深度配置彈跳視窗 */}
      <Dialog open={!!taConfigAgent} onOpenChange={open => !open && setTaConfigAgent(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>TradingAgents 深度配置</DialogTitle>
            <DialogDescription>
              雙模型分檔 / 月度預算 / 超時 / 模擬交易對接。完整說明見
              <code className="ml-1 text-[11px] bg-accent/40 px-1">.docs/tradingagents/USER_GUIDE.md § 12</code>
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5 mt-2 text-[13px]">
            {/* 模型分檔(可選)— 從 Agent 預設 model 所在的同一 AI Service 選 */}
            {(() => {
              // Agent 卡片上選的預設 model 決定 service;深度/快速只能從同 service 裡選
              const defaultModelId = taConfigAgent?.ai_model_id ?? null
              const agentService = defaultModelId
                ? services.find(s => s.models.some(m => m.id === defaultModelId))
                : null
              const defaultModel = defaultModelId
                ? agentService?.models.find(m => m.id === defaultModelId)
                : null
              // 候選 model 列表:有 service 則限定該 service,否則所有 services 全部 model
              const candidateModels = agentService
                ? agentService.models
                : services.flatMap(s => s.models)

              return (
                <section>
                  <div className="font-medium mb-2">模型分檔(可選)</div>

                  {/* 顯示 Agent 預設模型來源,讓使用者知道 service 上下文 */}
                  <div className="rounded-md bg-accent/30 border border-border/40 p-2 text-[11px] text-muted-foreground mb-3">
                    {defaultModel && agentService ? (
                      <>當前 Agent 預設模型: <span className="text-foreground font-medium">{defaultModel.model}</span>
                       <span className="opacity-70"> (來自 {agentService.name})</span></>
                    ) : (
                      <>當前 Agent 使用系統預設 AI 服務(在 Agent 卡片上「模型」處選擇)。
                       建議先選定一個 Service 再來分檔配置。</>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-[12px]">
                        深度思考模型 <span className="text-muted-foreground/70 font-normal">(辯論/風控/PM)</span>
                      </Label>
                      <Select
                        value={(taConfigForm.deep_model as string) || '__default__'}
                        onValueChange={val => setTaConfigForm({ ...taConfigForm, deep_model: val === '__default__' ? '' : val })}
                      >
                        <SelectTrigger className="h-9 text-[12px]">
                          <SelectValue placeholder="使用 Agent 預設" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__default__">使用 Agent 預設</SelectItem>
                          {candidateModels.map(m => (
                            <SelectItem key={`deep-${m.id}`} value={m.model}>
                              {m.model}{m.name !== m.model ? ` · ${m.name}` : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="text-[12px]">
                        快速思考模型 <span className="text-muted-foreground/70 font-normal">(分析師/工具)</span>
                      </Label>
                      <Select
                        value={(taConfigForm.quick_model as string) || '__default__'}
                        onValueChange={val => setTaConfigForm({ ...taConfigForm, quick_model: val === '__default__' ? '' : val })}
                      >
                        <SelectTrigger className="h-9 text-[12px]">
                          <SelectValue placeholder="= 深度模型" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__default__">= 深度模型</SelectItem>
                          {candidateModels.map(m => (
                            <SelectItem key={`quick-${m.id}`} value={m.model}>
                              {m.model}{m.name !== m.model ? ` · ${m.name}` : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
                    <div>• 留空 = 用 Agent 預設模型,不分檔</div>
                    <div>• 兩個分檔必須在同一個 AI 服務下(TradingAgents 共用 backend_url)</div>
                    <div className="text-amber-600">⚠️ 不要選推理模型 (如 deepseek-r1 / o1) — 它們在 langchain agent loop 裡會輸出亂碼。用 chat 類: claude-sonnet / deepseek-chat / gpt-4o-mini</div>
                  </div>
                </section>
              )
            })()}

            {/* 預算與策略 */}
            <section>
              <div className="font-medium mb-2">預算與策略</div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-[12px]">月度預算(美元)</Label>
                  <Input
                    type="number"
                    step="0.5"
                    value={String(taConfigForm.monthly_budget_usd ?? 10)}
                    onChange={e => setTaConfigForm({ ...taConfigForm, monthly_budget_usd: parseFloat(e.target.value) || 0 })}
                  />
                </div>
                <div>
                  <Label className="text-[12px]">超預算行為</Label>
                  <select
                    className="w-full h-9 rounded-md border border-border bg-background px-3 text-[13px]"
                    value={(taConfigForm.over_budget_action as string) || 'reject'}
                    onChange={e => setTaConfigForm({ ...taConfigForm, over_budget_action: e.target.value })}
                  >
                    <option value="reject">拒絕新觸發</option>
                    <option value="warn">警告但繼續</option>
                    <option value="continue">不提示也不擋</option>
                  </select>
                </div>
                <div>
                  <Label className="text-[12px]">辯論輪次</Label>
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={String(taConfigForm.debate_rounds ?? 1)}
                    onChange={e => setTaConfigForm({ ...taConfigForm, debate_rounds: parseInt(e.target.value) || 1 })}
                  />
                </div>
                <div>
                  <Label className="text-[12px]">超時(分鐘)</Label>
                  <Input
                    type="number"
                    min={1}
                    max={60}
                    value={String(taConfigForm.timeout_minutes ?? 15)}
                    onChange={e => setTaConfigForm({ ...taConfigForm, timeout_minutes: parseInt(e.target.value) || 15 })}
                  />
                </div>
              </div>
            </section>

            {/* 模擬交易對接 */}
            <section>
              <div className="flex items-center gap-2 mb-2">
                <input
                  type="checkbox"
                  id="emit-paper-trading"
                  checked={!!taConfigForm.emit_paper_trading_signal}
                  onChange={e => setTaConfigForm({ ...taConfigForm, emit_paper_trading_signal: e.target.checked })}
                />
                <label htmlFor="emit-paper-trading" className="font-medium cursor-pointer">
                  把 BUY 決策寫入模擬交易訊號
                </label>
              </div>
              <div className="text-[11px] text-muted-foreground">
                啟用後,TA 輸出 BUY 決策時會寫一條 StrategySignalRun,PaperTradingEngine 下個 tick 自動開模擬倉
                (停損 -5%,停利 +10%)。<strong>預設關閉</strong> 防止誤開倉。SELL 不會自動平倉。
              </div>
            </section>

            {/* 聯動觸發(intraday 急漲/急跌) */}
            {(() => {
              const autoTrigger = (taConfigForm.auto_trigger as Record<string, unknown>) || {}
              const setAuto = (patch: Record<string, unknown>) =>
                setTaConfigForm({ ...taConfigForm, auto_trigger: { ...autoTrigger, ...patch } })
              return (
                <section>
                  <div className="flex items-center gap-2 mb-2">
                    <input
                      type="checkbox"
                      id="auto-trigger-enabled"
                      checked={!!autoTrigger.enabled}
                      onChange={e => setAuto({ enabled: e.target.checked })}
                    />
                    <label htmlFor="auto-trigger-enabled" className="font-medium cursor-pointer">
                      盤中急漲/急跌自動觸發深度分析
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-3 mb-2">
                    <div>
                      <Label className="text-[12px]">漲跌幅閾值(%)</Label>
                      <Input
                        type="number"
                        step="0.5"
                        min={1}
                        max={20}
                        value={String(autoTrigger.change_pct_threshold ?? 5)}
                        onChange={e => setAuto({ change_pct_threshold: parseFloat(e.target.value) || 5 })}
                      />
                    </div>
                    <div>
                      <Label className="text-[12px]">冷卻時間(小時)</Label>
                      <Input
                        type="number"
                        min={1}
                        max={168}
                        value={String(autoTrigger.cooldown_hours ?? 24)}
                        onChange={e => setAuto({ cooldown_hours: parseInt(e.target.value) || 24 })}
                      />
                    </div>
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    啟用後,intraday_monitor 分析時若發現 |漲跌幅| ≥ 閾值,自動 fire-and-forget 觸發 TA 深度分析。
                    冷卻時間內同一標的不會重複觸發;月度預算用盡也會停止。<strong>預設關閉</strong> 避免成本失控。
                  </div>
                </section>
              )
            })()}

            {/* 高階 JSON */}
            <details className="text-[12px]">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                高階:完整 config JSON
              </summary>
              <textarea
                className="mt-2 w-full font-mono text-[11px] p-2 border border-border rounded bg-background min-h-[120px]"
                value={JSON.stringify(taConfigForm, null, 2)}
                onChange={e => {
                  try {
                    setTaConfigForm(JSON.parse(e.target.value))
                  } catch {
                    /* 輸入未完成,允許繼續編輯 */
                  }
                }}
              />
            </details>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setTaConfigAgent(null)}>取消</Button>
              <Button onClick={saveTaConfig}>儲存</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
