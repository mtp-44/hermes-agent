/**
 * Side-effect module: installs the browser `window.hermesDesktop` shim at
 * import time. MUST be the first import of the PWA entry so the facade exists
 * before any renderer module evaluates (boot hard-fails without it — see
 * use-gateway-boot.ts). A static side-effect import (rather than a dynamic
 * `import('../main')`) keeps ordering guaranteed by ES module semantics and
 * sidesteps a rolldown miscompile of inlined dynamic imports under
 * `codeSplitting: false` (`__reExport$1 is not defined`).
 */
import './pwa.css'

import { installHermesDesktopShim, isAuthGated, redirectToLogin } from './hermes-desktop-shim'
import { installReconnectProbe } from './reconnect-probe'

installHermesDesktopShim()
installReconnectProbe()

// Gated gateway (tailnet bind): if there's no valid session cookie yet, hop
// to the server-rendered /login page up front instead of letting the renderer
// boot into a wall of 401s. Fire-and-forget — the redirect interrupts boot.
if (isAuthGated()) {
  void fetch('/api/auth/me').then(response => {
    if (response.status === 401) {
      redirectToLogin()
    }
  })
}

// Browsers scroll the nearest clipped ancestor to reveal a focused element
// (scroll-into-view-on-focus) — even `overflow: hidden` containers, which CAN
// be scrolled programmatically. During boot the composer autofocuses while the
// layout is still settling, which leaves the app's overflow-hidden shell stuck
// part-scrolled (chat content pushed above the viewport). Electron's window
// lifecycle happens to dodge this; a plain browser doesn't. An overflow-hidden
// container is a clipping shell, never a legitimate scroller — so undo any
// browser-initiated scroll on one.
document.addEventListener(
  'scroll',
  event => {
    const el = event.target
    if (!(el instanceof Element) || (!el.scrollTop && !el.scrollLeft)) {
      return
    }
    const style = getComputedStyle(el)
    if (style.overflowY === 'hidden' && style.overflowX === 'hidden') {
      el.scrollTop = 0
      el.scrollLeft = 0
    }
  },
  { capture: true, passive: true }
)

// Touch affordance for the collapsed hover-reveal panes (chat sidebar on
// narrow viewports): the renderer's reveal is pure CSS :hover plus a
// `hermes:pane-toggle-reveal` CustomEvent used by the mod+b keybind
// (pane-shell.tsx). Touch devices have no hover, so tapping the (pointer-
// active) edge trigger strip dispatches the same event — and a tap anywhere
// outside a force-opened pane closes it again. Uses only the renderer's own
// public seams; no renderer changes.
document.addEventListener('click', event => {
  const target = event.target
  if (!(target instanceof Element)) {
    return
  }

  const trigger = target.closest('[data-pane-reveal-trigger]')
  const paneId = trigger?.closest('[data-pane-id]')?.getAttribute('data-pane-id')
  if (paneId) {
    window.dispatchEvent(new CustomEvent('hermes:pane-toggle-reveal', { detail: { id: paneId } }))
    return
  }

  // Tap outside a forced-open reveal pane → close it.
  for (const pane of document.querySelectorAll('[data-pane-hover-reveal="open"]')) {
    if (!pane.contains(target)) {
      const id = pane.getAttribute('data-pane-id')
      if (id) {
        window.dispatchEvent(new CustomEvent('hermes:pane-toggle-reveal', { detail: { id } }))
      }
    }
  }
})

// Diagnostic overlay (`?pwa-debug=1`): floats viewport/zoom/safe-area numbers
// and the widest offending elements over the app, so device-specific layout
// bugs (iOS) can be diagnosed from a screenshot instead of cable debugging.
if (new URLSearchParams(window.location.search).has('pwa-debug')) {
  const panel = document.createElement('pre')
  panel.style.cssText =
    'position:fixed;bottom:80px;left:4px;right:4px;z-index:99999;background:rgba(0,0,0,.85);' +
    'color:#0f0;font:11px/1.4 monospace;padding:8px;border-radius:8px;pointer-events:none;' +
    'white-space:pre-wrap;word-break:break-all;max-height:60vh;overflow:hidden'

  const refresh = () => {
    const iw = window.innerWidth
    const offenders: string[] = []
    for (const el of Array.from(document.querySelectorAll('body *'))) {
      const r = el.getBoundingClientRect()
      if (r.right > iw + 1 && r.width > 40 && offenders.length < 6) {
        const cls = String((el as HTMLElement).className).slice(0, 60)
        offenders.push(`+${Math.round(r.right - iw)}px  <${el.tagName.toLowerCase()}> ${cls}`)
      }
    }
    const vv = window.visualViewport
    const saProbe = document.createElement('div')
    saProbe.style.cssText =
      'position:fixed;visibility:hidden;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)'
    document.body.appendChild(saProbe)
    const saStyle = getComputedStyle(saProbe)
    const sa = `${saStyle.paddingTop} / ${saStyle.paddingBottom}`
    saProbe.remove()
    const shell = document.querySelector('[style*="--titlebar-height"]')
    const shellTh = shell ? getComputedStyle(shell as HTMLElement).getPropertyValue('--titlebar-height') : '?'
    panel.textContent = [
      `build: ${document.querySelector('script[src*="/assets/"]')?.getAttribute('src')?.slice(-18)}`,
      `innerW/H: ${iw}x${window.innerHeight}  clientW: ${document.documentElement.clientWidth}`,
      `docScrollW: ${document.documentElement.scrollWidth}  bodyScrollW: ${document.body.scrollWidth}`,
      `visualViewport: ${vv ? `${Math.round(vv.width)}x${Math.round(vv.height)} scale=${vv.scale.toFixed(2)} offL=${Math.round(vv.offsetLeft)}` : 'n/a'}`,
      `safe-area t/b: ${sa}   titlebar-h: ${shellTh || '?'}`,
      `standalone: ${window.matchMedia('(display-mode: standalone)').matches}  coarse: ${window.matchMedia('(pointer: coarse)').matches}`,
      offenders.length ? 'WIDE ELEMENTS (right edge past viewport):' : 'no elements past right edge',
      ...offenders
    ].join('\n')
  }
  window.addEventListener('load', () => {
    document.body.appendChild(panel)
    refresh()
    setInterval(refresh, 2000)
  })
}

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(err => {
      // eslint-disable-next-line no-console
      console.warn('[hermes-pwa] service worker registration failed', err)
    })
  })
}
