import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'

import { beforeEach, describe, expect, it, vi } from 'vitest'

type WorkerEvent = {
  request?: { method: string; mode: string; url: string }
  respondWith?: (response: Promise<Response>) => void
  waitUntil?: (work: Promise<unknown>) => void
}

function requestKey(request: string | { url?: string }) {
  return typeof request === 'string' ? request : request.url || ''
}

function loadWorker(source = fs.readFileSync(path.resolve(process.cwd(), 'public/sw.js'), 'utf8')) {
  const listeners = new Map<string, (event: WorkerEvent) => void>()
  const stores = new Map<string, Map<string, Response>>()
  const deleted: string[] = []
  const fetchMock = vi.fn()
  const skipWaiting = vi.fn()
  const claim = vi.fn()

  const caches = {
    open: vi.fn(async (name: string) => {
      const store = stores.get(name) ?? new Map<string, Response>()
      stores.set(name, store)

      return {
        put: vi.fn(async (request: string | { url?: string }, response: Response) => {
          store.set(requestKey(request), response.clone())
        })
      }
    }),
    keys: vi.fn(async () => [...stores.keys()]),
    delete: vi.fn(async (name: string) => {
      deleted.push(name)

      return stores.delete(name)
    }),
    match: vi.fn(async (request: string | { url?: string }) => {
      const key = requestKey(request)

      for (const store of stores.values()) {
        const response = store.get(key)

        if (response) {
          return response.clone()
        }
      }

      return undefined
    })
  }

  const self = {
    addEventListener: (type: string, listener: (event: WorkerEvent) => void) => listeners.set(type, listener),
    clients: { claim },
    location: { origin: 'https://hermes.test' },
    skipWaiting
  }

  vm.runInNewContext(source, {
    URL,
    Promise,
    Response,
    caches,
    fetch: fetchMock,
    self
  })

  return { caches, claim, deleted, fetchMock, listeners, skipWaiting, stores }
}

async function install(worker: ReturnType<typeof loadWorker>) {
  let work: Promise<unknown> | undefined
  worker.listeners.get('install')!({ waitUntil: value => (work = value) })
  await work
}

async function dispatchFetch(
  worker: ReturnType<typeof loadWorker>,
  request: { method?: string; mode?: string; url: string }
) {
  let response: Promise<Response> | undefined
  worker.listeners.get('fetch')!({
    request: {
      method: request.method ?? 'GET',
      mode: request.mode ?? 'cors',
      url: request.url
    },
    respondWith: value => (response = value)
  })

  return response
}

describe('PWA service-worker cache contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    ['bare API', { url: 'https://hermes.test/api' }],
    ['nested API', { url: 'https://hermes.test/api/status' }],
    ['non-GET', { method: 'POST', url: 'https://hermes.test/assets/app.js' }],
    ['cross-origin', { url: 'https://other.test/assets/app.js' }],
    ['login', { mode: 'navigate', url: 'https://hermes.test/login' }],
    ['auth callback', { mode: 'navigate', url: 'https://hermes.test/auth/callback' }]
  ])('never intercepts %s requests', async (_label, request) => {
    const worker = loadWorker()

    expect(await dispatchFetch(worker, request)).toBeUndefined()
    expect(worker.caches.match).not.toHaveBeenCalled()
    expect(worker.fetchMock).not.toHaveBeenCalled()
  })

  it('installs only a credential-free explicit offline document', async () => {
    const worker = loadWorker()
    await install(worker)

    const cached = await worker.caches.match('/__hermes_pwa_offline__')
    const html = await cached!.text()

    expect(html).toContain('Hermes is offline')
    expect(html).toContain('Reconnect to the tailnet')
    expect(html).not.toMatch(/session[_-]?token|authorization|cookie/i)
    expect(worker.skipWaiting).toHaveBeenCalledOnce()
  })

  it('never caches live navigation HTML that may contain an injected token', async () => {
    const worker = loadWorker()
    await install(worker)
    worker.fetchMock.mockResolvedValue(
      new Response('<script>window.__HERMES_SESSION_TOKEN__="secret"</script>', {
        headers: { 'Content-Type': 'text/html' }
      })
    )

    const response = await dispatchFetch(worker, {
      mode: 'navigate',
      url: 'https://hermes.test/chat'
    })

    expect(await (await response!).text()).toContain('secret')
    expect(await worker.caches.match('/')).toBeUndefined()
    expect(await worker.caches.match('https://hermes.test/chat')).toBeUndefined()
  })

  it('returns the explicit reconnect document when an offline navigation has no live response', async () => {
    const worker = loadWorker()
    await install(worker)
    worker.fetchMock.mockRejectedValue(new Error('offline'))

    const response = await dispatchFetch(worker, {
      mode: 'navigate',
      url: 'https://hermes.test/chat'
    })

    const html = await (await response!).text()

    expect(html).toContain('Hermes is offline')
    expect(html).toContain('Reconnect to the tailnet')
  })

  it('invalidates every prior cache version on activation', async () => {
    const worker = loadWorker()
    worker.stores.set('hermes-pwa-v1', new Map())
    worker.stores.set('hermes-pwa-v2', new Map())
    await install(worker)
    let work: Promise<unknown> | undefined

    worker.listeners.get('activate')!({ waitUntil: value => (work = value) })
    await work

    expect(worker.deleted).toEqual(['hermes-pwa-v1', 'hermes-pwa-v2'])
    expect(worker.stores.has('hermes-pwa-v3')).toBe(true)
    expect(worker.claim).toHaveBeenCalledOnce()
  })

  it('still caches same-origin GET assets by their versioned URL', async () => {
    const worker = loadWorker()
    await install(worker)
    worker.fetchMock.mockResolvedValue(new Response('asset-v1', { status: 200 }))
    const request = { url: 'https://hermes.test/assets/app-hash.js' }

    const first = await dispatchFetch(worker, request)
    expect(await (await first!).text()).toBe('asset-v1')

    const second = await dispatchFetch(worker, request)
    expect(await (await second!).text()).toBe('asset-v1')
    expect(worker.fetchMock).toHaveBeenCalledOnce()
  })
})
