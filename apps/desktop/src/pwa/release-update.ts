import { notify } from '@/store/notifications'

type ReleaseMetadata = {
  release_id?: string
}

const RELEASE_METADATA_URL = '/pwa-release.json'
const POLL_INTERVAL_MS = 60_000
const NOTIFICATION_ID = 'hermes-pwa-release-update'

export function installedReleaseId(): string | null {
  return document.querySelector<HTMLMetaElement>('meta[name="hermes-pwa-build"]')?.content || null
}

export async function checkForReleaseUpdate(installed = installedReleaseId()): Promise<boolean> {
  if (!installed) {
    return false
  }
  const response = await fetch(`${RELEASE_METADATA_URL}?t=${Date.now()}`, {
    cache: 'no-store',
    credentials: 'same-origin'
  })
  if (!response.ok) {
    return false
  }
  const latest = (await response.json()) as ReleaseMetadata
  if (!latest.release_id || latest.release_id === installed) {
    return false
  }
  notify({
    id: NOTIFICATION_ID,
    kind: 'info',
    title: 'New Hermes version ready',
    message: 'Reload to switch to the latest deployed version.',
    action: {
      label: 'Reload',
      onClick: () => window.location.reload()
    },
    durationMs: 0
  })
  return true
}

export function installReleaseUpdateMonitor(): void {
  if (!installedReleaseId()) {
    return
  }
  const check = () => {
    void checkForReleaseUpdate().catch(() => undefined)
  }
  window.addEventListener('focus', check)
  window.setInterval(check, POLL_INTERVAL_MS)
}
