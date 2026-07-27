import { atom, computed } from 'nanostores'

export interface ClientInboxAction {
  callback_id: string
  label: string
}

export interface ClientInboxItem {
  action_ack?: null | string
  action_callback_id?: null | string
  acted_at?: null | number
  actions: ClientInboxAction[]
  body: string
  created_at: number
  dismissed_at?: null | number
  event_id: string
  expires_at?: null | number
  kind: string
  priority: 'high' | 'low' | 'normal' | 'urgent'
  read_at?: null | number
  reference?: null | Record<string, unknown>
  session_id: string
  updated_at: number
}

export const $clientInboxItems = atom<ClientInboxItem[]>([])
export const $clientInboxOpen = atom(false)

const visible = (item: ClientInboxItem): boolean =>
  !item.dismissed_at && (!item.expires_at || item.expires_at > Date.now() / 1000)

const ordered = (items: ClientInboxItem[]): ClientInboxItem[] =>
  [...items].filter(visible).sort((a, b) => b.created_at - a.created_at || b.event_id.localeCompare(a.event_id))

export const $clientInboxUnread = computed(
  $clientInboxItems,
  items => items.filter(item => visible(item) && !item.read_at && !item.acted_at).length
)

export function replaceClientInbox(items: ClientInboxItem[]): void {
  const unique = new Map(items.map(item => [item.event_id, item]))
  $clientInboxItems.set(ordered([...unique.values()]))
}

export function upsertClientInboxItem(item: ClientInboxItem): void {
  const unique = new Map($clientInboxItems.get().map(current => [current.event_id, current]))
  unique.set(item.event_id, item)
  $clientInboxItems.set(ordered([...unique.values()]))
}

export function clearClientInbox(): void {
  $clientInboxItems.set([])
  $clientInboxOpen.set(false)
}

export function toggleClientInbox(): void {
  $clientInboxOpen.set(!$clientInboxOpen.get())
}
