import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantWelcome } from '@/components/assistant/AssistantWelcome'

describe('AssistantWelcome', () => {
  it('starts a focused research question from a suggested entry point', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(<AssistantWelcome onSubmit={onSubmit} />)

    expect(screen.getByRole('heading', { name: '今天想研究什麼？' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '診斷我的持倉' }))

    expect(onSubmit).toHaveBeenCalledWith('診斷我的持倉風險和關鍵關注點')
  })
})
