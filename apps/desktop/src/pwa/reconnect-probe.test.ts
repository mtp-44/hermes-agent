import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { installReconnectProbe, RECONNECT_PROBE_TTL_MS } from './reconnect-probe'

const ENABLE_KEY = 'hermes-pwa-reconnect-probe'
const ENABLED_AT_KEY = 'hermes-pwa-reconnect-probe-enabled-at'
const NOW = Date.UTC(2026, 6, 27, 12)

describe('PWA reconnect probe preference', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.history.replaceState({}, '', '/chat')
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
    window.history.replaceState({}, '', '/chat')
  })

  it('self-disables a persisted probe after approximately 24 hours', () => {
    window.localStorage.setItem(ENABLE_KEY, '1')
    window.localStorage.setItem(ENABLED_AT_KEY, String(NOW - RECONNECT_PROBE_TTL_MS))

    installReconnectProbe()

    expect(window.localStorage.getItem(ENABLE_KEY)).toBeNull()
    expect(window.localStorage.getItem(ENABLED_AT_KEY)).toBeNull()
  })

  it('keeps query-string enable and disable semantics while timestamping enablement', () => {
    window.localStorage.setItem(ENABLE_KEY, '1')
    window.localStorage.setItem(ENABLED_AT_KEY, String(NOW - RECONNECT_PROBE_TTL_MS))
    window.history.replaceState({}, '', '/chat?pwa-reconnect-probe=1')

    installReconnectProbe()

    expect(window.localStorage.getItem(ENABLE_KEY)).toBe('1')
    expect(window.localStorage.getItem(ENABLED_AT_KEY)).toBe(String(NOW))

    window.history.replaceState({}, '', '/chat?pwa-reconnect-probe=0')
    installReconnectProbe()

    expect(window.localStorage.getItem(ENABLE_KEY)).toBeNull()
    expect(window.localStorage.getItem(ENABLED_AT_KEY)).toBeNull()
  })
})
