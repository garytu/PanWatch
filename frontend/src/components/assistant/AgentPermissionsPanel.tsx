import type { AgentPermissions } from '@panwatch/api'

type ToolRisk = 'read' | 'write' | 'external' | 'destructive'
type PermissionMode = 'allow' | 'ask' | 'deny'
type AgentToolPermission = AgentPermissions['tools'][number]

interface AgentPermissionsPanelProps {
  permissions: AgentPermissions
  variant?: 'card' | 'drawer'
  onChange: (change: {
    selector_kind: 'tool' | 'risk'
    selector_value: string
    mode: PermissionMode
    risk: ToolRisk
  }) => void
}

const RISK_LABELS: Record<ToolRisk, string> = {
  read: '讀取',
  write: '修改',
  external: '外部操作',
  destructive: '破壞性操作',
}

const MODE_LABELS: Record<PermissionMode, string> = {
  allow: '直接允許',
  ask: '每次詢問',
  deny: '禁止',
}

function availableModes(tool: AgentToolPermission): PermissionMode[] {
  if (tool.risk === 'destructive') return ['deny']
  if (tool.confirmation_required) return ['ask', 'deny']
  return ['allow', 'ask', 'deny']
}

function availableModesForRisk(risk: ToolRisk): PermissionMode[] {
  return risk === 'destructive' ? ['deny'] : ['allow', 'ask', 'deny']
}

export function AgentPermissionsPanel({ permissions, onChange, variant = 'card' }: AgentPermissionsPanelProps) {
  const content = (
    <>
      <div className="mb-4">
        <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">助手工具權限</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          僅影響助手可見和可執行的工具；破壞性操作始終禁止，需確認的工具不能設為直接允許。
        </p>
      </div>
      <div className={`grid gap-2 ${variant === 'drawer' ? 'grid-cols-2' : 'sm:grid-cols-2 xl:grid-cols-4'}`}>
        {permissions.defaults.map((item) => (
          <label
            key={item.risk}
            className="flex min-w-0 items-center justify-between gap-1 rounded-xl border border-border/50 bg-accent/20 px-2.5 py-2 text-[11px] text-muted-foreground"
          >
            <span className="min-w-0 shrink truncate whitespace-nowrap">{RISK_LABELS[item.risk]}</span>
            <select
              aria-label={`${RISK_LABELS[item.risk]}預設權限`}
              value={item.mode}
              onChange={(event) => onChange({
                selector_kind: 'risk',
                selector_value: item.risk,
                mode: event.target.value as PermissionMode,
                risk: item.risk,
              })}
              className="h-7 min-w-0 shrink-0 rounded-md border border-border/60 bg-background px-1.5 text-[11px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              {availableModesForRisk(item.risk).map((mode) => (
                <option key={mode} value={mode}>{MODE_LABELS[mode]}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="mt-4 space-y-2">
        {permissions.tools.length === 0 ? (
          <p className="py-2 text-[12px] text-muted-foreground">當前沒有已註冊的可配置工具。</p>
        ) : permissions.tools.map((tool) => (
          <div key={tool.name} className="flex items-center justify-between gap-3 rounded-xl bg-accent/30 px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-[12px] font-medium text-foreground">{tool.title}</p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {tool.name} · {RISK_LABELS[tool.risk]}{tool.confirmation_required ? ' · 需確認' : ''}
              </p>
            </div>
            <select
              aria-label={tool.title}
              value={tool.mode}
              onChange={(event) => onChange({
                selector_kind: 'tool',
                selector_value: tool.name,
                mode: event.target.value as PermissionMode,
                risk: tool.risk,
              })}
              className="h-8 rounded-lg border border-border/60 bg-background px-2 text-[12px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              {availableModes(tool).map((mode) => (
                <option key={mode} value={mode}>{MODE_LABELS[mode]}</option>
              ))}
            </select>
          </div>
        ))}
      </div>
    </>
  )

  if (variant === 'drawer') {
    return <div>{content}</div>
  }

  return (
    <section id="sec-agent-permissions" className="card p-4 md:p-6 lg:col-span-12">
      {content}
    </section>
  )
}
