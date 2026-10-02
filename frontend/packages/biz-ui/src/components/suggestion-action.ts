export type SuggestionAction =
  | 'buy'
  | 'add'
  | 'reduce'
  | 'sell'
  | 'hold'
  | 'watch'
  | 'alert'
  | 'avoid'

export const suggestionActionColors: Record<SuggestionAction, string> = {
  buy: 'bg-rose-500 text-white',
  add: 'bg-rose-400 text-white',
  reduce: 'bg-emerald-500 text-white',
  sell: 'bg-emerald-600 text-white',
  hold: 'bg-amber-500 text-white',
  watch: 'bg-slate-500 text-white',
  alert: 'bg-blue-500 text-white',
  avoid: 'bg-red-600 text-white',
}

export const suggestionActionLabels: Record<SuggestionAction, string> = {
  buy: '買入',
  add: '加碼',
  reduce: '減碼',
  sell: '賣出',
  hold: '持有',
  watch: '觀望',
  avoid: '迴避',
  alert: '提醒',
}

export function normalizeSuggestionAction(action?: string, label?: string): SuggestionAction | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  if (raw === 'buy') return 'buy'
  if (raw === 'add' || raw === 'increase') return 'add'
  if (raw === 'reduce' || raw === 'decrease') return 'reduce'
  if (raw === 'sell') return 'sell'
  if (raw === 'hold') return 'hold'
  if (raw === 'watch' || raw === 'neutral') return 'watch'
  if (raw === 'avoid') return 'avoid'
  if (raw === 'alert') return 'alert'
  if (/買入|買|建倉/.test(raw)) return 'buy'
  if (/加碼|增持|補倉/.test(raw)) return 'add'
  if (/減碼|減持/.test(raw)) return 'reduce'
  if (/出清|賣出|停損|賣/.test(raw)) return 'sell'
  if (/持有|持倉/.test(raw)) return 'hold'
  if (/觀望|中性|等待/.test(raw)) return 'watch'
  if (/迴避|規避|避免/.test(raw)) return 'avoid'
  return null
}

export function resolveSuggestionAction(action?: string, label?: string): SuggestionAction {
  return normalizeSuggestionAction(action, label) || 'watch'
}

export function resolveSuggestionLabel(action?: string, label?: string, fallback = '觀望'): string {
  const normalized = normalizeSuggestionAction(action, label)
  if (normalized) return suggestionActionLabels[normalized] || fallback
  return String(label || '').trim() || fallback
}

export function resolveSuggestionColorClass(action?: string, label?: string): string {
  const normalized = resolveSuggestionAction(action, label)
  return suggestionActionColors[normalized] || suggestionActionColors.watch
}
