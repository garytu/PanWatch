import { Check, Loader2, Minimize2, X } from 'lucide-react'
import type { AssistantContextDetail, AssistantContextSnapshot, ContextUsage } from '@panwatch/api'

interface ContextPanelProps {
  detail: AssistantContextDetail | null
  loading: boolean
  compressing: boolean
  error?: string
  onCompress: (mode: AssistantContextSnapshot['mode']) => void
  onClose?: () => void
}

const SECTION_LABELS: Record<string, string> = {
  system: '系統指令',
  summary: '結構化摘要',
  page_context: '頁面上下文',
  tool_definitions: '工具定義',
  history: '歷史訊息',
  recent_messages: '最近訊息',
}

const STATUS_LABELS = {
  not_needed: '本次無需壓縮',
  compressed: '壓縮完成',
  no_gain: '本次壓縮無收益，已保留原上下文',
} as const

const MODE_LABELS: Array<{ mode: AssistantContextSnapshot['mode']; label: string }> = [
  { mode: 'balanced', label: '平衡壓縮' },
  { mode: 'preserve_details', label: '保留細節並壓縮' },
  { mode: 'handoff', label: '整理為交接摘要' },
]

function usagePercent(usage: ContextUsage): number {
  return Math.min(100, Math.round((usage.total_tokens / usage.budget_tokens) * 100))
}

function usageLabel(usage: ContextUsage): string {
  if (usage.measurement === 'provider') return '實際輸入 Token'
  if (usage.measurement === 'tokenizer') return 'Tokenizer 輸入 Token'
  return '估算輸入 Token'
}

export function ContextPanel({ detail, loading, compressing, error, onCompress, onClose }: ContextPanelProps) {
  return (
    <section data-testid="assistant-context-panel" className="border-b border-border/40 bg-background px-4 py-3 text-[12px]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-medium text-foreground">上下文用量</h3>
          <p className="mt-0.5 text-muted-foreground">隻影響下一次執行，不會刪除對話訊息。</p>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="關閉上下文面板">
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {loading && <div className="py-5 text-muted-foreground">正在測量上下文…</div>}
      {!loading && detail && (
        <>
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="font-medium tabular-nums">{usageLabel(detail.usage)}：{detail.usage.total_tokens.toLocaleString()} / {detail.usage.budget_tokens.toLocaleString()}</span>
            <span className={detail.status === 'needs_compression' ? 'text-rose-600' : detail.status === 'warning' ? 'text-amber-600' : 'text-emerald-600'}>
              {detail.status === 'needs_compression' ? '需要壓縮' : detail.status === 'warning' ? '接近上限' : '正常'} · {usagePercent(detail.usage)}%
            </span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={detail.status === 'needs_compression' ? 'h-full bg-rose-500' : detail.status === 'warning' ? 'h-full bg-amber-500' : 'h-full bg-emerald-500'}
              style={{ width: `${usagePercent(detail.usage)}%` }}
            />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
            {detail.usage.sections.filter((section) => section.tokens > 0).map((section) => (
              <div key={section.name} className="flex min-w-0 items-center justify-between gap-2 text-muted-foreground">
                <span className="truncate">{SECTION_LABELS[section.name] || section.name}</span>
                <span className="shrink-0 tabular-nums text-foreground">{section.tokens.toLocaleString()}</span>
              </div>
            ))}
          </div>
          {detail.snapshot && (
            <div className="mt-3 border-t border-border/40 pt-2 text-muted-foreground">
              <div className="flex items-center gap-1.5"><Check className="h-3.5 w-3.5 text-emerald-600" />摘要 v{detail.snapshot.version}</div>
              {detail.snapshot.summary.goal.length > 0 && <p className="mt-1 truncate">目標：{detail.snapshot.summary.goal[0]}</p>}
              {detail.snapshot.summary.current_state && <p className="truncate">狀態：{detail.snapshot.summary.current_state}</p>}
              {detail.snapshot.summary.open_items.length > 0 && <p className="truncate">待辦：{detail.snapshot.summary.open_items[0]}</p>}
            </div>
          )}
          {detail.last_compression && (
            <div className="mt-3 border-t border-border/40 pt-2 text-muted-foreground">
              <div className={detail.last_compression.status === 'no_gain' ? 'text-amber-600' : 'text-emerald-600'}>
                {STATUS_LABELS[detail.last_compression.status]}
              </div>
              {detail.last_compression.status === 'compressed' && (
                <p className="mt-1">
                  {detail.last_compression.usage_before.total_tokens.toLocaleString()} → {detail.last_compression.usage_after.total_tokens.toLocaleString()}，節省 {detail.last_compression.saved_tokens.toLocaleString()} Token（{detail.last_compression.saved_percent}%）
                </p>
              )}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            {MODE_LABELS.map(({ mode, label }) => (
              <button
                key={mode}
                type="button"
                disabled={compressing}
                onClick={() => onCompress(mode)}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1.5 text-[11px] text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                {compressing && mode === 'balanced' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Minimize2 className="h-3 w-3" />}
                {compressing && mode === 'balanced' ? '壓縮中…' : label}
              </button>
            ))}
          </div>
        </>
      )}
      {!loading && !detail && <p className="py-4 text-muted-foreground">當前還沒有可測量的會話。</p>}
      {error && <p className="mt-2 text-rose-600">{error}</p>}
    </section>
  )
}
