import { useEffect, useState, type ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowLeft,
  FileDown,
  ImageDown,
  List,
  ChevronDown,
  Target,
  TrendingUp,
  MessageSquare,
  Newspaper,
  BarChart3,
  Scale,
  ShieldAlert,
  History,
  type LucideIcon,
} from 'lucide-react'
import {
  tradingAgentsApi,
  type DeepAnalysisResult,
  type HistoryComparisonResponse,
} from '@panwatch/api'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { buildAnalysisSections } from '@panwatch/biz-ui/analysis-sections'
import ShareCardModal from '../components/ShareCardModal'

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-rose-500',
  hold: 'text-amber-500',
  sell: 'text-emerald-500',
}

/** 各 section 配圖示(決策/技術/情緒/新聞/基本面/辯論/風控),與 buildAnalysisSections 的 id 對齊 */
const SECTION_ICON: Record<string, LucideIcon> = {
  decision: Target,
  market: TrendingUp,
  social: MessageSquare,
  news: Newspaper,
  fundamentals: BarChart3,
  debate: Scale,
  risk: ShieldAlert,
}

/** 二級目錄顯示開關的 localStorage 鍵(記住使用者選擇) */
const TOC_SUB_KEY = 'panwatch_toc_show_sub'

/** 從程式碼粗略推斷市場:6 位數字=A股, 5 位數字=港股, 其餘=美股 */
function inferMarket(symbol: string): string {
  if (/^\d{6}$/.test(symbol)) return 'CN'
  if (/^\d{5}$/.test(symbol)) return 'HK'
  return 'US'
}

function pctClass(v: number | null | undefined): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-rose-500' : v < 0 ? 'text-emerald-500' : 'text-muted-foreground'
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** 標題 → 錨點 slug(去掉 markdown 強調/井號/emoji,空白轉連字元)。
 *  解析目錄與渲染標題兩側用同一份邏輯,保證 id 一致、點選可跳。 */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[*_`#~]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^\w一-龥-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

/** 從 ReactMarkdown 標題節點的 children 裡遞迴取純文本(用於算錨點 id)。 */
function nodeText(children: ReactNode): string {
  if (typeof children === 'string') return children
  if (typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(nodeText).join('')
  if (children && typeof children === 'object' && 'props' in children) {
    return nodeText((children as { props?: { children?: ReactNode } }).props?.children)
  }
  return ''
}

/** 從一段 markdown 裡抽出 2~4 級標題(用於二級目錄)。 */
function parseHeadings(markdown: string): { text: string; slug: string }[] {
  const out: { text: string; slug: string }[] = []
  for (const raw of markdown.split('\n')) {
    const m = /^(#{2,4})\s+(.+?)\s*#*$/.exec(raw)
    if (!m) continue
    const text = m[2].replace(/[*_`]/g, '').trim()
    if (text) out.push({ text, slug: slugify(m[2]) })
  }
  return out
}

