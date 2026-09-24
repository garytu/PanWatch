import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { RefreshCw, AlertTriangle, Sparkles, Activity, ShieldAlert, Newspaper, Share2 } from 'lucide-react'
import {
  dashboardApi,
  portfolioApi,
  recommendationsApi,
  homeApi,
  type DashboardMarketIndex,
  type DashboardMarketStatus,
  type DashboardMonitorStock,
  type DashboardOverviewResponse,
  type DashboardPortfolioSummary,
  type PortfolioDiagnostics,
  type PortfolioBenchmark,
  type StrategySignalItem,
  type AlertHitToday,
  type PortfolioTodo,
  type CurateCandidate,
  type CuratedItem,
  type AttributionItem,
  type PortfolioAiReview,
  type DashboardBrief,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Onboarding } from '@panwatch/biz-ui/components/onboarding'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import DiscoveryPanel from '@/components/DiscoveryPanel'
import Sparkline from '@/components/Sparkline'
import BenchChart from '@/components/BenchChart'
import BenchmarkShareCard from '@/components/BenchmarkShareCard'
import DiagnosticsShareCard from '@/components/DiagnosticsShareCard'
import DigestShareCard from '@/components/DigestShareCard'

function pct(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}
function moveColor(v?: number | null): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-rose-500' : v < 0 ? 'text-emerald-500' : 'text-muted-foreground'
}
/** 漲跌著色 chip 的背景+文字類;null/平盤 → 灰底。紅漲綠跌(A股口徑)。 */
function pctChipCls(v?: number | null): string {
  if (v == null) return 'bg-accent text-muted-foreground'
  if (v > 0) return 'bg-rose-500/10 text-rose-500'
  if (v < 0) return 'bg-emerald-500/10 text-emerald-500'
  return 'bg-accent text-muted-foreground'
}
/** 金額展示:+¥2,175 風格(千分位 + 正負號),脫敏場景外的常規展示用。 */
function fmtMoney(v?: number | null): string {
  if (v == null || !isFinite(v)) return '--'
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  return `${sign}¥${Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}
/** 去掉常見 markdown 標記,供簡報摘要行取純文本用。 */
function stripMarkdown(s: string): string {
  return s
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[.*?\]\(.*?\)/g, '')
    .replace(/\[(.*?)\]\(.*?\)/g, '$1')
    .replace(/[#*_>`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}
