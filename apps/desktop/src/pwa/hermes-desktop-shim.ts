/**
 * Browser implementation of the `window.hermesDesktop` preload facade.
 *
 * The desktop renderer boots against the Electron preload bridge
 * (`electron/preload.cjs`). In the tailnet PWA the very same renderer runs in
 * a plain browser, served same-origin by the Hermes gateway
 * (`hermes serve` / `hermes dashboard`), so the facade collapses to:
 *
 *   - connection resolution → `window.location.origin` + the session token
 *     the gateway injects into the served index.html
 *     (`window.__HERMES_SESSION_TOKEN__`, see `web_server.py:mount_spa`)
 *   - REST proxy `api()`    → same-origin `fetch()` with the
 *     `X-Hermes-Session-Token` header (mirrors `fetchJson` in
 *     `electron/main.cjs`, including its error/HTML-detection semantics)
 *   - WS URL minting        → `wss://<host>/api/ws?token=…` via the shared
 *     `buildHermesWebSocketUrl` helper
 *
 * Everything desktop-only (multi-window, git, terminal, local FS, updates,
 * bootstrap, pet overlay) degrades to inert stubs. A Proxy fallback catches
 * any facade member this file forgot, so a missed call logs a warning
 * instead of crashing the renderer.
 *
 * This file is only ever imported by the PWA entry (`src/pwa/main.tsx`); the
 * Electron build is untouched.
 */
import { buildHermesWebSocketUrl } from '@hermes/shared'

import type { HermesApiRequest, HermesConnection } from '@/global'

declare global {
  interface Window {
    /** Injected by the gateway into the served SPA HTML (loopback/token mode). */
    __HERMES_SESSION_TOKEN__?: string
    /** True when the gateway's auth gate (cookie + ws-ticket) is engaged. */
    __HERMES_AUTH_REQUIRED__?: boolean
  }
}

const TOKEN_STORAGE_KEY = 'hermes-pwa-session-token'
const DEFAULT_FETCH_TIMEOUT_MS = 15_000 // mirrors electron/hardening.cjs

/**
 * True when the gateway's auth gate is engaged (non-loopback bind — e.g. the
 * tailnet FQDN). The server then injects `__HERMES_AUTH_REQUIRED__ = true`
 * and NO session token: REST authenticates via the HttpOnly session cookie
 * (sent automatically on same-origin fetch), and WebSockets via single-use
 * `?ticket=` values minted from `POST /api/auth/ws-ticket` (30s TTL — mint
 * immediately before every connect).
 */
export function isAuthGated(): boolean {
  return window.__HERMES_AUTH_REQUIRED__ === true
}

/** Send the browser to the gateway's login page, preserving the location. */
export function redirectToLogin(): void {
  const next = encodeURIComponent(window.location.pathname + window.location.hash)
  const url = `/login?next=${next}`

  // Expose a non-sensitive signal for diagnostics/tests before navigation
  // tears down the page. Never include the failed request or response body.
  window.dispatchEvent(new CustomEvent('hermes:pwa-login-required', { detail: { url } }))
  window.location.replace(url)
}

async function mintWsTicket(): Promise<string> {
  const response = await fetch(`${window.location.origin}/api/auth/ws-ticket`, {
    method: 'POST',
    signal: AbortSignal.timeout(DEFAULT_FETCH_TIMEOUT_MS)
  })
  if (response.status === 401) {
    // Session cookie expired mid-use: a fresh login is the only fix.
    redirectToLogin()
    throw new Error('Session expired — redirecting to login')
  }
  if (!response.ok) {
    throw new Error(`ws-ticket mint failed: ${response.status}`)
  }
  const body = (await response.json()) as { ticket?: string }
  if (!body.ticket) {
    throw new Error('ws-ticket mint returned no ticket')
  }
  return body.ticket
}

