const ENABLE_KEY = 'hermes-pwa-reconnect-probe'
const ENABLED_AT_KEY = 'hermes-pwa-reconnect-probe-enabled-at'
const EVENT_NAME = 'hermes:gateway-state'
const POLL_MS = 100
const REQUEST_TIMEOUT_MS = 500
export const RECONNECT_PROBE_TTL_MS = 24 * 60 * 60 * 1_000

type GatewayStateDetail = { state?: string }

export function emitGatewayStateForReconnectProbe(state: string): void {
  window.dispatchEvent(new CustomEvent<GatewayStateDetail>(EVENT_NAME, { detail: { state } }))
}

function disableReconnectProbe(): void {
  localStorage.removeItem(ENABLE_KEY)
  localStorage.removeItem(ENABLED_AT_KEY)
}

function hasLiveReconnectProbePreference(now = Date.now()): boolean {
  if (localStorage.getItem(ENABLE_KEY) !== '1') {
    return false
  }

  let enabledAt = Number(localStorage.getItem(ENABLED_AT_KEY))

  // Migrate the original boolean-only preference and recover safely from
  // malformed/future timestamps by starting one final bounded window.
  if (!Number.isFinite(enabledAt) || enabledAt <= 0 || enabledAt > now) {
    enabledAt = now
    localStorage.setItem(ENABLED_AT_KEY, String(enabledAt))
  }

  if (now - enabledAt < RECONNECT_PROBE_TTL_MS) {
    return true
  }

  disableReconnectProbe()

  return false
}

export function installReconnectProbe(): void {
  const parameter = new URLSearchParams(window.location.search).get('pwa-reconnect-probe')
  const now = Date.now()

  if (parameter === '1') {
    localStorage.setItem(ENABLE_KEY, '1')
    localStorage.setItem(ENABLED_AT_KEY, String(now))
  } else if (parameter === '0') {
    disableReconnectProbe()
  }

  if (!hasLiveReconnectProbePreference(now)) {
    return
  }

  const panel = document.createElement('pre')
  panel.style.cssText =
    'position:fixed;top:max(8px,env(safe-area-inset-top));left:8px;right:8px;z-index:100000;' +
    'background:rgba(0,0,0,.9);color:#7dff9b;font:12px/1.4 monospace;padding:10px;' +
    'border:1px solid #36c95f;border-radius:8px;pointer-events:none;white-space:pre-wrap'

  let outageObserved = false
  let gatewayOpenedAt: number | null = null
  let status = 'waiting for a disconnect'
  let active = true
  const samples: string[] = []

  const render = () => {
    panel.textContent = [
      'WP3 reconnect probe',
      status,
      ...(samples.length ? ['samples (reachability→gateway):', ...samples.slice(-3)] : []),
      'Disable: open /?pwa-reconnect-probe=0 once'
    ].join('\n')
  }

  const record = (probeStartedAt: number, probeEndedAt: number, openedAt: number) => {
    const lowerMs = Math.max(0, openedAt - probeEndedAt)
    const upperMs = Math.max(lowerMs, openedAt - probeStartedAt)
    const label = `${(lowerMs / 1000).toFixed(2)}–${(upperMs / 1000).toFixed(2)} s`
    samples.push(label)
    status = upperMs <= 2_000 ? `PASS ${label}` : `CHECK ${label}`
    outageObserved = false
    gatewayOpenedAt = null
    render()
  }

  const probe = async () => {
    const startedAt = performance.now()
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

    try {
      const response = await fetch(`/api/status?pwa_reconnect_probe=${Date.now()}`, {
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal
      })

      if (!response.ok) {
        throw new Error(`status ${response.status}`)
      }

      const endedAt = performance.now()

      if (outageObserved) {
        status = 'tailnet reachable; waiting for gateway'

        if (gatewayOpenedAt !== null) {
          record(startedAt, endedAt, gatewayOpenedAt)
        } else if (!panel.dataset.probeStartedAt) {
          panel.dataset.probeStartedAt = String(startedAt)
          panel.dataset.probeEndedAt = String(endedAt)
          render()
        }
      }
    } catch {
      delete panel.dataset.probeStartedAt
      delete panel.dataset.probeEndedAt
      gatewayOpenedAt = null

      if (!outageObserved) {
        outageObserved = true
      }

      status = 'offline detected; waiting for tailnet'
      render()
    } finally {
      window.clearTimeout(timeout)
    }
  }

  window.addEventListener(EVENT_NAME, event => {
    if (!active) {
      return
    }

    const state = (event as CustomEvent<GatewayStateDetail>).detail?.state

    if (state === 'closed' || state === 'error') {
      outageObserved = true
      gatewayOpenedAt = null
      delete panel.dataset.probeStartedAt
      delete panel.dataset.probeEndedAt
      status = 'gateway offline; waiting for tailnet'
      render()

      return
    }

    if (state !== 'open' || !outageObserved) {
      return
    }

    gatewayOpenedAt = performance.now()
    const startedValue = panel.dataset.probeStartedAt
    const endedValue = panel.dataset.probeEndedAt
    const startedAt = Number(startedValue)
    const endedAt = Number(endedValue)

    if (startedValue && endedValue && Number.isFinite(startedAt) && Number.isFinite(endedAt)) {
      delete panel.dataset.probeStartedAt
      delete panel.dataset.probeEndedAt
      record(startedAt, endedAt, gatewayOpenedAt)
    } else {
      status = 'gateway open; confirming tailnet reachability'
      render()
    }
  })

  window.addEventListener('load', () => {
    document.body.appendChild(panel)
    render()

    const poll = async () => {
      if (!hasLiveReconnectProbePreference()) {
        active = false
        panel.remove()

        return
      }

      await probe()
      window.setTimeout(() => void poll(), POLL_MS)
    }

    void poll()
  })
}
