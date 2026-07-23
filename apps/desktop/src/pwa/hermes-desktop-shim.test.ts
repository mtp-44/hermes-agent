import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch, installHermesDesktopShim } from './hermes-desktop-shim'

const TOKEN_STORAGE_KEY = 'hermes-pwa-session-token'

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
}

function setPickedFiles(files: File[]) {
  return vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(function (this: HTMLInputElement) {
    Object.defineProperty(this, 'files', { configurable: true, value: files })
    this.onchange?.(new Event('change'))
  })
}

describe('PWA Hermes desktop shim', () => {
  beforeEach(() => {
    window.hermesDesktop = undefined as unknown as Window['hermesDesktop']
    delete window.__HERMES_AUTH_REQUIRED__
    delete window.__HERMES_SESSION_TOKEN__
    window.localStorage.clear()
    window.history.replaceState({}, '', '/chat')
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('uses the injected token for loopback REST and WebSocket transport', async () => {
    window.__HERMES_SESSION_TOKEN__ = 'loopback-token'
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }))

    installHermesDesktopShim()

    const connection = await window.hermesDesktop!.getConnection()
    expect(connection.authMode).toBe('token')
    expect(connection.token).toBe('loopback-token')
    expect(connection.wsUrl).toContain('token=loopback-token')

    await window.hermesDesktop!.api({ path: '/api/status' })
    expect(vi.mocked(fetch).mock.calls[0]?.[1]).toMatchObject({
      headers: expect.objectContaining({ 'X-Hermes-Session-Token': 'loopback-token' })
    })
  })

  it('removes query/storage tokens in auth-gated mode and relies on cookies', async () => {
    window.__HERMES_AUTH_REQUIRED__ = true
    window.__HERMES_SESSION_TOKEN__ = 'must-not-be-used'
    window.localStorage.setItem(TOKEN_STORAGE_KEY, 'legacy-token')
    window.history.replaceState({}, '', '/chat?token=query-token&keep=yes#thread')

    installHermesDesktopShim()

    const connection = await window.hermesDesktop!.getConnection()
    expect(connection.authMode).toBe('oauth')
    expect(connection.token).toBe('')
    expect(connection.wsUrl).not.toContain('token=')
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    expect(window.location.search).toBe('?keep=yes')
    expect(window.location.hash).toBe('#thread')
  })

  it('mints a distinct single-use ticket immediately before every reconnect', async () => {
    window.__HERMES_AUTH_REQUIRED__ = true
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ ticket: 'fresh-one' }))
      .mockResolvedValueOnce(jsonResponse({ ticket: 'fresh-two' }))

    installHermesDesktopShim()

    await expect(window.hermesDesktop!.getGatewayWsUrl()).resolves.toContain('ticket=fresh-one')
    await expect(window.hermesDesktop!.getGatewayWsUrl()).resolves.toContain('ticket=fresh-two')
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2)
    expect(vi.mocked(fetch).mock.calls.map(call => call[0])).toEqual([
      `${window.location.origin}/api/auth/ws-ticket`,
      `${window.location.origin}/api/auth/ws-ticket`
    ])
  })

  it('redirects a gated 401 without exposing its response body', async () => {
    window.__HERMES_AUTH_REQUIRED__ = true
    vi.mocked(fetch).mockResolvedValue(new Response('sensitive reflected request body', { status: 401 }))
    const loginRequired = vi.fn()

    const failure = (await apiFetch(
      '',
      {
        path: '/api/private',
        method: 'POST',
        body: { secret: 'must-not-leak' }
      },
      loginRequired
    ).catch(error => error)) as Error

    expect(failure.message).toBe('Session expired — redirecting to login')
    expect(failure.message).not.toContain('sensitive')
    expect(failure.message).not.toContain('must-not-leak')
    expect(loginRequired).toHaveBeenCalledOnce()
  })

  it('raises a clear error when a missing API route falls through to HTML', async () => {
    window.__HERMES_SESSION_TOKEN__ = 'loopback-token'
    vi.mocked(fetch).mockResolvedValue(
      new Response('<!doctype html><html><body>app shell</body></html>', {
        headers: { 'Content-Type': 'text/html' }
      })
    )

    installHermesDesktopShim()

    await expect(window.hermesDesktop!.api({ path: '/api/missing' })).rejects.toThrow(
      'Expected JSON from http://localhost:3000/api/missing but got HTML'
    )
  })

  it('uploads selected files to the managed endpoint and returns host paths', async () => {
    window.__HERMES_SESSION_TOKEN__ = 'loopback-token'
    setPickedFiles([new File(['hello'], 'Quarterly report.txt', { type: 'text/plain' })])
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true, entry: { path: '/host/pwa/report.txt' } }))

    installHermesDesktopShim()

    await expect(window.hermesDesktop!.selectPaths({ multiple: true })).resolves.toEqual(['/host/pwa/report.txt'])
    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    const body = JSON.parse(String(init?.body))

    expect(url).toBe(`${window.location.origin}/api/files/upload`)
    expect(init?.method).toBe('POST')
    expect(body.path).toMatch(/^~\/\.hermes\/pwa-uploads\/\d+-Quarterly_report\.txt$/)
    expect(body.data_url).toBe('data:text/plain;base64,aGVsbG8=')
  })

  it('surfaces upload size and backend errors', async () => {
    window.__HERMES_SESSION_TOKEN__ = 'loopback-token'
    const oversized = new File(['x'], 'huge.bin')
    Object.defineProperty(oversized, 'size', { value: 25 * 1024 * 1024 + 1 })
    setPickedFiles([oversized])
    installHermesDesktopShim()

    await expect(window.hermesDesktop!.selectPaths({})).rejects.toThrow('larger than the 25 MB PWA upload limit')

    window.hermesDesktop = undefined as unknown as Window['hermesDesktop']
    setPickedFiles([new File(['x'], 'small.txt')])
    vi.mocked(fetch).mockResolvedValue(new Response('upload rejected', { status: 413 }))
    installHermesDesktopShim()

    await expect(window.hermesDesktop!.selectPaths({})).rejects.toThrow('413: upload rejected')
  })

  it('fails denied browser permissions safely and leaves optional Electron APIs absent', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error('denied')) }
    })
    vi.stubGlobal(
      'Notification',
      class {
        static permission = 'denied'
      }
    )

    installHermesDesktopShim()

    await expect(window.hermesDesktop!.requestMicrophoneAccess()).resolves.toBe(false)
    await expect(window.hermesDesktop!.notify({ title: 'No permission' })).resolves.toBe(false)
    expect(window.hermesDesktop!.git).toBeUndefined()
    expect(window.hermesDesktop!.gitRoot).toBeUndefined()
    expect(window.hermesDesktop!.revealPath).toBeUndefined()
  })
})
