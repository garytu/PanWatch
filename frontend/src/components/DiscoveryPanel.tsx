import { DEFAULT_MARKET, MARKET_OPTIONS } from '@/lib/markets'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layers, RefreshCw } from 'lucide-react'
import {
  dashboardApi,
  discoveryApi,
  type DashboardMonitorStock,
  type DashboardPortfolioSummary,
  type DashboardWatchStock,
  type HotStockItem,
  type HotBoardItem,
  type TaiwanOfficialDiscoveryResponse,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { useLocalStorage } from '@/lib/utils'

interface Props {
  monitorStocks: DashboardMonitorStock[]
  onOpenStock: (symbol: string, market: string, name?: string, hasPosition?: boolean) => void
}

const officialReasonLabels: Record<string, string> = {
  price_pool_incomplete_or_capped: '價格範圍受限或尚未讀完',
  candidate_cap_reached: '僅篩選成交額前 20 檔候選',
  candidate_reads_incomplete: '部分候選逾時',
  candidate_data_reads_failed: '部分官方資料讀取失敗',
  official_factor_source_unavailable: '官方研究來源未啟用',
  discovery_concurrency_limit: '讀取名額已滿，請稍後重試',
  quote_source_disabled: '官方價格來源未啟用',
  no_current_eligible_prices: '沒有可用的股票／ETF 價格候選',
  catalog_unavailable_or_empty: '標的目錄為空',
  catalog_error: '標的目錄讀取失敗',
  timeout: '來源查詢逾時',
  screen_deadline_exceeded: '本次篩選逾時',
  provider_error: '來源讀取失敗',
  official_source_not_configured: '官方研究來源未啟用',
  unsupported_etf: 'ETF 不支援公司營收條件',
  unsupported_valuation_selector: '來源不支援此標的的估值查詢',
  coverage_missing: '查詢期別缺少資料',
  coverage_not_returned: '來源未提供覆蓋證據',
  value_missing: '所選期別缺少有效數值',
  stale: '資料超過新鮮度範圍',
  condition_not_met: '未達所選門檻',
}

const officialReasonLabel = (reason: string) => officialReasonLabels[reason] || reason

export default function DiscoveryPanel({ monitorStocks, onOpenStock }: Props) {
  const navigate = useNavigate()
  const [watchlist, setWatchlist] = useState<DashboardWatchStock[]>([])
  const [portfolioRaw, setPortfolioRaw] = useState<DashboardPortfolioSummary | null>(null)

  const [discoverTab, setDiscoverTab] = useLocalStorage<'boards' | 'stocks'>('panwatch_dashboard_discoverTab', 'boards')
  const [discoverMarket, setDiscoverMarket] = useLocalStorage<'CN' | 'HK' | 'US' | 'TW'>('panwatch_dashboard_discoverMarket_tw_v1', DEFAULT_MARKET)
  const [stocksMode, setStocksMode] = useLocalStorage<'turnover' | 'gainers' | 'for_you'>('panwatch_dashboard_stocksMode', 'for_you')
  const [boardsMode, setBoardsMode] = useLocalStorage<'gainers' | 'turnover'>('panwatch_dashboard_boardsMode', 'gainers')
  const [hotStocks, setHotStocks] = useState<HotStockItem[]>([])
  const [hotBoards, setHotBoards] = useState<HotBoardItem[]>([])
  const [discoverLoading, setDiscoverLoading] = useState(false)
  const [discoverError, setDiscoverError] = useState('')
  const [boardDialogOpen, setBoardDialogOpen] = useState(false)
  const [activeBoard, setActiveBoard] = useState<HotBoardItem | null>(null)
  const [boardStocks, setBoardStocks] = useState<HotStockItem[]>([])
  const [officialScreenOpen, setOfficialScreenOpen] = useState(false)
  const [officialScreenLoading, setOfficialScreenLoading] = useState(false)
  const [officialScreenError, setOfficialScreenError] = useState('')
  const [officialScreenResult, setOfficialScreenResult] = useState<TaiwanOfficialDiscoveryResponse | null>(null)
  const [officialFilters, setOfficialFilters] = useState({
    pe_max: '',
    pb_max: '',
    dividend_yield_min_pct: '',
    revenue_yoy_min_pct: '',
    institutional_net_min_shares: '',
  })
  const officialScreenSequence = useRef(0)
  const discoveryCacheRef = useRef<{
    boards: Record<string, { ts: number; data: HotBoardItem[] }>
    stocks: Record<string, { ts: number; data: HotStockItem[] }>
  }>({ boards: {}, stocks: {} })

  useEffect(() => {
    dashboardApi.watchlist().then(setWatchlist).catch(() => {})
    dashboardApi.portfolioSummary({ include_quotes: false }).then(setPortfolioRaw).catch(() => {})
  }, [])

  useEffect(() => {
    officialScreenSequence.current += 1
    setOfficialScreenLoading(false)
    setOfficialScreenResult(null)
  }, [discoverMarket])

  const watchlistSet = useMemo(
    () => new Set((watchlist || []).map((s) => `${s.market}:${s.symbol}`)),
    [watchlist],
  )
  const holdingSet = useMemo(() => {
    const set = new Set<string>()
    for (const acc of portfolioRaw?.accounts || []) for (const p of acc.positions || []) set.add(`${p.market}:${p.symbol}`)
    return set
  }, [portfolioRaw])
  const stylePreference = useMemo(() => {
    const score: Record<string, number> = { short: 0, swing: 0, long: 0 }
    for (const acc of portfolioRaw?.accounts || [])
      for (const p of acc.positions || []) {
        if (p.trading_style && p.trading_style in score) score[p.trading_style] += 1
      }
    const ranked = Object.entries(score).sort((a, b) => b[1] - a[1])
    return ranked[0]?.[1] ? ranked[0][0] : null
  }, [portfolioRaw])

  const loadDiscovery = async (which?: 'boards' | 'stocks', opts?: { silent?: boolean; force?: boolean }) => {
    const tab = which || discoverTab
    const silent = !!opts?.silent
    const force = !!opts?.force
    const cacheKey = tab === 'boards' ? `${discoverMarket}:${boardsMode}` : `${discoverMarket}:${stocksMode}`
    const now = Date.now()
    const ttlMs = 60 * 1000
    const cache = tab === 'boards' ? discoveryCacheRef.current.boards[cacheKey] : discoveryCacheRef.current.stocks[cacheKey]
    if (!force && cache && now - cache.ts < ttlMs) {
      if (tab === 'boards') setHotBoards(cache.data as HotBoardItem[])
      else setHotStocks(cache.data as HotStockItem[])
      return
    }
    if (!silent) {
      setDiscoverLoading(true)
      setDiscoverError('')
    }
    try {
      if (tab === 'boards') {
        const items = (await discoveryApi.listHotBoards({ market: discoverMarket, mode: boardsMode, limit: 12 })) || []
        setHotBoards(items)
        discoveryCacheRef.current.boards[cacheKey] = { ts: now, data: items }
      } else if (stocksMode === 'for_you') {
        const [turnoverItems, gainerItems] = await Promise.all([
          discoveryApi.listHotStocks({ market: discoverMarket, mode: 'turnover', limit: 20 }),
          discoveryApi.listHotStocks({ market: discoverMarket, mode: 'gainers', limit: 20 }),
        ])
        const map = new Map<string, HotStockItem>()
        for (const item of [...(turnoverItems || []), ...(gainerItems || [])]) map.set(item.symbol, item)
        const items = Array.from(map.values())
        setHotStocks(items)
        discoveryCacheRef.current.stocks[cacheKey] = { ts: now, data: items }
      } else {
        const items = (await discoveryApi.listHotStocks({ market: discoverMarket, mode: stocksMode, limit: 20 })) || []
        setHotStocks(items)
        discoveryCacheRef.current.stocks[cacheKey] = { ts: now, data: items }
      }
    } catch (e) {
      if (!silent) {
        setDiscoverError(e instanceof Error ? e.message : '載入失敗')
        if (tab === 'boards') setHotBoards([])
        else setHotStocks([])
      }
    } finally {
      if (!silent) setDiscoverLoading(false)
    }
  }

  const openBoard = async (b: HotBoardItem) => {
    setActiveBoard(b)
    setBoardStocks([])
    setBoardDialogOpen(true)
    try {
      setBoardStocks((await discoveryApi.listBoardStocks(b.code, { mode: 'gainers', limit: 20 })) || [])
    } catch {
      setBoardStocks([])
    }
  }

  const runOfficialScreen = async () => {
    const sequence = ++officialScreenSequence.current
    const request: Record<string, number> = {}
    for (const [key, raw] of Object.entries(officialFilters)) {
      if (!raw.trim()) continue
      const value = Number(raw)
      if (!Number.isFinite(value)) {
        setOfficialScreenError('條件必須是有效數字。')
        setOfficialScreenLoading(false)
        setOfficialScreenResult(null)
        return
      }
      request[key] = value
    }
    if (Object.keys(request).length === 0) {
      setOfficialScreenError('請至少填入一項官方資料條件。')
      setOfficialScreenLoading(false)
      setOfficialScreenResult(null)
      return
    }
    setOfficialScreenLoading(true)
    setOfficialScreenError('')
    try {
      const result = await discoveryApi.screenTaiwanOfficialStocks({ ...request, limit: 20 })
      if (sequence !== officialScreenSequence.current) return
      setOfficialScreenResult(result)
    } catch (error) {
      if (sequence !== officialScreenSequence.current) return
      setOfficialScreenError(error instanceof Error ? error.message : '官方條件選股暫時不可用。')
      setOfficialScreenResult(null)
    } finally {
      if (sequence === officialScreenSequence.current) setOfficialScreenLoading(false)
    }
  }

  const toggleOfficialScreen = () => {
    officialScreenSequence.current += 1
    setOfficialScreenLoading(false)
    setOfficialScreenOpen((open) => !open)
    setOfficialScreenError('')
  }

  const updateOfficialFilter = (key: keyof typeof officialFilters, value: string) => {
    officialScreenSequence.current += 1
    setOfficialScreenLoading(false)
    setOfficialScreenError('')
    setOfficialScreenResult(null)
    setOfficialFilters((current) => ({ ...current, [key]: value }))
  }

  const personalizedHotStocks = useMemo(() => {
    const monitorMap = new Map<string, DashboardMonitorStock>()
    for (const s of monitorStocks || []) monitorMap.set(`${s.market}:${s.symbol}`, s)
    const scored = (hotStocks || []).map((stock) => {
      const market = stock.market || discoverMarket
      const key = `${market}:${stock.symbol}`
      const reasons: string[] = []
      let score = 0
      const pctAbs = Math.abs(stock.change_pct || 0)
      score += Math.min((stock.turnover || 0) / 1e8, 8) + pctAbs * 0.6
      if (holdingSet.has(key)) {
        score += 10
        reasons.push('持倉相關')
      } else if (watchlistSet.has(key)) {
        score += 6
        reasons.push('自選相關')
      }
      const monitor = monitorMap.get(key)
      if (monitor?.suggestion?.should_alert || monitor?.alert_type) {
        score += 5
        reasons.push('監控訊號')
      }
      if (stylePreference === 'short' && pctAbs >= 3) {
        score += 3
        reasons.push('短線風格匹配')
      } else if (stylePreference === 'swing' && pctAbs >= 1.5 && pctAbs <= 6) {
        score += 2
        reasons.push('波段風格匹配')
      } else if (stylePreference === 'long' && pctAbs <= 4) {
        score += 2
        reasons.push('長線波動適中')
      }
      if (reasons.length === 0) reasons.push('市場活躍度高')
      return { ...stock, _score: score, _reasons: reasons.slice(0, 2) }
    })
    return scored.sort((a, b) => b._score - a._score)
  }, [hotStocks, holdingSet, watchlistSet, stylePreference, monitorStocks, discoverMarket])

  const visibleHotStocks = useMemo(
    () => (stocksMode === 'for_you' ? personalizedHotStocks.slice(0, 8) : hotStocks.slice(0, 8)),
    [stocksMode, personalizedHotStocks, hotStocks],
  )

  useEffect(() => {
    loadDiscovery('boards', { silent: true })
    loadDiscovery('stocks', { silent: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discoverMarket, boardsMode, stocksMode])

  return (
    <>
      <div className="mt-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Layers className="h-4 w-4 text-primary" />
            機會發現
          </h2>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => navigate('/opportunities')} className="h-7 text-[12px]">
              進入機會頁
            </Button>
            <Select value={discoverMarket} onValueChange={(v) => setDiscoverMarket(v as 'CN' | 'HK' | 'US' | 'TW')}>
              <SelectTrigger className="h-7 w-[90px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MARKET_OPTIONS.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => loadDiscovery(undefined, { force: true })}
              disabled={discoverLoading}
              className="h-7 text-[12px]"
              title="重新整理"
            >
              {discoverLoading ? (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-current/30 border-t-current" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>

        <div className="card p-4">
          <div className="mb-3 flex items-center gap-1.5">
            <button
              onClick={() => {
                setDiscoverTab('boards')
                loadDiscovery('boards')
              }}
              className={`rounded px-2.5 py-1 text-[11px] transition-colors ${discoverTab === 'boards' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
            >
              熱門板塊
            </button>
            <button
              onClick={() => {
                setDiscoverTab('stocks')
                loadDiscovery('stocks')
              }}
              className={`rounded px-2.5 py-1 text-[11px] transition-colors ${discoverTab === 'stocks' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
            >
              熱門股票
            </button>
            <div className="ml-auto flex items-center gap-2">
              {discoverTab === 'boards' ? (
                <Select value={boardsMode} onValueChange={(v) => { setBoardsMode(v as 'gainers' | 'turnover'); setTimeout(() => loadDiscovery('boards'), 0) }}>
                  <SelectTrigger className="h-7 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="gainers">漲幅榜</SelectItem>
                    <SelectItem value="turnover">成交額榜</SelectItem>
                  </SelectContent>
                </Select>
              ) : (
                <Select value={stocksMode} onValueChange={(v) => { setStocksMode(v as 'turnover' | 'gainers' | 'for_you'); setTimeout(() => loadDiscovery('stocks'), 0) }}>
                  <SelectTrigger className="h-7 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="for_you">For You</SelectItem>
                    <SelectItem value="turnover">成交額榜</SelectItem>
                    <SelectItem value="gainers">漲幅榜</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          {discoverTab === 'stocks' && discoverMarket === 'TW' && (
            <section className="mb-4 rounded-xl border border-border/70 bg-background/50 p-3" aria-label="台股官方條件選股">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-[13px] font-semibold">官方資料條件選股</div>
                  <div className="text-[11px] text-muted-foreground">從有限的官方成交額候選池篩選；法人條件只看最新單日資料。</div>
                </div>
                <Button variant="outline" size="sm" className="h-7 text-[11px]" onClick={toggleOfficialScreen}>
                  {officialScreenOpen ? '收合條件' : '設定條件'}
                </Button>
              </div>
              {officialScreenOpen && (
                <div className="mt-3 space-y-3">
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {([
                      ['pe_max', '本益比上限', '倍'],
                      ['pb_max', '股價淨值比上限', '倍'],
                      ['dividend_yield_min_pct', '殖利率下限', '%'],
                      ['revenue_yoy_min_pct', '營收年增率下限', '%'],
                      ['institutional_net_min_shares', '單日法人買賣超下限', '股'],
                    ] as const).map(([key, label, unit]) => (
                      <label key={key} className="space-y-1 text-[11px] text-muted-foreground">
                        <span>{label}（{unit}）</span>
                        <input
                          aria-label={label}
                          type="number"
                          step={key === 'institutional_net_min_shares' ? '1' : 'any'}
                          value={officialFilters[key]}
                          onChange={(event) => updateOfficialFilter(key, event.target.value)}
                          className="h-8 w-full rounded-md border border-input bg-background px-2 text-[12px] text-foreground"
                        />
                      </label>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" className="h-8 text-[11px]" onClick={() => void runOfficialScreen()} disabled={officialScreenLoading}>
                      {officialScreenLoading ? '官方資料篩選中…' : '執行官方條件篩選'}
                    </Button>
                    <span className="text-[10px] text-muted-foreground">營收使用有留存資料的最新月份；來源未提供發布時間。缺漏、過期或不支援的條件不會以零補值。</span>
                  </div>
                  {officialScreenError && <div role="alert" className="text-[11px] text-destructive">{officialScreenError}</div>}
                  {officialScreenResult && (
                    <div className="space-y-3 border-t border-border/60 pt-3">
                      <div className="grid grid-cols-1 gap-1 text-[10px] text-muted-foreground sm:grid-cols-2">
                        <div>候選範圍：目錄 {officialScreenResult.scope.eligible_catalog_count ?? '未知'} 檔，價格掃描 {officialScreenResult.scope.price_universe_scanned ?? 0}/{officialScreenResult.scope.price_universe_selected_count ?? 0} 檔，前 {officialScreenResult.scope.candidate_limit} 檔成交額候選</div>
                        <div>結果：符合 {officialScreenResult.scope.matched_count} 檔，排除 {officialScreenResult.scope.excluded_count} 檔；資料日期分別顯示於各條件</div>
                        <div>價格資料日：{officialScreenResult.scope.price_data_dates?.join('、') || '無'}</div>
                        <div>官方讀取 {officialScreenResult.scope.request_counts.total ?? 0} 次（HTTP {officialScreenResult.scope.request_counts.total_http_attempts ?? '—'} 次），快取命中 {officialScreenResult.scope.request_counts.cache_hits ?? 0} 次；{officialScreenResult.scope.partial_scan ? `部分掃描：${officialScreenResult.scope.partial_reasons.map(officialReasonLabel).join('、')}` : '所列候選已檢查完成'}</div>
                        <div className="sm:col-span-2">官方查詢期間：日資料 {officialScreenResult.selectors.daily_start_date} 至 {officialScreenResult.selectors.daily_end_date}；月營收 {officialScreenResult.selectors.revenue_start_month} 至 {officialScreenResult.selectors.revenue_end_month}。法人只使用最新有資料的單日，未啟用連買日數條件。</div>
                        <div className="sm:col-span-2">估值／法人限 7 個日曆日內的資料，營收限月份月底起 90 日內；各資料日分開判定。{officialScreenResult.scope.limits && `本次期限 ${officialScreenResult.scope.limits.aggregate_timeout_seconds} 秒，最多 ${officialScreenResult.scope.limits.concurrent_factor_reads} 個研究讀取並行。`}</div>
                      </div>
                      {Object.entries(officialScreenResult.conditions).map(([key, condition]) => (
                        <span key={key} className="mr-1 inline-flex rounded-full bg-accent/50 px-2 py-1 text-[10px]">{condition.label} {condition.operator} {condition.threshold} {condition.unit}</span>
                      ))}
                      {officialScreenResult.matches.length === 0 ? (
                        <div className="py-2 text-[11px] text-muted-foreground">
                          {officialScreenResult.scope.scan_status && !['available', 'partial'].includes(officialScreenResult.scope.scan_status)
                            ? `${officialReasonLabel(officialScreenResult.scope.scan_status)}，本次沒有可用的篩選結果。`
                            : officialScreenResult.scope.partial_scan
                              ? '已完成檢查的候選中沒有符合全部條件的股票；部分資料尚未完成。'
                              : '目前候選中沒有符合全部條件的股票。'}
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {officialScreenResult.matches.map((candidate) => (
                            <button
                              key={candidate.instrument_id}
                              onClick={() => onOpenStock(candidate.instrument_id, 'TW', candidate.name, false)}
                              className="w-full rounded-lg bg-accent/25 p-2 text-left hover:bg-accent/40"
                            >
                              <div className="flex items-center justify-between gap-2 text-[12px]">
                                <span className="font-medium">{candidate.name} <span className="font-mono text-muted-foreground">{candidate.instrument_id} · {candidate.security_type}</span></span>
                                <span className="font-mono">成交額 {candidate.price.turnover ?? '—'}</span>
                              </div>
                              {candidate.explanations.map((explanation, index) => (
                                <div key={`${candidate.instrument_id}-explanation-${index}`} className="mt-1 text-[10px] text-muted-foreground">{explanation}</div>
                              ))}
                              {Object.entries(candidate.condition_results).map(([key, result]) => (
                                <div key={`${candidate.instrument_id}-${key}`} className="mt-1 text-[10px] text-muted-foreground">
                                  {result.label}：{result.value ?? '無資料'}（{result.data_date ?? '日期未知'}）{candidate.data_evidence?.[officialScreenResult.conditions[key]?.dataset]?.source_contract ? ` · ${candidate.data_evidence[officialScreenResult.conditions[key]?.dataset]?.source_contract}` : ''}
                                </div>
                              ))}
                              {candidate.data_evidence?.valuation?.dividend_reference_year != null && (
                                <div className="mt-1 text-[10px] text-muted-foreground">股利參考年度：{candidate.data_evidence.valuation.dividend_reference_year}（來源提供殖利率，未推定為目前年化殖利率）</div>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                      {officialScreenResult.excluded.length > 0 && (
                        <details className="text-[10px] text-muted-foreground">
                          <summary className="cursor-pointer">查看排除項目與原因（{officialScreenResult.excluded.length}）</summary>
                          <div className="mt-2 max-h-40 space-y-1 overflow-auto">
                            {officialScreenResult.excluded.map((candidate) => (
                              <div key={candidate.instrument_id}>
                                <span className="font-mono">{candidate.instrument_id}</span>：{candidate.exclusion_reasons.map(officialReasonLabel).join('、') || '條件未通過'}
                                {candidate.explanations.map((explanation, index) => <div key={`${candidate.instrument_id}-excluded-${index}`} className="pl-2">{explanation}</div>)}
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                    </div>
                  )}
                </div>
              )}
            </section>
          )}

          {discoverLoading ? (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="animate-pulse rounded-xl bg-accent/20 p-3">
                  <div className="mb-2 h-3 w-24 rounded bg-accent/60" />
                  <div className="h-3 w-16 rounded bg-accent/50" />
                </div>
              ))}
            </div>
          ) : discoverTab === 'boards' ? (
            hotBoards.length === 0 ? (
              <div className="py-6 text-center text-[12px] text-muted-foreground">
                {discoverError || (discoverMarket === 'CN' ? '暫無資料' : `${discoverMarket === 'HK' ? '港股' : '美股'}暫不提供板塊榜，已支援熱門股票`)}
                {discoverMarket !== 'CN' && (
                  <div className="mt-2">
                    <Button variant="ghost" size="sm" className="h-7 text-[11px]" onClick={() => setDiscoverTab('stocks')}>
                      切換到熱門股票
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {hotBoards.slice(0, 6).map((b) => {
                  const pct = b.change_pct ?? 0
                  const color = pct > 0 ? 'text-rose-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'
                  return (
                    <button
                      key={b.code}
                      onClick={() => openBoard(b)}
                      className="flex items-center justify-between gap-3 rounded-xl bg-accent/20 p-3 text-left transition-colors hover:bg-accent/35"
                      title="查看板塊成分股"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium text-foreground">{b.name}</div>
                        <div className="truncate font-mono text-[11px] text-muted-foreground">{b.code}</div>
                      </div>
                      <div className={`font-mono text-[12px] font-semibold ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                    </button>
                  )
                })}
              </div>
            )
          ) : hotStocks.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">{discoverError || '暫無資料'}</div>
          ) : (
            <div className="space-y-2">
              {stocksMode === 'for_you' && <div className="px-1 text-[11px] text-muted-foreground">根據持倉/自選/監控訊號/風格偏好排序</div>}
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {visibleHotStocks.slice(0, 6).map((s) => {
                  const pct = s.change_pct ?? 0
                  const color = pct > 0 ? 'text-rose-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'
                  const reasons = (s as HotStockItem & { _reasons?: string[] })._reasons
                  return (
                    <div
                      key={`${s.market || discoverMarket}:${s.symbol}`}
                      onClick={() => onOpenStock(s.symbol, s.market || discoverMarket, s.name, false)}
                      className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-accent/20 p-3 text-left transition-colors hover:bg-accent/35"
                      title="開啟股票詳細資訊彈跳視窗"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium text-foreground">{s.name}</div>
                        <div className="font-mono text-[11px] text-muted-foreground">{s.market || discoverMarket}:{s.symbol}</div>
                        {reasons && reasons.length > 0 && (
                          <div className="mt-0.5 truncate text-[10px] text-muted-foreground">{reasons.join(' · ')}</div>
                        )}
                      </div>
                      <div className="text-right">
                        <div className="font-mono text-[12px] text-foreground">{s.price != null ? s.price.toFixed(2) : '--'}</div>
                        <div className={`font-mono text-[11px] ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      <Dialog open={boardDialogOpen} onOpenChange={setBoardDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{activeBoard ? `板塊：${activeBoard.name}` : '板塊成分股'}</DialogTitle>
            <DialogDescription>點選個股開啟統一詳細資訊彈跳視窗（含概覽、K線、建議、新聞、歷史）</DialogDescription>
          </DialogHeader>
          {boardStocks.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">暫無資料</div>
          ) : (
            <div className="scrollbar grid max-h-[60vh] grid-cols-1 gap-2 overflow-y-auto md:grid-cols-2">
              {boardStocks.map((s) => {
                const pct = s.change_pct ?? 0
                const color = pct > 0 ? 'text-rose-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'
                return (
                  <div
                    key={s.symbol}
                    onClick={() => {
                      setBoardDialogOpen(false)
                      onOpenStock(s.symbol, s.market || DEFAULT_MARKET, s.name, false)
                    }}
                    className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-accent/20 p-3 text-left transition-colors hover:bg-accent/35"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-medium text-foreground">{s.name}</div>
                      <div className="font-mono text-[11px] text-muted-foreground">{s.symbol}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-[12px] text-foreground">{s.price != null ? s.price.toFixed(2) : '--'}</div>
                      <div className={`font-mono text-[11px] ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
