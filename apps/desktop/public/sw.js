/*
 * Hermes tailnet-PWA service worker (hand-written; no build-time plugin).
 *
 * Strategy:
 *   - /api/* and non-GET:      never touched — the live gateway is the app.
 *   - navigations:             network-first, falling back to a credential-free
 *                              offline/reconnect document.
 *   - static assets (hashed):  cache-first with background fill.
 *
 * The release command replaces the placeholder with the immutable release
 * stamp, so each promoted build owns a distinct cache.
 */
const VERSION = 'hermes-pwa-__HERMES_PWA_BUILD_STAMP__'
const OFFLINE_URL = '/__hermes_pwa_offline__'
const RELEASE_METADATA_URL = '/pwa-release.json'

const offlineResponse = () =>
  new Response(
    '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>Hermes offline</title></head><body><main><h1>Hermes is offline</h1>' +
      '<p>Reconnect to the tailnet and reload this page.</p></main></body></html>',
    { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
  )

const isAuthRoute = pathname =>
  pathname === '/login' || pathname === '/logout' || pathname === '/auth' || pathname.startsWith('/auth/')

self.addEventListener('install', event => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then(cache => cache.put(OFFLINE_URL, offlineResponse()))
      .then(() => self.skipWaiting())
  )
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
  if (
    url.pathname === '/api' ||
    url.pathname.startsWith('/api/') ||
    url.pathname === RELEASE_METADATA_URL ||
    isAuthRoute(url.pathname)
  ) {
    return
  }

  // App-shell navigations: network-first. Never cache the live HTML because it
  // can contain an injected loopback session token. The offline fallback is a
  // static credential-free document created during service-worker install.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL).then(response => response || offlineResponse()))
    )
    return
  }

  // Static assets: cache-first (Vite hashes them, so staleness is impossible
  // for /assets/*; the handful of unhashed public files revalidate on
  // navigation-driven reloads).
  let cacheWrite = Promise.resolve()
  const response = caches.match(request).then(
    hit =>
      hit ||
      fetch(request).then(networkResponse => {
        if (networkResponse.ok) {
          const copy = networkResponse.clone()
          cacheWrite = caches.open(VERSION).then(cache => cache.put(request, copy))
        }
        return networkResponse
      })
  )

  event.respondWith(response)
  event.waitUntil(response.then(() => cacheWrite))
})
