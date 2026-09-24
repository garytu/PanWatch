import { fetchAPI } from './client'

export interface AlertHitToday {
  rule_id: number
  rule_name: string
  symbol: string
  name: string
  market: string
  trigger_time: string
  snapshot: Record<string, unknown>
}

export interface PortfolioTodo {
  type: string // no_alert | alert_expiring
  symbol?: string
  market?: string
  message: string
}

export const homeApi = {
  /** 今日(本地時區)全部提醒命中,跨規則聚合。 */
  alertHitsToday: () => fetchAPI<AlertHitToday[]>('/price-alerts/hits/today'),

  /** 首頁空態待辦:持倉未設提醒 / 提醒即將到期。 */
  todos: () => fetchAPI<{ todos: PortfolioTodo[]; count: number }>('/portfolio/todos'),
}
