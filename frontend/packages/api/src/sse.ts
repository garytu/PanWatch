// SSE 使用者端基建：基於 fetch + ReadableStream（原生 EventSource 無法帶 Authorization header）
// - readSSE: 單次連線，流結束後 resolve；連線失敗直接 reject（呼叫方據此降級輪詢/非流式）
// - subscribeSSE: 自動重連訂閱（帶 Last-Event-ID 續推），用於進度/日誌等 GET 流
import { getToken } from './client'

export interface SSEEvent {
  /** 事件序號（伺服器端自增，斷線重連用） */
  id: number
  event: string
  /** data 行 JSON.parse 後的結果；解析失敗時為原始字串 */
  data: any
}

export interface ReadSSEOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  /** 斷線重連時帶上，伺服器端從其後續推 */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
}

/** 解析一段 SSE wire 文本塊（不含結尾空行分隔符） */
function parseEventBlock(block: string): SSEEvent | null {
  const lines = block.split('\n')
  if (lines.every((l) => !l || l.startsWith(':'))) return null // 心跳註釋
  let id = 0
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('id: ')) id = parseInt(line.slice(4), 10) || 0
    else if (line.startsWith('event: ')) event = line.slice(7)
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    else if (line === 'data:') dataLines.push('')
  }
  const raw = dataLines.join('\n')
  let data: any = raw
  if (raw) {
    try {
      data = JSON.parse(raw)
    } catch {
      /* 保留原始字串 */
    }
  }
  return { id, event, data }
}

/**
 * 建立一次 SSE 連線並消費到流結束。
 * 返回本次收到的最後事件序號（供呼叫方斷線重連續推）。
 * 連線失敗（HTTP 非 2xx / content-type 不對 / 網路錯誤）時拋異常。
 */
export async function readSSE(path: string, options: ReadSSEOptions): Promise<{ lastEventId: number }> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (options.lastEventId && options.lastEventId > 0) {
    headers['Last-Event-ID'] = String(options.lastEventId)
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method: options.method || 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  })
  if (!res.ok) throw new Error(`SSE HTTP ${res.status}`)
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) throw new Error(`非 SSE 回應: ${contentType}`)
  if (!res.body) throw new Error('SSE 回應無 body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEventId = options.lastEventId || 0

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // 事件之間以空行分隔
    let sepIndex: number
    while ((sepIndex = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      const ev = parseEventBlock(block)
      if (ev) {
        if (ev.id > 0) lastEventId = ev.id
        options.onEvent(ev)
      }
    }
  }
  return { lastEventId }
}

export interface SubscribeSSEOptions {
  /** 首次連線的續推起點（如已知的最大日誌 id） */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
  /** 每次（重）連線成功前觸發，可用於 UI 狀態 */
  onRetry?: (attempt: number) => void
  /** 重試次數用盡後觸發（呼叫方降級輪詢） */
  onFailed?: (err: unknown) => void
  /** 伺服器端正常關流後觸發（如流超時；呼叫方可重新訂閱或降級） */
  onClosed?: () => void
  maxRetries?: number
}

/**
 * 自動重連的 SSE 訂閱（GET）。斷線按指數退避重連並帶 Last-Event-ID 續推。
 * 返回取消函式；伺服器端正常關流（收到 done 事件後呼叫方主動 close）或重試用盡後停止。
 */
export function subscribeSSE(path: string, options: SubscribeSSEOptions): () => void {
  const controller = new AbortController()
  let closed = false
  let lastEventId = options.lastEventId || 0
  const maxRetries = options.maxRetries ?? 5

  const loop = async () => {
    let attempt = 0
    while (!closed) {
      try {
        const { lastEventId: newId } = await readSSE(path, {
          signal: controller.signal,
          lastEventId,
          onEvent: (ev) => {
            if (ev.id > 0) lastEventId = ev.id
            attempt = 0 // 收到資料即重置重試計數
            options.onEvent(ev)
          },
        })
        lastEventId = newId
        // 伺服器端正常關流（如超時 done）：由呼叫方決定是否重訂閱，這裡退出
        if (!closed) options.onClosed?.()
        return
      } catch (err) {
        if (closed || controller.signal.aborted) return
        attempt += 1
        if (attempt > maxRetries) {
          options.onFailed?.(err)
          return
        }
        options.onRetry?.(attempt)
        // 指數退避：1s/2s/4s/8s/8s...
        const delay = Math.min(1000 * 2 ** (attempt - 1), 8000)
        await new Promise((r) => setTimeout(r, delay))
      }
    }
  }
  void loop()

  return () => {
    closed = true
    controller.abort()
  }
}
