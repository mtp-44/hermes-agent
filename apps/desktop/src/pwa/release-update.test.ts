import { beforeEach, describe, expect, it, vi } from 'vitest'

const { notify } = vi.hoisted(() => ({ notify: vi.fn() }))

vi.mock('@/store/notifications', () => ({ notify }))

import { checkForReleaseUpdate, installedReleaseId } from './release-update'

describe('PWA release update flow', () => {
  beforeEach(() => {
    document.head.innerHTML = ''
    notify.mockClear()
    vi.restoreAllMocks()
  })

  it('reads the immutable release embedded in the served document', () => {
    document.head.innerHTML = '<meta name="hermes-pwa-build" content="release-a">'

    expect(installedReleaseId()).toBe('release-a')
  })

  it('offers an explicit reload when the promoted release changes', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ release_id: 'release-b' }), {
        headers: { 'Content-Type': 'application/json' },
        status: 200
      })
    )

    expect(await checkForReleaseUpdate('release-a')).toBe(true)
    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'hermes-pwa-release-update',
        action: expect.objectContaining({ label: 'Reload' })
      })
    )
  })

  it('stays quiet while the installed release is current', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ release_id: 'release-a' }), {
        headers: { 'Content-Type': 'application/json' },
        status: 200
      })
    )

    expect(await checkForReleaseUpdate('release-a')).toBe(false)
    expect(notify).not.toHaveBeenCalled()
  })
})
