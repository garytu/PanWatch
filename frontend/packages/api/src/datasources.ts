import { fetchAPI } from './client'

export interface ResetToSeedDeletedItem {
  id: number
  type: string
  provider: string
  name: string
}

export interface ResetToSeedSeededItem {
  name: string
  type: string
  provider: string
}

export interface ResetToSeedResult {
  deleted: ResetToSeedDeletedItem[]
  seeded_missing: ResetToSeedSeededItem[]
}

/** 資料來源"恢復預設":刪孤兒 + 補缺失預設,重置內建測試股票,保留使用者配置/憑證。 */
export const resetDataSourcesToSeed = () =>
  fetchAPI<ResetToSeedResult>('/datasources/reset-to-seed', { method: 'POST' })
