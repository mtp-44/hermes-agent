import type { ChatMessage, ChatMessageAction, GatewayEventPayload } from '@/lib/chat-messages'

export function normalizeMessageActions(actions: GatewayEventPayload['actions']): ChatMessageAction[] {
  const unique = new Map<string, ChatMessageAction>()

  for (const action of actions ?? []) {
    const label = typeof action?.label === 'string' ? action.label.trim() : ''
    const callbackId = typeof action?.callback_id === 'string' ? action.callback_id.trim() : ''

    if (label && callbackId && !unique.has(callbackId)) {
      unique.set(callbackId, { label, callback_id: callbackId })
    }
  }

  return [...unique.values()]
}

export function attachActionsToLastCompletedAssistant(
  messages: ChatMessage[],
  actions: ChatMessageAction[]
): ChatMessage[] {
  if (actions.length === 0) {
    return messages
  }

  const reverseIndex = [...messages]
    .reverse()
    .findIndex(message => message.role === 'assistant' && !message.hidden && !message.pending)

  if (reverseIndex < 0) {
    return messages
  }

  const index = messages.length - 1 - reverseIndex

  return messages.map((message, messageIndex) => (messageIndex === index ? { ...message, actions } : message))
}

export function routeMessageActions(
  messages: ChatMessage[],
  actions: ChatMessageAction[]
): { messages: ChatMessage[]; pendingMessageActions?: ChatMessageAction[] } {
  const latestVisibleAssistant = [...messages]
    .reverse()
    .find(message => message.role === 'assistant' && !message.hidden)

  if (!latestVisibleAssistant || latestVisibleAssistant.pending) {
    return { messages, pendingMessageActions: actions }
  }

  return { messages: attachActionsToLastCompletedAssistant(messages, actions) }
}
