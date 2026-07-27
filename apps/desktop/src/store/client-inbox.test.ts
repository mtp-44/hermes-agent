import { beforeEach, describe, expect, it } from 'vitest'

import {
  $clientInboxItems,
  $clientInboxUnread,
  clearClientInbox,
  type ClientInboxItem,
  replaceClientInbox,
  upsertClientInboxItem
} from './client-inbox'

const item = (overrides: Partial<ClientInboxItem> = {}): ClientInboxItem => ({
  actions: [],
  body: 'Brief',
  created_at: 100,
  event_id: 'event-1',
  kind: 'daily_digest',
  priority: 'normal',
  session_id: 'home',
  updated_at: 100,
  ...overrides
})

describe('client inbox store', () => {
  beforeEach(() => clearClientInbox())

  it('deduplicates delivery retries and keeps deterministic newest-first ordering', () => {
    replaceClientInbox([
      item({ event_id: 'same-a', created_at: 200 }),
      item({ event_id: 'same-b', created_at: 200 }),
      item({ event_id: 'old', created_at: 50 }),
      item({ event_id: 'same-a', created_at: 200 })
    ])

    expect($clientInboxItems.get().map(current => current.event_id)).toEqual(['same-b', 'same-a', 'old'])
  })

  it('rehydrates persisted state and updates one live item without duplicating it', () => {
    replaceClientInbox([item()])
    upsertClientInboxItem(item({ read_at: 101, updated_at: 101 }))

    expect($clientInboxItems.get()).toHaveLength(1)
    expect($clientInboxItems.get()[0].read_at).toBe(101)
    expect($clientInboxUnread.get()).toBe(0)
  })

  it('counts only live unread items', () => {
    replaceClientInbox([
      item({ event_id: 'unread' }),
      item({ event_id: 'read', read_at: 101 }),
      item({ event_id: 'acted', acted_at: 102 }),
      item({ event_id: 'dismissed', dismissed_at: 103 }),
      item({ event_id: 'expired', expires_at: 1 })
    ])

    expect($clientInboxUnread.get()).toBe(1)
    expect($clientInboxItems.get().map(current => current.event_id)).toEqual(['unread', 'read', 'acted'])
  })
})