export default function AnalysisDetailPage() {
  const { symbol = '', date = '' } = useParams()
  const navigate = useNavigate()
  const [result, setResult] = useState<DeepAnalysisResult | null>(null)
  const [history, setHistory] = useState<HistoryComparisonResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState('')
  const [tocOpen, setTocOpen] = useState(false)
  const [showSub, setShowSub] = useState(() => {
    try {
      return localStorage.getItem(TOC_SUB_KEY) !== '0'
    } catch {
      return true
    }
  })
  const [pdfBusy, setPdfBusy] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)

  const handleExportPdf = async () => {
    if (pdfBusy) return
    setPdfBusy(true)
    try {
      await tradingAgentsApi.downloadAnalysisPdf(symbol, date)
    } catch (e) {
      alert(e instanceof Error ? e.message : '匯出失敗')
    } finally {
      setPdfBusy(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    tradingAgentsApi
      .getAnalysisByDate(symbol, date)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false))
    tradingAgentsApi
      .getHistoryComparison(symbol, inferMarket(symbol), 90)
      .then(setHistory)
      .catch(() => setHistory(null))
  }, [symbol, date])

  // 記住二級目錄開關
  useEffect(() => {
    try {
      localStorage.setItem(TOC_SUB_KEY, showSub ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [showSub])

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const reviewRequired = sug?.review_required === true || sug?.rating_raw === 'review'
  const decisionLabel = reviewRequired ? '待人工複核' : sug?.action_label
  const decisionColor = reviewRequired ? 'text-orange-500' : (sug ? DECISION_COLOR[sug.action] || '' : '')
  const sections = buildAnalysisSections(rawData)
  const stats = history?.stats
  const items = history?.items || []

  // 完整目錄:每個 section(一級) + 其 markdown 內 2~4 級標題(二級) + 歷史決策對比
  const fullToc: { id: string; title: string; level: 0 | 1 }[] = []
  for (const s of sections) {
    fullToc.push({ id: `sec-${s.id}`, title: s.title, level: 0 })
    for (const h of parseHeadings(s.markdown)) {
      fullToc.push({ id: `h-${s.id}-${h.slug}`, title: h.text, level: 1 })
    }
  }
  fullToc.push({ id: 'sec-history', title: '歷史決策對比', level: 0 })
  // 開關決定是否展示/聯動二級目錄
  const toc = showSub ? fullToc : fullToc.filter((t) => t.level === 0)

  // 滾動聯動:正文滾動時自動高亮當前段(取視口內最靠上、避開頂部導航的標題)
  useEffect(() => {
    if (!result) return
    const els = toc
      .map((t) => document.getElementById(t.id))
      .filter((el): el is HTMLElement => !!el)
    if (!els.length) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveId(visible[0].target.id)
      },
      { rootMargin: '-100px 0px -55% 0px', threshold: 0 },
    )
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, toc.length])

  if (loading) {
    return <div className="p-12 text-center text-muted-foreground">載入中...</div>
  }
  if (!result) {
    return (
      <div className="p-12 text-center text-muted-foreground space-y-3">
        <div>未找到 {symbol} 在 {date} 的深度分析記錄</div>
        <button onClick={() => navigate(-1)} className="text-primary hover:underline">
          返回
        </button>
      </div>
    )
  }

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  // 當前所在段標題(移動端摺疊條上顯示,讓使用者知道讀到哪了)
  const currentTitle = toc.find((t) => t.id === activeId)?.title || ''

  // markdown 標題渲染:掛上與目錄一致的錨點 id + 頂部留白(避開吸頂導航)
  const headingComponents = (sectionId: string) => {
    const make = (Tag: 'h2' | 'h3' | 'h4') =>
      function Heading({ children }: { children?: ReactNode }) {
        const id = `h-${sectionId}-${slugify(nodeText(children))}`
        return (
          <Tag id={id} className="scroll-mt-24">
            {children}
          </Tag>
        )
      }
    return { h2: make('h2'), h3: make('h3'), h4: make('h4') }
  }

  // 目錄頭(標題 + 二級目錄開關),桌面右欄 / 移動下拉共用
  const tocHeader = (
    <div className="flex items-center justify-between gap-2 mb-2 px-2">
      <span className="text-[11px] font-medium text-muted-foreground/70">目錄</span>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="cursor-pointer select-none" onClick={() => setShowSub((v) => !v)}>
          二級目錄
        </span>
        <Switch checked={showSub} onCheckedChange={setShowSub} />
      </div>
    </div>
  )

  // 目錄列表(桌面右欄 / 移動下拉共用);onAfter 用於移動端選完自動收起
  const tocNav = (onAfter?: () => void) => (
    <nav className="space-y-0.5 text-[13px]">
      {toc.map((t) => (
        <button
          key={t.id}
          onClick={() => {
            scrollTo(t.id)
            onAfter?.()
          }}
          className={`block w-full text-left py-1 rounded-md transition-colors truncate ${
            t.level === 1 ? 'pl-5 pr-2 text-[12px]' : 'px-2'
          } ${
            activeId === t.id
              ? 'bg-accent text-foreground font-medium'
              : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
          }`}
        >
          {t.title}
        </button>
      ))}
    </nav>
  )

  return (
    <div className="min-h-screen">
      <div className="max-w-5xl mx-auto px-4 pb-12 flex gap-8">
        {/* 左列:標題欄 + 正文(標題欄只佔左列寬度,不壓到右側目錄) */}
        <div className="flex-1 min-w-0 max-w-3xl">
          {/* 頂部欄 */}
          <div className="border-b border-border/40 pb-3 mb-4 flex items-center gap-3">
            <button
              onClick={() => navigate(-1)}
              className="w-8 h-8 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-all shrink-0"
              aria-label="返回"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <h1 className="text-base font-bold truncate min-w-0">{result.title || `${symbol} 深度分析`}</h1>
            <span className="text-[12px] text-muted-foreground shrink-0">{date}</span>
            <button
              onClick={() => setShareOpen(true)}
              className="ml-auto shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent transition-all"
              title="生成可分享的結論卡片圖"
            >
              <ImageDown className="w-3.5 h-3.5" />
              分享圖
            </button>
            <button
              onClick={handleExportPdf}
              disabled={pdfBusy}
              className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent transition-all disabled:opacity-50"
              title="匯出 PDF 檔案"
            >
              <FileDown className="w-3.5 h-3.5" />
              {pdfBusy ? '匯出中…' : '匯出 PDF'}
            </button>
          </div>

          {/* 正文 */}
          <article>
          {/* 決策摘要(移動端在正文頂部;桌面端移到右側目錄區,見下方 aside) */}
          {sug && (
            <div className="lg:hidden rounded-xl bg-accent/30 p-4 mb-6 flex items-center gap-3 flex-wrap">
              <span className={`text-[24px] font-bold ${decisionColor}`}>
                {decisionLabel}
              </span>
              {reviewRequired && <span className="text-[12px] text-orange-600">資料或結論存在不確定性，請人工核驗後再決策</span>}
              <span className="text-[13px] text-muted-foreground">
                置信度 {sug.confidence?.toFixed(1) ?? '-'} / 10
              </span>
              <span className="ml-auto text-[11px] text-muted-foreground">
                成本 ${rawData.cost_usd?.toFixed(4) ?? '-'}
              </span>
            </div>
          )}

          {/* 移動端目錄:吸頂摺疊條,顯示當前段,展開下拉(覆蓋式),選完/點外部收起(桌面隱藏) */}
          <div className="lg:hidden sticky top-16 z-30 mb-6">
            <div className="relative">
              <button
                onClick={() => setTocOpen((o) => !o)}
                className="w-full flex items-center gap-2 px-3.5 py-2.5 rounded-xl border border-border/50 bg-card/95 backdrop-blur text-[13px] font-medium shadow-sm"
              >
                <List className="w-4 h-4 shrink-0" />
                <span className="truncate">{currentTitle || '目錄'}</span>
                <ChevronDown
                  className={`w-4 h-4 ml-auto shrink-0 transition-transform ${tocOpen ? 'rotate-180' : ''}`}
                />
              </button>
              {tocOpen && (
                <>
                  <div className="fixed inset-0 z-0" onClick={() => setTocOpen(false)} />
                  <div className="absolute left-0 right-0 top-full mt-1 z-10 rounded-xl border border-border/50 bg-card/95 backdrop-blur shadow-lg max-h-[60vh] overflow-y-auto scrollbar p-2">
                    {tocHeader}
                    {tocNav(() => setTocOpen(false))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* 各部分長文 */}
          {sections.map((s) => {
            const Icon = SECTION_ICON[s.id]
            return (
              <section key={s.id} id={`sec-${s.id}`} className="mb-12 scroll-mt-24">
                <h2 className="flex items-center gap-2 text-[18px] font-bold mb-4 pb-2 border-b border-border/40">
                  {Icon && <Icon className="w-[18px] h-[18px] text-primary/70 shrink-0" />}
                  {s.title}
                </h2>
                <div className="prose prose-base dark:prose-invert max-w-none leading-relaxed prose-headings:mt-6 prose-headings:mb-2 prose-h2:text-[16px] prose-h3:text-[15px] prose-h4:text-[14px] prose-h2:font-semibold prose-h3:font-semibold prose-p:my-3 prose-p:text-foreground/90 prose-li:my-1 prose-table:my-4 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-strong:text-foreground">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents(s.id)}>
                    {s.markdown}
                  </ReactMarkdown>
                </div>
              </section>
            )
          })}

          {/* 歷史決策對比 */}
          <section id="sec-history" className="mb-10 scroll-mt-24">
            <h2 className="flex items-center gap-2 text-[18px] font-bold mb-4 pb-2 border-b border-border/40">
              <History className="w-[18px] h-[18px] text-primary/70 shrink-0" />
              歷史決策 vs 實際漲跌
            </h2>
            {stats && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-[13px]">
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">總命中率</div>
                  <div className="font-bold">{stats.overall_hit_rate != null ? `${(stats.overall_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">買入命中</div>
                  <div className="font-bold">{stats.buy_hit_rate != null ? `${(stats.buy_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">賣出命中</div>
                  <div className="font-bold">{stats.sell_hit_rate != null ? `${(stats.sell_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">平均 20 日收益</div>
                  <div className={`font-bold ${pctClass(stats.avg_return_20d_pct)}`}>{fmtPct(stats.avg_return_20d_pct)}</div>
                </div>
              </div>
            )}
            {items.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-[12px]">
                      <th className="text-left py-2 pr-3">日期</th>
                      <th className="text-left py-2 px-2">決策</th>
                      <th className="text-right py-2 px-2">分析價</th>
                      <th className="text-right py-2 px-2">1日</th>
                      <th className="text-right py-2 px-2">5日</th>
                      <th className="text-right py-2 px-2">20日</th>
                      <th className="text-right py-2 pl-2">命中</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="py-2 pr-3">{it.analysis_date}</td>
                        <td className="py-2 px-2">{it.action_label}{it.confidence != null ? ` (${it.confidence.toFixed(1)})` : ''}</td>
                        <td className="text-right py-2 px-2">{it.price_at_analysis ?? '-'}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_1d_pct)}`}>{fmtPct(it.return_1d_pct)}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_5d_pct)}`}>{fmtPct(it.return_5d_pct)}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_20d_pct)}`}>{fmtPct(it.return_20d_pct)}</td>
                        <td className="text-right py-2 pl-2">{it.hit_20d == null ? '-' : it.hit_20d ? '✓' : '✗'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-[13px] text-muted-foreground py-4">暫無歷史決策記錄</div>
            )}
          </section>

          {/* 免責 */}
          <div className="text-[11px] text-muted-foreground/70 italic border-t border-border/30 pt-4">
            本分析由 AI 多 Agent 框架生成,僅供學習研究參考,不構成任何投資建議。投資有風險,決策需自主判斷。
          </div>
          </article>
        </div>

        {/* 右列:最終決策 + 目錄合併到同一張卡片(與標題同高起始,不被標題壓住;主題 token 適配日/夜) */}
        <aside className="hidden lg:block w-52 shrink-0">
          <div className="sticky top-24 rounded-xl border border-border bg-card overflow-hidden">
            {/* 最終決策摘要 */}
            {sug && (
              <div className="p-3.5 border-b border-border">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`text-[22px] font-bold leading-none ${decisionColor}`}>
                    {decisionLabel}
                  </span>
                  <span className="text-[11px] text-muted-foreground shrink-0">
                    ${rawData.cost_usd?.toFixed(4) ?? '-'}
                  </span>
                </div>
                {reviewRequired && (
                  <p className="mt-2 text-[11px] leading-4 text-orange-600">
                    上游無法安全生成可執行評級，請人工核驗資料與報告。
                  </p>
                )}
                {sug.confidence != null && (
                  <div className="mt-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                      <span>置信度</span>
                      <span className="font-medium text-foreground">{sug.confidence.toFixed(1)} / 10</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          sug.action === 'buy'
                            ? 'bg-rose-500'
                            : sug.action === 'sell'
                              ? 'bg-emerald-500'
                              : 'bg-amber-500'
                        }`}
                        style={{ width: `${Math.max(0, Math.min(100, sug.confidence * 10))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            {/* 目錄 */}
            <div className="p-2">
              {tocHeader}
              <div className="max-h-[calc(100vh-19rem)] overflow-y-auto scrollbar">{tocNav()}</div>
            </div>
          </div>
        </aside>
      </div>

      {/* 分享卡片(匯出 PNG) */}
      <ShareCardModal
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={result}
        symbol={symbol}
        date={date}
      />
    </div>
  )
}
