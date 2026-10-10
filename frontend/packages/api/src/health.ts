import { fetchAPI } from './client'

export interface SelfCheckItem {
  category: 'datasource' | 'ai' | 'notify'
  key: string
  name: string
  status: 'ok' | 'slow' | 'fail'
  latency_ms: number
  error: string | null
  /** 中文修復提示(僅 fail 時非空)。 */
  hint: string
  /** 例如通知"僅校驗配置未真發"。 */
  note: string | null
  /** 二級分組(AI 類目=服務商名);其餘類目為 null。 */
  group: string | null
}

export interface SelfCheckResult {
  items: SelfCheckItem[]
  summary: {
    total: number
    ok: number
    slow: number
    fail: number
  }
  notify_send: boolean
}

export const healthApi = {
  /** 系統自檢(資料來源/AI/通知連通性)。notifySend=true 會真實傳送測試通知。 */
  selfcheck: (notifySend = false) =>
    fetchAPI<SelfCheckResult>('/health/selfcheck?notify_send=' + notifySend, { timeoutMs: 60000 }),

  /** 只取可自檢項清單(不探測,秒回),用於先渲染再逐項檢查。 */
  selfcheckList: () =>
    fetchAPI<{ items: Array<{ category: string; key: string; name: string; group: string | null }> }>(
      '/health/selfcheck?list=1',
    ),

  /** 只探測指定 key 的若干項(用於逐項/小併發自檢)。notifySend 僅影響 notify 類目。 */
  selfcheckKeys: (keys: string[], notifySend = false) =>
    fetchAPI<SelfCheckResult>(
      '/health/selfcheck?keys=' +
        encodeURIComponent(keys.join(',')) +
        '&notify_send=' +
        notifySend,
      { timeoutMs: 30000 },
    ),
}