const WEEKDAY_LABEL = ['日', '一', '二', '三', '四', '五', '六']
function formatHeaderTime(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day} 周${WEEKDAY_LABEL[d.getDay()]} · ${hh}:${mm} 已重新整理`
}
const ALERT_LABEL: Record<string, string> = {
  surge: '快速拉昇',
  plunge: '快速跳水',
  high_volume: '放量異動',
  breakout: '突破',
  breakdown: '破位',
  limit_up: '漲停',
  limit_down: '跌停',
}

const FEED_BADGE: Record<string, { label: string; cls: string }> = {
  alert: { label: '提醒命中', cls: 'bg-rose-500/15 text-rose-500' },
  holding: { label: '持倉', cls: 'bg-emerald-500/15 text-emerald-500' },
  watch: { label: '自選', cls: 'bg-accent text-muted-foreground' },
  risk: { label: '風險', cls: 'bg-amber-500/15 text-amber-600' },
  opportunity: { label: '機會', cls: 'bg-primary/10 text-primary' },
}

// 市場分佈 stacked 條配色:CN 用品牌色,US/HK 用差異化色區分
const MARKET_BAR_CLS: Record<string, string> = {
  CN: 'bg-primary',
  US: 'bg-emerald-500',
  HK: 'bg-orange-500',
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [indices, setIndices] = useState<DashboardMarketIndex[]>([])
  const [scan, setScan] = useState<DashboardMonitorStock[]>([])
  const [overview, setOverview] = useState<DashboardOverviewResponse | null>(null)
  const [diag, setDiag] = useState<PortfolioDiagnostics | null>(null)
  const [bench, setBench] = useState<PortfolioBenchmark | null>(null)
  const [benchState, setBenchState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading')
  const [oppFallback, setOppFallback] = useState<StrategySignalItem[]>([])
  const [alertHits, setAlertHits] = useState<AlertHitToday[]>([])
  const [todos, setTodos] = useState<PortfolioTodo[]>([])
  const [curated, setCurated] = useState<CuratedItem[]>([])
  const [attribution, setAttribution] = useState<AttributionItem[]>([])
  const [aiReview, setAiReview] = useState<PortfolioAiReview | null>(null)
  const [aiReviewLoading, setAiReviewLoading] = useState(false)
  const [brief, setBrief] = useState<DashboardBrief | null>(null)
  const [briefOpen, setBriefOpen] = useState(false)
  const [portfolioSummary, setPortfolioSummary] = useState<DashboardPortfolioSummary | null>(null)
  const [marketStatus, setMarketStatus] = useState<DashboardMarketStatus[]>([])
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)
  // 分享卡開關:成績單(基準)/ 組合體檢 / 每日 digest
  const [shareBench, setShareBench] = useState(false)
  const [shareDiag, setShareDiag] = useState(false)
  const [shareDigest, setShareDigest] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [modal, setModal] = useState<{ open: boolean; symbol: string; market: string; name: string; hasPosition: boolean }>({
    open: false,
    symbol: '',
    market: 'CN',
    name: '',
    hasPosition: false,
  })

  // 慢車道:基準/歸因(拉全持倉 K 線,分鐘級);獨立可重試,失敗/為空各有明確狀態
  const loadBench = useCallback(() => {
    setBenchState('loading')
    Promise.allSettled([portfolioApi.benchmark({ days: 60 }), portfolioApi.attribution(60)]).then(([bn, at]) => {
      if (bn.status === 'fulfilled') {
        setBench(bn.value)
        setBenchState(!bn.value?.empty && (bn.value?.curve?.length ?? 0) >= 2 ? 'ready' : 'empty')
      } else {
        setBenchState('error')
      }
      if (at.status === 'fulfilled') setAttribution(at.value.items || [])
    })
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    // 指數 pills:獨立載入不阻塞首屏(spark 冷啟動可能 ~1s,資料到了自然浮現)
    dashboardApi.indices().then(setIndices).catch(() => {})
    // 快車道:DB/輕量查詢,先讓首屏(要緊事/體檢分佈/組合速覽)儘快出來
    const [sc, ov, dg, ht, td, ps, ms] = await Promise.allSettled([
      dashboardApi.intradayScan(),
      dashboardApi.overview({ market: 'ALL', action_limit: 6, risk_limit: 6 }),
      portfolioApi.diagnostics(),
      homeApi.alertHitsToday(),
      homeApi.todos(),
      dashboardApi.portfolioSummary(),
      dashboardApi.marketStatus(),
    ])
    if (sc.status === 'fulfilled') setScan(sc.value.stocks || [])
    if (ov.status === 'fulfilled') setOverview(ov.value)
    if (dg.status === 'fulfilled') setDiag(dg.value)
    if (ht.status === 'fulfilled') setAlertHits(ht.value)
    if (td.status === 'fulfilled') setTodos(td.value.todos || [])
    if (ps.status === 'fulfilled') setPortfolioSummary(ps.value)
    if (ms.status === 'fulfilled') setMarketStatus(ms.value)
    setLoading(false) // 首屏不再等基準/歸因(要拉全持倉 K 線)
    setRefreshedAt(new Date())

    // 機會兜底:overview 無機會時再取(不擋首屏)
    if (ov.status !== 'fulfilled' || !ov.value.action_center?.opportunities?.length) {
      recommendationsApi
        .listStrategySignals({ status: 'active', limit: 5 })
        .then((r) => setOppFallback(r.items || []))
        .catch(() => {})
    }

    // 慢車道:基準/歸因需拉全持倉 K 線(分鐘級),獨立載入,就緒後回填超額/歸因
    loadBench()

    // 盤前/盤後簡報:獨立載入,取較新一條
    Promise.allSettled([dashboardApi.brief('premarket'), dashboardApi.brief('eod')]).then((res) => {
      const briefs = res
        .filter((b): b is PromiseFulfilledResult<DashboardBrief> => b.status === 'fulfilled' && !b.value.empty)
        .map((b) => b.value)
      briefs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
      setBrief(briefs[0] || null)
    })
  }, [loadBench])

  useEffect(() => {
    load()
    if (!localStorage.getItem('panwatch_onboarding_completed')) setShowOnboarding(true)
  }, [load])

  const handleOnboardingComplete = () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    setShowOnboarding(false)
  }

  const openStock = (symbol: string, market: string, name = '', hasPosition = false) =>
    setModal({ open: true, symbol, market: market || 'CN', name, hasPosition })

  const runAiReview = async () => {
    setAiReviewLoading(true)
    try {
      setAiReview(await portfolioApi.aiReview())
    } catch (e) {
      setAiReview({ content: e instanceof Error ? `AI 體檢失敗: ${e.message}` : 'AI 體檢失敗' })
    } finally {
      setAiReviewLoading(false)
    }
  }

  // 今日要緊事:持倉異動 + 觸發的盯盤訊號(有 AI 建議/告警優先)
  const urgent = useMemo(() => {
    const items = (scan || []).filter((s) => s.has_position || s.alert_type || s.suggestion?.should_alert)
    const weight = (s: DashboardMonitorStock) =>
      (s.suggestion?.should_alert ? 1000 : 0) + (s.has_position ? 500 : 0) + Math.abs(s.change_pct || 0)
    return items.sort((a, b) => weight(b) - weight(a)).slice(0, 8)
  }, [scan])

  const opportunities = useMemo(() => {
    const list = overview?.action_center?.opportunities?.length ? overview.action_center.opportunities : oppFallback
    return list.slice(0, 5)
  }, [overview, oppFallback])

  // 今日必讀候選(多源)→ 交 AI 策展(失敗兜底原序)
  const candidates = useMemo<CurateCandidate[]>(() => {
    const out: CurateCandidate[] = []
    for (const h of alertHits) {
      out.push({ type: 'alert', symbol: h.symbol, name: h.name || h.symbol, market: h.market, signal: `觸發提醒 ${h.rule_name}` })
    }
    for (const s of urgent) {
      out.push({
        type: s.has_position ? 'holding' : 'watch',
        symbol: s.symbol,
        name: s.name,
        market: s.market,
        change_pct: s.change_pct,
        signal: s.suggestion?.signal || (s.alert_type ? ALERT_LABEL[s.alert_type] || s.alert_type : ''),
      })
    }
    for (const a of diag?.alerts || []) out.push({ type: 'risk', name: '組合風險', market: '', signal: a })
    for (const o of opportunities.slice(0, 3)) {
      out.push({ type: 'opportunity', symbol: o.stock_symbol, name: o.stock_name || o.stock_symbol, market: o.stock_market, signal: o.signal || o.reason || o.action_label || '' })
    }
    return out
  }, [alertHits, urgent, diag, opportunities])

  const candKey = useMemo(
    () => candidates.map((c) => `${c.type}:${c.symbol}:${c.change_pct ?? ''}`).join('|'),
    [candidates],
  )

  useEffect(() => {
    if (candidates.length === 0) {
      setCurated([])
      return
    }
    let alive = true
    dashboardApi
      .curate(candidates)
      .then((r) => alive && setCurated(r.items || []))
      .catch(() => alive && setCurated([]))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candKey])

  const feed = useMemo(() => {
    const rows = curated.length
      ? curated.map((ci) => (candidates[ci.index] ? { ...candidates[ci.index], why: ci.why } : null))
      : candidates.map((c) => ({ ...c, why: c.signal }))
    return rows.filter((x): x is CurateCandidate & { why: string } => !!x)
  }, [curated, candidates])

  const today = useMemo(() => {
    const d = new Date()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    return `${d.getFullYear()}-${mm}-${dd}`
  }, [])
  const hasHoldings = (diag?.position_count ?? 0) > 0
  const benchReady = bench && !bench.empty && bench.excess_return != null
  const hasWatchlist = (overview?.kpis?.watchlist_count ?? 0) > 0
  const portfolioPnlPct =
    diag && diag.total_market_value - diag.total_unrealized_pnl > 0
      ? (diag.total_unrealized_pnl / (diag.total_market_value - diag.total_unrealized_pnl)) * 100
      : null

  // 今日損益(組合速覽條 hero):來自 portfolioSummary.total.total_daily_pnl(與 Stocks 頁同源欄位)
  const dailyPnl = portfolioSummary?.total?.total_daily_pnl ?? null
  const dailyPnlPct = useMemo(() => {
    if (!portfolioSummary || dailyPnl == null) return null
    const basis = portfolioSummary.total.total_market_value - dailyPnl
    return basis > 0 ? (dailyPnl / basis) * 100 : null
  }, [portfolioSummary, dailyPnl])
  const positionRatioPct = useMemo(() => {
    if (!portfolioSummary) return null
    const { total_market_value, total_assets } = portfolioSummary.total
    return total_assets > 0 ? (total_market_value / total_assets) * 100 : null
  }, [portfolioSummary])
  const benchPortfolioSeries = useMemo(() => (bench?.curve || []).map((p) => p.portfolio), [bench])

  // 市場分佈 stacked 條的分段(佔比降序,過濾掉 0 佔比)
  const marketSegs = useMemo(() => {
    if (!diag || diag.total_market_value <= 0) return []
    return Object.entries(diag.by_market)
      .map(([market, value]) => ({ market, pct: (value / diag.total_market_value) * 100 }))
      .filter((s) => s.pct > 0.05)
      .sort((a, b) => b.pct - a.pct)
  }, [diag])

  // 領漲/拖累雙向條的歸一基準(取全量 attribution 裡最大貢獻絕對值,雙向對稱)
  const attributionMaxAbs = useMemo(() => {
    if (attribution.length === 0) return 0
    return Math.max(...attribution.map((a) => Math.abs(a.contribution_pct)), 0.01)
  }, [attribution])

  const briefSummary = useMemo(() => {
    if (!brief?.content) return ''
    const stripped = stripMarkdown(brief.content)
    return stripped.length > 120 ? `${stripped.slice(0, 120)}…` : stripped
  }, [brief])

  return (
    <div className="page-container pb-10">
      {/* 頂部:標題 + 重新整理 + 日期/市場狀態 pills */}
      <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-[20px] font-bold tracking-tight text-foreground md:text-[22px]">今日該看什麼</h1>
          <Button onClick={load} disabled={loading} size="sm" variant="ghost" className="h-7 px-2">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          {refreshedAt && <span className="text-muted-foreground">{formatHeaderTime(refreshedAt)}</span>}
          {marketStatus.map((m) => (
            <span key={m.code} className="inline-flex items-center gap-1.5 rounded-full bg-accent/40 px-2 py-0.5">
              <span className={`h-1.5 w-1.5 rounded-full ${m.is_trading ? 'bg-amber-500' : 'bg-muted-foreground/40'}`} />
              <span className="text-muted-foreground">{m.name}</span>
            </span>
          ))}
        </div>
      </div>

      {/* 組合速覽條:今日損益 hero + 累計未實現獲利 + 60日超額 + 倉位% + mini 淨值走勢 */}
      <div className="card mb-3 p-4">
        {!hasHoldings ? (
          <div className="py-4 text-center text-[12px] text-muted-foreground">
            {loading ? '載入中…' : '暫無持倉,新增持倉後這裡展示今日損益與組合走勢'}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <div>
              <div className="text-[11px] text-muted-foreground">今日損益</div>
              <div className={`font-mono text-[22px] font-bold leading-tight ${moveColor(dailyPnl)}`}>{fmtMoney(dailyPnl)}</div>
              {dailyPnlPct != null && <div className={`font-mono text-[11px] ${moveColor(dailyPnlPct)}`}>{pct(dailyPnlPct)}</div>}
            </div>
            <div className="hidden h-9 w-px bg-border/60 sm:block" />
            <div>
              <div className="text-[11px] text-muted-foreground">累計未實現獲利</div>
              <div className={`font-mono text-[14px] ${moveColor(diag!.total_unrealized_pnl)}`}>
                {fmtMoney(diag!.total_unrealized_pnl)} <span className="text-[11px]">{pct(portfolioPnlPct)}</span>
              </div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">60日超額</div>
              <div className={`font-mono text-[14px] ${benchReady ? moveColor(bench!.excess_return) : 'text-muted-foreground'}`}>
                {benchReady ? pct(bench!.excess_return) : '--'}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">倉位</div>
              <div className="font-mono text-[14px]">{positionRatioPct != null ? `${positionRatioPct.toFixed(0)}%` : '--'}</div>
            </div>
            <div className="ml-auto flex items-center gap-3">
              <div className="w-24">
                <Sparkline data={benchPortfolioSeries} height={32} className="text-primary" />
              </div>
              <button
                type="button"
                onClick={() => navigate('/portfolio')}
                className="shrink-0 text-[11px] text-muted-foreground hover:text-primary"
              >
                持倉頁 →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 指數走勢 pills */}
      <div className="mb-3 grid grid-cols-2 gap-2.5 md:grid-cols-3 lg:grid-cols-5">
        {indices.slice(0, 5).map((ix) => (
          <div key={`${ix.market}:${ix.symbol}`} className="card-subtle relative p-2.5">
            <div className="flex items-start justify-between gap-1">
              <div className="min-w-0">
                <div className="truncate text-[11px] text-muted-foreground">{ix.name}</div>
                <div className="font-mono text-[15px] text-foreground">
                  {ix.current_price != null ? ix.current_price.toFixed(2) : '--'}
                </div>
              </div>
              <span className={`shrink-0 rounded px-1 py-0.5 font-mono text-[10px] ${pctChipCls(ix.change_pct)}`}>
                {ix.change_pct != null ? pct(ix.change_pct) : '--'}
              </span>
            </div>
            {ix.spark && ix.spark.length >= 2 && (
              <div className="mt-1.5">
                <Sparkline data={ix.spark} height={26} className={moveColor(ix.change_pct)} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 主體:要緊事(7) | 體檢(5);機會(5) | 簡報(7) */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
        {/* 今日要緊事(主角) */}
        <div className="card p-4 lg:col-span-7">
          <div className="mb-2 flex items-center gap-2">
            <Activity className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">今日要緊事</h2>
            <span className="text-[11px] text-muted-foreground">你的持倉/自選裡今天該關注的</span>
            {feed.length > 0 && (
              <button
                type="button"
                onClick={() => setShareDigest(true)}
                className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
                title="生成今日盯盤分享圖"
              >
                <Share2 className="h-3.5 w-3.5" />
                分享圖
              </button>
            )}
          </div>
          {loading && candidates.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">掃描中…</div>
          ) : candidates.length === 0 ? (
            todos.length > 0 ? (
              <div className="space-y-1.5 py-1">
                <div className="text-[11px] text-muted-foreground">今日暫無異動/觸發 ✓ · 待辦:</div>
                {todos.map((t, i) => (
                  <div
                    key={i}
                    className={`flex items-center gap-2 py-1 text-[12px] ${t.symbol ? 'cursor-pointer hover:bg-accent/30' : ''}`}
                    onClick={() => t.symbol && openStock(t.symbol, t.market || 'CN', '')}
                  >
                    <span className="shrink-0 rounded bg-amber-500/15 px-1 text-[9px] text-amber-600">
                      {t.type === 'no_alert' ? '加提醒' : '將到期'}
                    </span>
                    <span className="truncate">{t.message}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-6 text-center text-[12px] text-muted-foreground">今日暫無明顯異動或觸發訊號 ✓</div>
            )
          ) : (
            <div className="divide-y divide-border/40">
              {feed.map((it, i) => {
                const badge = FEED_BADGE[it.type] || { label: it.type, cls: 'bg-accent text-muted-foreground' }
                return (
                  <div
                    key={i}
                    className={`flex items-center gap-3 py-2 ${it.symbol ? 'cursor-pointer hover:bg-accent/30' : ''}`}
                    onClick={() => it.symbol && openStock(it.symbol, it.market || 'CN', it.name || '')}
                  >
                    <span className={`shrink-0 rounded px-1 text-[9px] ${badge.cls}`}>{badge.label}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium">{it.name || it.symbol}</div>
                      {it.why && <div className="truncate text-[11px] text-muted-foreground">{it.why}</div>}
                    </div>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] ${pctChipCls(it.change_pct)}`}>
                      {it.change_pct != null ? pct(it.change_pct) : '--'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 組合體檢(併入首頁) */}
        <div className="card p-4 lg:col-span-5">
          <div className="mb-2 flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">組合體檢</h2>
            {benchReady && (
              <button
                type="button"
                onClick={() => setShareBench(true)}
                className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
                title="生成模擬交易成績單分享圖"
              >
                <Share2 className="h-3.5 w-3.5" />
                成績單
              </button>
            )}
            {hasHoldings && (
              <button
                type="button"
                onClick={() => setShareDiag(true)}
                className={`${benchReady ? '' : 'ml-auto'} inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary`}
                title="生成組合體檢分享圖"
              >
                <Share2 className="h-3.5 w-3.5" />
                體檢圖
              </button>
            )}
          </div>
          {!hasHoldings ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">
              {loading ? '載入中…' : '暫無持倉,新增持倉後這裡給風險與相對大盤表現'}
            </div>
          ) : (
            <div className="space-y-3 text-[12px]">
              {/* 圖例行:色塊 + 我的組合/基準收益 + 超額 chip */}
              <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1.5">
                    <span className="h-[3px] w-3.5 rounded-full bg-primary" />
                    <span className="text-muted-foreground">我的組合 {benchReady ? pct(bench!.portfolio_return) : ''}</span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-0 w-3.5 border-t-[1.5px] border-dashed border-muted-foreground/70" />
                    <span className="text-muted-foreground">
                      {bench?.benchmark_label || '滬深300'} {benchReady ? pct(bench!.benchmark_return) : ''}
                    </span>
                  </span>
                </div>
                {benchReady && (
                  <span className={`rounded px-1.5 py-0.5 font-mono ${pctChipCls(bench!.excess_return)}`}>
                    超額 {pct(bench!.excess_return)}
                  </span>
                )}
              </div>

              {/* 淨值 vs 基準雙線圖:loading/ready/empty/error 四態,不再永遠"計算中" */}
              {benchState === 'ready' && bench?.curve && bench.curve.length >= 2 ? (
                <BenchChart curve={bench.curve} />
              ) : (
                <div className="flex h-[150px] flex-col items-center justify-center gap-2 rounded-lg bg-accent/10 text-[11px] text-muted-foreground">
                  {benchState === 'loading' && <span>基準對比計算中…(需拉全部持倉 K 線,約 1 分鐘)</span>}
                  {benchState === 'empty' && <span>{bench?.reason || '資料不足,暫無法計算基準對比'}</span>}
                  {benchState === 'error' && (
                    <>
                      <span>基準對比載入失敗(超時或網路異常)</span>
                      <button
                        type="button"
                        onClick={loadBench}
                        className="rounded border border-border/60 px-2.5 py-1 text-[11px] text-primary hover:bg-accent/30"
                      >
                        重試
                      </button>
                    </>
                  )}
                </div>
              )}

              <div className="flex justify-between">
                <span className="text-muted-foreground">持倉 {diag!.position_count} 只 · 最大單倉</span>
                <span className={`font-mono ${diag!.max_weight >= 0.4 ? 'text-amber-600' : ''}`}>
                  {(diag!.max_weight * 100).toFixed(0)}%
                </span>
              </div>

              {/* 市場分佈:stacked 單條 */}
              {marketSegs.length > 0 && (
                <div>
                  <div className="flex h-2 overflow-hidden rounded-full bg-accent/30">
                    {marketSegs.map((seg, i) => (
                      <div
                        key={seg.market}
                        className={`h-full ${MARKET_BAR_CLS[seg.market] || 'bg-muted-foreground/50'}`}
                        style={{ width: `${seg.pct}%`, marginRight: i < marketSegs.length - 1 ? 2 : 0 }}
                      />
                    ))}
                  </div>
                  <div className="mt-1 text-[10.5px] text-muted-foreground">
                    {marketSegs.map((seg) => `${seg.market} ${seg.pct.toFixed(0)}%`).join(' · ')}
                  </div>
                </div>
              )}

              {/* 領漲/拖累:雙向條 */}
              {attribution.length > 1 &&
                [
                  { label: '領漲', item: attribution[0] },
                  { label: '拖累', item: attribution[attribution.length - 1] },
                ].map(({ label, item }) => {
                  const w = Math.min(50, (Math.abs(item.contribution_pct) / attributionMaxAbs) * 50)
                  const positive = item.contribution_pct >= 0
                  return (
                    <div key={label} className="flex items-center gap-2">
                      <span className="w-8 shrink-0 text-[10px] text-muted-foreground">{label}</span>
                      <div className="relative h-1.5 flex-1 rounded-full bg-accent/30">
                        <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                        <div
                          className={`absolute inset-y-0 rounded-full ${positive ? 'bg-rose-500' : 'bg-emerald-500'}`}
                          style={
                            positive
                              ? { left: '50%', width: `${w}%` }
                              : { right: '50%', width: `${w}%` }
                          }
                        />
                      </div>
                      <span className="w-28 shrink-0 truncate text-right text-[11px]">
                        {item.name} <span className={`font-mono ${moveColor(item.contribution_pct)}`}>{pct(item.contribution_pct)}</span>
                      </span>
                    </div>
                  )
                })}

              {diag!.alerts.length > 0 ? (
                <div className="space-y-1 pt-1">
                  {diag!.alerts.map((a, i) => (
                    <div key={i} className="flex items-start gap-1 text-[11px] text-amber-600">
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>{a}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="pt-1 text-[11px] text-emerald-500">✓ 集中度/分佈未見明顯風險</div>
              )}
              <button
                type="button"
                onClick={runAiReview}
                disabled={aiReviewLoading}
                className="mt-1 w-full rounded border border-border/60 py-1 text-[11px] text-primary hover:bg-accent/30 disabled:opacity-60"
              >
                {aiReviewLoading ? 'AI 體檢中…' : 'AI 體檢報告'}
              </button>
              {aiReview?.content && (
                <div className="prose prose-sm dark:prose-invert mt-1 max-w-none break-words text-[12px] [&_p]:my-1 [&_ul]:my-1">
                  <ReactMarkdown>{aiReview.content}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 機會精選 */}
        <div className="card p-4 lg:col-span-5">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-primary" />
              機會精選
            </h2>
            <button
              type="button"
              className="text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => navigate('/opportunities')}
            >
              進入機會頁
            </button>
          </div>
          {opportunities.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">{loading ? '載入中…' : '暫無活躍機會訊號'}</div>
          ) : (
            <div className="divide-y divide-border/40">
              {opportunities.slice(0, 3).map((o) => {
                const score = Math.max(0, Math.min(100, o.rank_score ?? o.score ?? 0))
                return (
                  <div
                    key={`${o.stock_market}:${o.stock_symbol}`}
                    className="flex cursor-pointer items-center gap-2 py-2 hover:bg-accent/30"
                    onClick={() => openStock(o.stock_symbol, o.stock_market, o.stock_name || o.stock_symbol)}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-[13px] font-medium">{o.stock_name || o.stock_symbol}</span>
                        {o.action_label && <span className="rounded bg-primary/10 px-1 text-[9px] text-primary">{o.action_label}</span>}
                      </div>
                      {(o.signal || o.reason) && <div className="truncate text-[11px] text-muted-foreground">{o.signal || o.reason}</div>}
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="font-mono text-[13px] text-foreground">{score.toFixed(0)}</div>
                      <div className="text-[9px] text-muted-foreground">評分</div>
                      <div className="mt-1 h-[3px] w-10 rounded bg-accent/40">
                        <div className="h-[3px] rounded bg-primary/70" style={{ width: `${score}%` }} />
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 盤前/盤後簡報 */}
        {brief && (brief.title || brief.content) && (
          <div className="card p-4 lg:col-span-7">
            <div className="mb-1 flex items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <Newspaper className="h-4 w-4 text-primary" />
                {brief.agent_label}
              </h2>
              <div className="flex shrink-0 items-center gap-2">
                <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  AI{brief.date ? ` · ${brief.date}` : ''}
                </span>
                {brief.content && (
                  <button
                    type="button"
                    className="text-[11px] text-muted-foreground hover:text-foreground"
                    onClick={() => setBriefOpen((v) => !v)}
                  >
                    {briefOpen ? '收起' : '展開'}
                  </button>
                )}
              </div>
            </div>
            {brief.title && <div className="text-[14.5px] font-semibold text-foreground">{brief.title}</div>}
            {!briefOpen && briefSummary && <div className="mt-1 text-[12px] text-muted-foreground">{briefSummary}</div>}
            {briefOpen && brief.content && (
              <div className="prose prose-sm dark:prose-invert mt-1 max-w-none break-words text-[12px] [&_p]:my-1 [&_ul]:my-1">
                <ReactMarkdown>{brief.content}</ReactMarkdown>
              </div>
            )}
          </div>
        )}
      </div>

      <DiscoveryPanel monitorStocks={scan} onOpenStock={openStock} />

      <StockInsightModal
        open={modal.open}
        onOpenChange={(o) => setModal((m) => ({ ...m, open: o }))}
        symbol={modal.symbol}
        market={modal.market}
        stockName={modal.name}
        hasPosition={modal.hasPosition}
      />

      {/* 分享卡:模擬交易成績單(vs 基準) */}
      {shareBench && bench && (
        <BenchmarkShareCard open={shareBench} onClose={() => setShareBench(false)} bench={bench} />
      )}

      {/* 分享卡:組合體檢(脫敏,無金額) */}
      {shareDiag && diag && (
        <DiagnosticsShareCard
          open={shareDiag}
          onClose={() => setShareDiag(false)}
          diag={diag}
          excessReturn={benchReady ? bench!.excess_return : null}
          benchmarkLabel={bench?.benchmark_label}
        />
      )}

      {/* 分享卡:今日盯盤 digest */}
      <DigestShareCard
        open={shareDigest}
        onClose={() => setShareDigest(false)}
        date={today}
        items={feed.map((it) => ({
          type: it.type,
          name: it.name,
          symbol: it.symbol,
          why: it.why,
          change_pct: it.change_pct ?? null,
        }))}
      />

      <Onboarding open={showOnboarding} onComplete={handleOnboardingComplete} hasStocks={hasWatchlist} />
    </div>
  )
}
