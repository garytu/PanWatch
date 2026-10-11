import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TraceTimeline } from '@/components/assistant/TraceTimeline'

describe('TraceTimeline', () => {
  it('renders factual runtime events without reasoning content', () => {
    render(
      <TraceTimeline
        events={[
          { event: 'run_started', data: { task_id: 7 } },
          { event: 'context_prepared', data: { compressed: true } },
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持倉查詢完成' } },
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    expect(screen.getByTestId('assistant-trace')).toBeTruthy()
    expect(screen.getByText(/已完成/)).toBeTruthy()
    expect(screen.queryByText('上下文已壓縮並準備')).toBeNull()
    expect(screen.queryByText('呼叫工具：get_portfolio')).toBeNull()
    expect(screen.queryByText(/思考過程|chain of thought/i)).toBeNull()
  })

  it('shows provider token usage as a factual runtime event', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /執行記錄/ }))
    expect(screen.getByText('模型用量：輸入 120，輸出 30')).toBeTruthy()
  })

  it('expands the factual steps from the compact summary', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持倉查詢完成' } },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'completed',
              data: { selected_tools: ['get_portfolio'] },
            },
          },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /執行記錄/ }))

    expect(screen.getByText('呼叫工具：get_portfolio')).toBeTruthy()
    expect(screen.getByText('{"market":"CN"}')).toBeTruthy()
    expect(screen.getByText('持倉查詢完成')).toBeTruthy()
    expect(screen.getByText('工具研究完成：選出 1 個')).toBeTruthy()
  })

  it('distinguishes tool exposure and model-side search from execution', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'exposure',
              data: { direct_tools: ['get_quote'], loaded_tools: [] },
            },
          },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'searched',
              data: { selected_tools: ['get_fundamentals'] },
            },
          },
          { event: 'tool_call_start', data: { name: 'get_fundamentals', arguments: {} } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /執行記錄/ }))

    expect(screen.getByText('工具目錄已準備：1 個直達，0 個已載入')).toBeTruthy()
    expect(screen.getByText('工具搜尋完成：載入 1 個')).toBeTruthy()
    expect(screen.getByText('呼叫工具：get_fundamentals')).toBeTruthy()
  })
})
