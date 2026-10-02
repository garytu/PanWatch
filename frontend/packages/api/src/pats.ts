import { fetchAPI } from './client'

/** 個人訪問令牌(PAT)—— MCP 端點專用憑據 */
export interface PatItem {
  id: number
  name: string
  prefix: string
  scopes: string[]
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string | null
  revoked: boolean
}

/** 建立回應:額外帶一次性明文 token */
export interface PatCreated extends PatItem {
  token: string
}

export interface CreatePatBody {
  name?: string
  scopes?: string[]
  /** 過期天數;null = 永不過期 */
  expires_in_days?: number | null
}

export const patsApi = {
  list: () => fetchAPI<{ items: PatItem[] }>('/pats'),

  create: (body: CreatePatBody) =>
    fetchAPI<PatCreated>('/pats', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  revoke: (id: number) =>
    fetchAPI<{ ok: boolean; id: number }>(`/pats/${encodeURIComponent(String(id))}`, {
      method: 'DELETE',
    }),
}
