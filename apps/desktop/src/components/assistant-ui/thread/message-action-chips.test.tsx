import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MessageActionChips } from './assistant-message'

const { notifyError } = vi.hoisted(() => ({ notifyError: vi.fn() }))

vi.mock('@/store/notifications', async importOriginal => {
  const original = await importOriginal()

  return { ...(original as object), notifyError }
})

describe('MessageActionChips', () => {
  const api = vi.fn()
  const action = { label: 'Approve', callback_id: 'act:approve:token' }

  beforeEach(() => {
    window.hermesDesktop = { api } as unknown as Window['hermesDesktop']
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    window.hermesDesktop = undefined as unknown as Window['hermesDesktop']
  })

  it('replaces chips with the server acknowledgement after dispatch succeeds', async () => {
    api.mockResolvedValue({ ok: true, ack: 'Recorded' })
    render(<MessageActionChips actions={[action]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await screen.findByText('Recorded')
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(api).toHaveBeenCalledWith({
      path: '/api/actions/dispatch',
      method: 'POST',
      body: { callback_id: 'act:approve:token' }
    })
  })

  it('preserves an enabled retry path when dispatch fails', async () => {
    api.mockRejectedValue(new Error('gateway unavailable'))
    render(<MessageActionChips actions={[action]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(notifyError).toHaveBeenCalledOnce())
    expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByText('Recorded')).toBeNull()
  })
})
