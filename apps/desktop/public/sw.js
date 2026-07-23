/*
 * Hermes tailnet-PWA service worker (hand-written; no build-time plugin).
 *
 * Strategy:
 *   - /api/* and non-GET:      never touched — the live gateway is the app.
 *   - navigations:             network-first, falling back to the cached shell
 *                              so the installed PWA still opens offline.
 *   - static assets (hashed):  cache-first with background fill.
 *
 * Bump VERSION to invalidate every cache after a breaking change.
 */
const VERSION = 'hermes-pwa-v1'

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches
      .keys()
      .then(keys => Promise.all(keys.filter(key => key !== VERSION).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', event => {
  const request = event.request
  const url = new URL(request.url)

  if (request.method !== 'GET' || url.origin !== self.location.origin) {
    return
  }

  // The live API (REST + WS upgrade) must never be served from cache.
  if (url.pathname === '/api' || url.pathname.startsWith('/api/')) {
    return
  }

  // App-shell navigations: network-first so the injected session token stays
  // fresh; cached shell keeps the PWA opening when the gateway is unreachable.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok) {
            const copy = response.clone()
            caches.open(VERSION).then(cache => cache.put('/', copy))
          }
          return response
        })
        .catch(() => caches.match('/'))
    )
    return
  }

  // Static assets: cache-first (Vite hashes them, so staleness is impossible
  // for /assets/*; the handful of unhashed public files revalidate on
  // navigation-driven reloads).
  event.respondWith(
    caches.match(request).then(
      hit =>
        hit ||
        fetch(request).then(response => {
          if (response.ok) {
            const copy = response.clone()
            caches.open(VERSION).then(cache => cache.put(request, copy))
          }
          return response
        })
    )
  )
})
