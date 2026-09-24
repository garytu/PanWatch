import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chatApi } from '@panwatch/api'
import ChatWidget from '@/components/ChatWidget'

vi.mock('@panwatch/api', () => ({
  chatApi: {
    listConversations: vi.fn().mockResolvedValue([]),
    createConversation: vi.fn().mockResolvedValue({
      id: 1,
      title: '',
      stock_symbol: null,
      stock_market: null,
      created_at: '2026-09-12T00:00:00Z',
    }),
    getConversation: vi.fn().mockResolvedValue({
      conversation: { id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      messages: [],
    }),
    getAssistantTask: vi.fn().mockResolvedValue({
      conversation_id: 1,
      status: 'completed',
      pending_approvals: [],
    }),
    subscribeAssistantTaskStream: vi.fn().mockResolvedValue(undefined),
    sendMessage: vi.fn(),
    sendAssistantMessageStream: vi.fn().mockResolvedValue(undefined),
    decideAssistantApprovalStream: vi.fn().mockResolvedValue(undefined),
  },
}))

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})

describe('ChatWidget layout', () => {
  it('reconnects a running durable task after a refresh', async () => {
    sessionStorage.setItem('panwatch:assistant-task:1', '88')
    vi.mocked(chatApi.getAssistantTask)
      .mockResolvedValueOnce({
        id: 88,
        conversation_id: 1,
        status: 'running',
        pending_approvals: [],
      })
      .mockResolvedValueOnce({
        id: 88,
        conversation_id: 1,
        status: 'completed',
        pending_approvals: [],
      })
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      conversation: { id: 1, title: '恢復任務', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      messages: [{ id: 188, role: 'assistant', content: '後臺任務已完成', created_at: '2026-09-12T00:00:00Z' }],
    })
    vi.mocked(chatApi.subscribeAssistantTaskStream).mockImplementation(async (_taskId, callbacks) => {
      callbacks.onToolCallStart?.({ name: 'create_price_alert', arguments: {} })
      callbacks.onDone?.({ message_id: 188, content: '後臺任務已完成', created_at: '2026-09-12T00:00:00Z' })
    })

    render(<ChatWidget embedded conversationIdFromUrl={1} onConversationChange={vi.fn()} />)

    await waitFor(() => expect(chatApi.subscribeAssistantTaskStream).toHaveBeenCalledWith(
      88,
      expect.any(Object),
      expect.any(AbortSignal),
    ))
    await screen.findByText('後臺任務已完成')
    await waitFor(() => expect(sessionStorage.getItem('panwatch:assistant-task:1')).toBeNull())
  })

  it('does not restore an approval from a conversation that was left before the response arrived', async () => {
    let resolveTask: ((value: unknown) => void) | undefined
    vi.mocked(chatApi.listConversations).mockResolvedValue([
      { id: 1, title: '舊會話', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      { id: 2, title: '新會話', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
    ])
    vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
      conversation: { id, title: id === 1 ? '舊會話' : '新會話', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      messages: [{ id: id * 10, role: 'assistant', content: `會話 ${id}`, created_at: '2026-09-12T00:00:00Z' }],
    }))
    vi.mocked(chatApi.getAssistantTask).mockImplementationOnce(() => new Promise((resolve) => {
      resolveTask = resolve
    }))
    sessionStorage.setItem('panwatch:assistant-task:1', '99')

    const onConversationChange = vi.fn()
    const { rerender } = render(
      <ChatWidget embedded conversationIdFromUrl={1} onConversationChange={onConversationChange} />,
    )
    await screen.findByText('會話 1')

    rerender(<ChatWidget embedded conversationIdFromUrl={2} onConversationChange={onConversationChange} />)
    await screen.findByText('會話 2')

    resolveTask?.({
      id: 99,
      conversation_id: 1,
      status: 'awaiting_approval',
      pending_approvals: [{
        id: 'old-approval',
        tool_name: 'delete_price_alert',
        risk: 'write',
        presentation: { tool_title: '舊會話操作', summary: '不應顯示' },
        expires_at: '2026-09-12T01:00:00Z',
      }],
    })

    await waitFor(() => expect(screen.queryByText('不應顯示')).toBeNull())
    sessionStorage.removeItem('panwatch:assistant-task:1')
  })

  it('uses the sidebar new research entry instead of a duplicate header plus', async () => {
    render(<ChatWidget embedded />)

    await waitFor(() => expect(screen.getByRole('button', { name: '新研究' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: '新建對話' })).toBeNull()
  })

  it('keeps the composer at the bottom while only the message list scrolls', async () => {
    const user = userEvent.setup()

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))

    await waitFor(() => expect(screen.getByPlaceholderText('輸入問題...')).toBeTruthy())

    const shell = screen.getByTestId('assistant-shell')
    const messageList = screen.getByTestId('assistant-message-list')
    const composer = screen.getByTestId('assistant-composer')

    expect(shell.className).toContain('min-h-0')
    expect(shell.className).toContain('h-full')
    expect(messageList.className).toContain('min-h-0')
    expect(messageList.className).toContain('overflow-y-auto')
    expect(composer.className).toContain('shrink-0')
  })

  it('ignores a second send fired before the first request updates React state', async () => {
    render(<ChatWidget embedded />)
    const quickQuestion = await screen.findByRole('button', { name: '診斷我的持倉' })

    fireEvent.click(quickQuestion)
    fireEvent.click(quickQuestion)

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalledTimes(1))
    expect(chatApi.sendAssistantMessageStream).toHaveBeenCalledTimes(1)
  })

  it('shows a prominent centered control when the reader scrolls away from the latest message', async () => {
    const user = userEvent.setup()

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))
    const messageList = await screen.findByTestId('assistant-message-list')

    Object.defineProperties(messageList, {
      scrollHeight: { configurable: true, value: 1000, writable: true },
      scrollTop: { configurable: true, value: 0, writable: true },
      clientHeight: { configurable: true, value: 500, writable: true },
    })
    fireEvent.scroll(messageList)

    const scrollButton = await screen.findByRole('button', { name: '回到底部' })
    expect(scrollButton.textContent).toContain('回到底部')
    expect(scrollButton.className).toContain('left-1/2')
    expect(scrollButton.className).toContain('h-10')

    await user.click(scrollButton)
    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
  })

  it('renders GFM table syntax as a semantic table in assistant answers', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 43 })
      callbacks.onDone?.({
        message_id: 44,
        content: '| 標的 | 漲跌幅 |\n| --- | ---: |\n| 貴州茅臺 | +1.2% |',
        created_at: '2026-09-12T00:00:00Z',
      })
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))

    const table = await screen.findByRole('table')
    expect(table).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: '標的' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '貴州茅臺' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '+1.2%' })).toBeTruthy()
  })

  it('attaches the completed trace to the assistant response and keeps it collapsed', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 46 })
      callbacks.onTrace?.({ event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } })
      callbacks.onTrace?.({ event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持倉查詢完成' } })
      callbacks.onDone?.({ message_id: 47, content: '已完成分析', created_at: '2026-09-12T00:00:00Z' })
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))

    await screen.findByText('已完成分析')
    expect(screen.getAllByTestId('assistant-trace')).toHaveLength(1)
    expect(screen.queryByText('呼叫工具：get_portfolio')).toBeNull()

    await user.click(screen.getByRole('button', { name: /執行記錄/ }))
    expect(screen.getByText('呼叫工具：get_portfolio')).toBeTruthy()
  })

  it('does not render a generic retry card when a stream fails', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 45 })
      callbacks.onError?.('助手沒有執行寫入操作，因為本輪沒有收到對應工具的成功結果。')
      throw new Error('助手沒有執行寫入操作，因為本輪沒有收到對應工具的成功結果。')
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))

    await waitFor(() => expect(screen.queryByRole('button', { name: '重試執行' })).toBeNull())
    expect(screen.queryByText(/尚未執行/)).toBeNull()
  })

  it('does not downgrade the embedded assistant to the legacy non-streaming endpoint', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockRejectedValueOnce(new Error('SSE unavailable'))

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))

    await waitFor(() => expect((screen.getByPlaceholderText('輸入問題...') as HTMLInputElement).disabled).toBe(false))
    expect(screen.queryByText(/請求未完成/)).toBeNull()
    expect(chatApi.sendMessage).not.toHaveBeenCalled()
  })

  it('keeps the first card visible as completed while the next approval remains pending', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 42 })
      callbacks.onApprovalRequired?.({
        id: 'approval-1',
        tool_title: '建立提醒',
        risk: 'write',
        summary: '建立第一個提醒',
        expires_at: '',
        status: 'pending',
      })
      callbacks.onApprovalRequired?.({
        id: 'approval-2',
        tool_title: '建立提醒',
        risk: 'write',
        summary: '建立第二個提醒',
        expires_at: '',
        status: 'pending',
      })
      callbacks.onPaused?.({ taskId: 42, reason: 'approval_required' })
    })
    vi.mocked(chatApi.decideAssistantApprovalStream).mockImplementation(async (_approvalId, _decision, callbacks) => {
      callbacks.onToolResult?.({ name: 'create_price_alert', ok: true, preview: '已建立第一個提醒' })
      callbacks.onPaused?.({
        taskId: 42,
        reason: 'approval_required',
        resolvedApprovalId: 'approval-1',
        resolvedStatus: 'approved',
      })
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))
    await screen.findByText('建立第一個提醒')
    await screen.findByText('建立第二個提醒')

    await user.click(screen.getAllByRole('button', { name: '本次允許' })[0])

    await screen.findByText('已允許，已執行')
    expect(screen.getAllByRole('button', { name: '本次允許' })).toHaveLength(1)
    expect(chatApi.decideAssistantApprovalStream).toHaveBeenCalledWith(
      'approval-1',
      'approved',
      expect.any(Object),
      42,
    )
  })
})
