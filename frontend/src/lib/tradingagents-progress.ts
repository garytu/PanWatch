export const TERMINAL_PROGRESS_STATUSES = ['success', 'failed', 'stale'] as const

export type TerminalProgressStatus = typeof TERMINAL_PROGRESS_STATUSES[number]

export function isTerminalProgressStatus(status: string | null | undefined): status is TerminalProgressStatus {
  return TERMINAL_PROGRESS_STATUSES.includes(status as TerminalProgressStatus)
}

/**
 * SSE 關閉只代表這條連線結束，不等於任務結束。
 * not_found、running、timeout 和空狀態都應該交給 polling 接力。
 */
export function shouldContinueProgressWatch(
  status: string | null | undefined,
  event: 'progress' | 'done' = 'progress',
): boolean {
  void event
  return !isTerminalProgressStatus(status)
}
