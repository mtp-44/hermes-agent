import { useStore } from '@nanostores/react'
import { CheckIcon, XIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { $clientInboxItems, $clientInboxOpen, type ClientInboxItem, upsertClientInboxItem } from '@/store/client-inbox'
import { notifyError } from '@/store/notifications'
import { $selectedStoredSessionId } from '@/store/session'

async function updateItem(item: ClientInboxItem, state: { dismissed?: boolean; read?: boolean }) {
  const result = await window.hermesDesktop?.api<{ item?: ClientInboxItem }>({
    path: `/api/client-inbox/${encodeURIComponent(item.event_id)}`,
    method: 'PATCH',
    body: {
      session_id: item.session_id,
      dismissed: Boolean(state.dismissed),
      read: Boolean(state.read)
    }
  })
  if (result?.item) {
    upsertClientInboxItem(result.item)
  }
}

function InboxActions({ item }: { item: ClientInboxItem }) {
  const [busy, setBusy] = useState(false)

  if (item.action_ack) {
    return <p className="mt-2 text-xs text-(--ui-text-tertiary)">{item.action_ack}</p>
  }

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {item.actions.map(action => (
        <button
          className="rounded-full border border-(--ui-stroke-tertiary) px-2.5 py-1 text-xs text-(--ui-text-secondary) hover:bg-(--ui-sidebar-surface-background) disabled:opacity-50"
          disabled={busy || Boolean(item.acted_at)}
          key={action.callback_id}
          onClick={async () => {
            setBusy(true)
            try {
              const result = await window.hermesDesktop?.api<{ ack?: string; item?: ClientInboxItem }>({
                path: '/api/actions/dispatch',
                method: 'POST',
                body: {
                  callback_id: action.callback_id,
                  inbox_event_id: item.event_id,
                  session_id: item.session_id
                }
              })
              if (result?.item) {
                upsertClientInboxItem(result.item)
              }
            } catch (error) {
              notifyError(error, 'Could not run inbox action')
              setBusy(false)
            }
          }}
          type="button"
        >
          {action.label}
        </button>
      ))}
    </div>
  )
}

export function ClientInboxPanel() {
  const open = useStore($clientInboxOpen)
  const items = useStore($clientInboxItems)
  const selectedSessionId = useStore($selectedStoredSessionId)

  useEffect(() => {
    if (!open) {
      return
    }
    for (const item of items) {
      if (!item.read_at && !item.acted_at) {
        void updateItem(item, { read: true }).catch(error => notifyError(error, 'Could not mark inbox item as read'))
      }
    }
  }, [items, open])

  if (!open) {
    return null
  }

  return (
    <section
      aria-label="Proactive inbox"
      className="fixed right-3 top-[calc(var(--titlebar-height)+0.5rem)] z-80 flex max-h-[min(70vh,34rem)] w-[min(26rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background) shadow-2xl"
    >
      <header className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-3 py-2">
        <div>
          <h2 className="text-sm font-medium">Inbox</h2>
          <p className="text-xs text-(--ui-text-tertiary)">For this home session</p>
        </div>
        <button
          aria-label="Close inbox"
          className="rounded-md p-1 text-(--ui-text-secondary) hover:bg-(--ui-sidebar-surface-background)"
          onClick={() => $clientInboxOpen.set(false)}
          type="button"
        >
          <XIcon className="size-4" />
        </button>
      </header>

      <div className="min-h-0 overflow-y-auto p-2">
        {!selectedSessionId || items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-(--ui-text-tertiary)">
            <Codicon className="text-2xl" name="inbox" />
            <p className="text-sm">{selectedSessionId ? 'No inbox items' : 'Open a session to view its inbox'}</p>
          </div>
        ) : (
          <ol className="space-y-2">
            {items.map(item => (
              <li
                className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background) p-3"
                data-event-id={item.event_id}
                key={item.event_id}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[0.7rem] uppercase tracking-wide text-(--ui-text-tertiary)">
                      {item.kind.replaceAll('_', ' ')}
                      {item.priority !== 'normal' ? ` · ${item.priority}` : ''}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-(--ui-text-primary)">{item.body}</p>
                  </div>
                  <button
                    aria-label="Dismiss inbox item"
                    className="shrink-0 rounded-md p-1 text-(--ui-text-tertiary) hover:bg-(--ui-chat-surface-background)"
                    onClick={() =>
                      void updateItem(item, { dismissed: true }).catch(error =>
                        notifyError(error, 'Could not dismiss inbox item')
                      )
                    }
                    type="button"
                  >
                    <CheckIcon className="size-4" />
                  </button>
                </div>
                {item.actions.length > 0 && <InboxActions item={item} />}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}