function resolveSessionToken(): string {
  if (isAuthGated()) {
    // Auth-gated deployments use only HttpOnly cookies + single-use WS
    // tickets. Remove legacy/debug token artifacts so they cannot survive in
    // browser storage or a copied production URL.
    try {
      localStorage.removeItem(TOKEN_STORAGE_KEY)
      const url = new URL(window.location.href)

      if (url.searchParams.has('token')) {
        url.searchParams.delete('token')
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
      }
    } catch {
      // Storage/history can be unavailable in hardened browser contexts. The
      // gated connection still returns no token below.
    }

    return ''
  }

  // Primary: the gateway injects the token into the HTML it serves.
  const injected = window.__HERMES_SESSION_TOKEN__
  if (injected) {
    return injected
  }

  // Dev/debug fallback: `?token=…` in the URL (persisted so reloads and the
  // installed PWA keep working), then localStorage.
  try {
    const fromQuery = new URLSearchParams(window.location.search).get('token')
    if (fromQuery) {
      localStorage.setItem(TOKEN_STORAGE_KEY, fromQuery)
      return fromQuery
    }
    return localStorage.getItem(TOKEN_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function buildConnection(token: string): HermesConnection {
  return {
    baseUrl: window.location.origin,
    wsUrl: buildHermesWebSocketUrl({ path: '/api/ws', authParam: token ? ['token', token] : undefined }),
    token,
    // 'remote' routes all FS/git helpers through the gateway's REST /api/fs
    // endpoints (see src/lib/desktop-fs.ts) instead of Electron-only IPC.
    mode: 'remote',
    // 'oauth' makes resolveGatewayWsUrl() re-mint via getGatewayWsUrl before
    // EVERY connect (tickets are single-use, 30s TTL) instead of reusing the
    // cached conn.wsUrl. Token mode reuses the long-lived token URL.
    authMode: isAuthGated() ? 'oauth' : 'token',
    source: 'env',
    isFullscreen: false,
    nativeOverlayWidth: 0,
    windowButtonPosition: null,
    logs: []
  }
}

/** Same-origin fetch mirroring electron/main.cjs `fetchJson` semantics. */
export async function apiFetch<T>(
  token: string,
  request: HermesApiRequest,
  onUnauthorized: () => void = redirectToLogin
): Promise<T> {
  const timeoutMs =
    Number.isFinite(request.timeoutMs) && (request.timeoutMs as number) > 0
      ? (request.timeoutMs as number)
      : DEFAULT_FETCH_TIMEOUT_MS
  const url = `${window.location.origin}${request.path}`
  // Gated mode authenticates via the HttpOnly session cookie (attached
  // automatically on same-origin fetch); token mode via the injected header.
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) {
    headers['X-Hermes-Session-Token'] = token
  }
  let response: Response

  try {
    response = await fetch(url, {
      method: request.method || 'GET',
      headers,
      body: request.body === undefined ? undefined : JSON.stringify(request.body),
      signal: AbortSignal.timeout(timeoutMs)
    })
  } catch (error) {
    if (error instanceof DOMException && (error.name === 'AbortError' || error.name === 'TimeoutError')) {
      throw new Error('Hermes gateway timed out — check the gateway and tailnet connection')
    }

    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      throw new Error('Network unavailable — reconnect to the tailnet and retry')
    }

    throw new Error('Hermes gateway is unreachable over the tailnet — check Tailscale and the gateway')
  }

  if (response.status === 401 && isAuthGated()) {
    // Do not read or surface the response body: it may reflect request
    // details. The auth gate is the only layer that should explain this
    // failure to the user.
    onUnauthorized()
    throw new Error('Session expired — redirecting to login')
  }

  const text = await response.text()

  if (response.status >= 400) {
    throw new Error(`${response.status}: ${text || response.statusText}`)
  }

  if (!text) {
    return null as T
  }

  // A 2xx whose body is HTML means the request fell through to the SPA
  // index.html (unregistered /api path) — surface a clear diagnostic instead
  // of an opaque JSON.parse SyntaxError.
  const looksHtml = /^\s*<(?:!doctype|html)/i.test(text)
  const contentType = response.headers.get('content-type') || ''
  if (looksHtml || contentType.includes('text/html')) {
    throw new Error(
      `Expected JSON from ${url} but got HTML (status ${response.status}). ` +
        'The endpoint is likely missing on the Hermes backend.'
    )
  }

  try {
    return JSON.parse(text) as T
  } catch {
    throw new Error(`Invalid JSON from ${url} (status ${response.status}): ${text.slice(0, 200)}`)
  }
}

type HermesDesktopFacade = Window['hermesDesktop']

// A subscribe-shaped no-op: accepts a callback, returns an unsubscriber.
const noopUnsubscribe = () => () => {}

export function installHermesDesktopShim(): void {
  if (window.hermesDesktop) {
    // Running inside the real Electron shell — never shadow the preload bridge.
    return
  }

  const token = resolveSessionToken()
  const connection = buildConnection(token)

  const shim: HermesDesktopFacade = {
    // ---- connection / transport (the load-bearing part) --------------------
    getConnection: async () => connection,
    revalidateConnection: async () => ({ ok: true, rebuilt: false }),
    touchBackend: async () => ({ ok: true }),
    getGatewayWsUrl: async () =>
      isAuthGated()
        ? buildHermesWebSocketUrl({ path: '/api/ws', authParam: ['ticket', await mintWsTicket()] })
        : buildHermesWebSocketUrl({ path: '/api/ws', authParam: ['token', token] }),
    api: <T>(request: HermesApiRequest) => apiFetch<T>(token, request),

    // ---- boot lifecycle (the gateway is already up when the page loads) ----
    getBootProgress: async () => ({
      error: null,
      fakeMode: false,
      message: 'Connected to Hermes gateway',
      phase: 'backend.ready',
      progress: 10,
      running: true,
      timestamp: Date.now()
    }),
    onBootProgress: noopUnsubscribe,
    onBackendExit: noopUnsubscribe,
    onPowerResume: noopUnsubscribe,
    onWindowStateChanged: noopUnsubscribe,
    onFocusSession: noopUnsubscribe,
    onNotificationAction: noopUnsubscribe,
    onPreviewFileChanged: noopUnsubscribe,
    onClosePreviewRequested: noopUnsubscribe,
    onOpenUpdatesRequested: noopUnsubscribe,
    onDeepLink: noopUnsubscribe,
    signalDeepLinkReady: async () => ({ ok: true }),

    // ---- profile (one gateway, one profile — the server's active one) ------
    profile: {
      get: async () => ({ profile: null }),
      set: async name => ({ profile: name })
    },

    // ---- browser-native equivalents ----------------------------------------
    notify: async payload => {
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') {
        return false
      }
      new Notification(payload?.title || 'Hermes', {
        body: payload?.body || '',
        silent: Boolean(payload?.silent)
      })
      return true
    },
    requestMicrophoneAccess: async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        stream.getTracks().forEach(track => track.stop())
        return true
      } catch {
        return false
      }
    },
    writeClipboard: async text => {
      try {
        await navigator.clipboard.writeText(text)
        return true
      } catch {
        return false
      }
    },
    openExternal: async url => {
      window.open(url, '_blank', 'noopener,noreferrer')
    },
    openPreviewInBrowser: async url => {
      window.open(url, '_blank', 'noopener,noreferrer')
    },
    saveImageFromUrl: async url => {
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = ''
      anchor.click()
      return true
    },
    fetchLinkTitle: async () => '',

    // ---- desktop-only: degrade to inert stubs ------------------------------
    openSessionWindow: async () => ({ ok: false, error: 'unsupported' }),
    openNewSessionWindow: async () => ({ ok: false, error: 'unsupported' }),
    petOverlay: {
      open: async () => ({ ok: false }),
      close: async () => ({ ok: true }),
      setBounds: () => {},
      setIgnoreMouse: () => {},
      setFocusable: () => {},
      pushState: () => {},
      control: () => {},
      onState: noopUnsubscribe,
      onControl: noopUnsubscribe
    },
    getConnectionConfig: async profile => ({
      envOverride: true,
      mode: 'remote',
      profile: profile ?? null,
      remoteAuthMode: 'token',
      remoteOauthConnected: false,
      remoteTokenPreview: null,
      remoteTokenSet: true,
      remoteUrl: window.location.origin
    }),
    saveConnectionConfig: async payload => ({
      envOverride: true,
      mode: 'remote',
      profile: payload.profile ?? null,
      remoteAuthMode: 'token',
      remoteOauthConnected: false,
      remoteTokenPreview: null,
      remoteTokenSet: true,
      remoteUrl: window.location.origin
    }),
    applyConnectionConfig: async payload => ({
      envOverride: true,
      mode: 'remote',
      profile: payload.profile ?? null,
      remoteAuthMode: 'token',
      remoteOauthConnected: false,
      remoteTokenPreview: null,
      remoteTokenSet: true,
      remoteUrl: window.location.origin
    }),
    testConnectionConfig: async () => ({ baseUrl: window.location.origin, ok: true, version: null }),
    probeConnectionConfig: async remoteUrl => ({
      baseUrl: remoteUrl,
      reachable: false,
      authMode: 'unknown',
      providers: [],
      version: null,
      error: 'Connection editing is not available in the PWA'
    }),
    oauthLoginConnectionConfig: async remoteUrl => ({ ok: false, baseUrl: remoteUrl, connected: false }),
    oauthLogoutConnectionConfig: async () => ({ ok: false, connected: false }),
    readFileDataUrl: async () => {
      throw new Error('Local file access is not available in the browser')
    },
    readFileText: async () => {
      throw new Error('Local file access is not available in the browser')
    },
    // Browser bridge for the "attach a file" flows: the renderer expects HOST
    // paths (attachContextFilePath reads them back over /api/fs). Open a real
    // file picker, upload each pick to the gateway's managed-files endpoint
    // under ~/.hermes/pwa-uploads/, and hand back the resulting host paths —
    // downstream code then works unchanged.
    selectPaths: async options => {
      if (options?.directories) {
        return [] // browsers can't upload directories — behave like "cancel"
      }
      const files = await new Promise<File[]>(resolve => {
        const input = document.createElement('input')
        input.type = 'file'
        input.multiple = options?.multiple !== false
        const extensions = (options?.filters ?? []).flatMap(f => f.extensions).filter(ext => ext && ext !== '*')
        if (extensions.length) {
          input.accept = extensions.map(ext => `.${ext.replace(/^\./, '')}`).join(',')
        }
        // Keep the picker rendered and DOM-connected. iOS Home Screen WebKit
        // ignores programmatic activation of display:none file inputs even
        // when input.click() runs synchronously inside the user's menu press.
        // A transparent off-screen control preserves that trusted activation
        // without adding a focusable or visible element to the page.
        input.tabIndex = -1
        input.setAttribute('aria-hidden', 'true')
        Object.assign(input.style, {
          position: 'fixed',
          left: '-9999px',
          top: '0',
          width: '1px',
          height: '1px',
          opacity: '0',
          pointerEvents: 'none'
        })
        input.onchange = () => {
          resolve(Array.from(input.files ?? []))
          input.remove()
        }
        input.oncancel = () => {
          resolve([])
          input.remove()
        }
        document.body.appendChild(input)
        input.click()
      })

      const uploaded: string[] = []
      for (const file of files) {
        if (file.size > 25 * 1024 * 1024) {
          throw new Error(`"${file.name}" is larger than the 25 MB PWA upload limit`)
        }
        const dataUrl = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result))
          reader.onerror = () => reject(reader.error ?? new Error('read failed'))
          reader.readAsDataURL(file)
        })
        const safeName = file.name.replace(/[^\w.-]+/g, '_').slice(-80) || 'upload'
        const hostPath = `~/.hermes/pwa-uploads/${Date.now()}-${safeName}`
        let result: { ok: boolean; path?: string; entry?: { path?: string } }
        try {
          result = await apiFetch(token, {
            path: '/api/files/upload',
            method: 'POST',
            body: { path: hostPath, data_url: dataUrl, overwrite: true },
            timeoutMs: 120_000
          })
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error)
          throw new Error(`Upload failed for "${file.name}": ${detail}`)
        }
        uploaded.push(result.entry?.path || result.path || hostPath)
      }
      return uploaded
    },
    saveImageBuffer: async () => '',
    saveClipboardImage: async () => '',
    getPathForFile: () => '',
    normalizePreviewTarget: async () => null,
    watchPreviewFile: async () => ({ id: '', path: '' }),
    stopPreviewFileWatch: async () => false,
    setTitleBarTheme: () => {},
    setNativeTheme: () => {},
    setTranslucency: () => {},
    setPreviewShortcutActive: () => {},
    sanitizeWorkspaceCwd: async cwd => ({ cwd: cwd ?? '', sanitized: false }),
    settings: {
      getDefaultProjectDir: async () => ({ defaultLabel: '', dir: null, resolvedCwd: '' }),
      pickDefaultProjectDir: async () => ({ canceled: true, dir: null }),
      setDefaultProjectDir: async dir => ({ dir })
    },
    revealLogs: async () => ({ ok: false, path: '', error: 'unsupported' }),
    getRecentLogs: async () => ({ path: '', lines: [] }),
    readDir: async () => ({ entries: [] }),
    terminal: {
      dispose: async () => true,
      resize: async () => true,
      start: async () => {
        throw new Error('The embedded terminal is not available in the browser')
      },
      write: async () => false,
      onData: () => () => {},
      onExit: () => () => {}
    },
    getBootstrapState: async () => ({
      active: false,
      manifest: null,
      stages: {},
      error: null,
      log: [],
      startedAt: null,
      completedAt: null,
      unsupportedPlatform: null
    }),
    resetBootstrap: async () => ({ ok: false }),
    repairBootstrap: async () => ({ ok: false }),
    cancelBootstrap: async () => ({ ok: false, cancelled: false }),
    onBootstrapEvent: noopUnsubscribe,
    getVersion: async () => ({
      appVersion: 'pwa',
      electronVersion: '',
      nodeVersion: '',
      platform: 'web',
      hermesRoot: ''
    }),
    getRemoteDisplayReason: async () => null,
    updates: {
      check: async () => ({ supported: false }),
      apply: async () => ({ ok: false }),
      getBranch: async () => ({ branch: '' }),
      setBranch: async name => ({ branch: name }),
      onProgress: noopUnsubscribe
    },
    uninstall: {
      summary: async () => {
        throw new Error('Uninstall is not available in the browser')
      },
      run: async () => ({ ok: false, error: 'unsupported' })
    },
    themes: {
      fetchMarketplace: async () => {
        throw new Error('Marketplace themes are not available in the browser')
      },
      searchMarketplace: async () => []
    }
  }

  // NOTE: deliberately NOT wrapped in a catch-all Proxy — the renderer
  // feature-detects optional facade members (`desktop.git`, `desktop.gitRoot`,
  // `desktop.revealPath`, …) with optional chaining, so a fabricated no-op
  // would make desktop-only features look supported and then crash one level
  // deeper. Required members are all implemented above (TS enforces it);
  // optional ones must stay genuinely `undefined`.
  window.hermesDesktop = shim
}
