import { describe, expect, it } from 'vitest'

import type { ChatMessage } from '@/lib/chat-messages'

import { attachActionsToLastCompletedAssistant, normalizeMessageActions, routeMessageActions } from './message-actions'

const assistant = (id: string, pending = false): ChatMessage => ({
  id,
  role: 'assistant',
  parts: [{ type: 'text', text: id }],
  pending
})

describe('message action routing', () => {
  it('normalizes malformed and duplicate callbacks without duplicating chips', () => {
    expect(
      normalizeMessageActions([
        { label: ' Approve ', callback_id: ' act:approve:1 ' },
        { label: 'Duplicate', callback_id: 'act:approve:1' },
        { label: '', callback_id: 'act:missing-label:1' },
        { label: 'Missing callback' }
      ])
    ).toEqual([{ label: 'Approve', callback_id: 'act:approve:1' }])
  })

  it('attaches actions only to the latest completed visible assistant message', () => {
    const messages: ChatMessage[] = [
      assistant('older'),
      { ...assistant('hidden'), hidden: true },
      { id: 'user', role: 'user', parts: [{ type: 'text', text: 'question' }] },
      assistant('latest')
    ]

    const actions = [{ label: 'Approve', callback_id: 'act:approve:1' }]
    const updated = attachActionsToLastCompletedAssistant(messages, actions)

    expect(updated.find(message => message.id === 'older')?.actions).toBeUndefined()
    expect(updated.find(message => message.id === 'hidden')?.actions).toBeUndefined()
    expect(updated.find(message => message.id === 'latest')?.actions).toEqual(actions)
  })

  it('queues out-of-order actions while the latest assistant message is pending', () => {
    const messages = [assistant('settled'), assistant('streaming', true)]
    const actions = [{ label: 'Retry', callback_id: 'act:retry:1' }]

    expect(routeMessageActions(messages, actions)).toEqual({
      messages,
      pendingMessageActions: actions
    })
  })

  it('replaces an identical action event instead of appending duplicate chips', () => {
    const actions = [{ label: 'Approve', callback_id: 'act:approve:1' }]
    const once = routeMessageActions([assistant('done')], actions).messages
    const twice = routeMessageActions(once, actions).messages

    expect(twice[0]?.actions).toEqual(actions)
    expect(twice[0]?.actions).toHaveLength(1)
  })
})
