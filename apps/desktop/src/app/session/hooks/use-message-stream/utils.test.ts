import { describe, expect, it } from 'vitest'

import type { GatewayEventPayload } from '@/lib/chat-messages'

import type { ClientSessionState } from '../../../types'

import {
  completionErrorText,
  delegateTaskPayloads,
  hasSessionInfoStatePatch,
  sessionInfoStatePatch,
  settleInterruptedCompletion,
  toTodoPayload
} from './utils'

const payload = (over: Record<string, unknown>): GatewayEventPayload => over as GatewayEventPayload

describe('completionErrorText', () => {
  it('flags provider/HTTP/retry failures, ignores normal text', () => {
    expect(completionErrorText('API call failed after 3 retries: boom')).toMatch(/^API call failed/)
    expect(completionErrorText('HTTP 500 upstream')).toMatch(/^HTTP 500/)
    expect(completionErrorText('Gateway error: nope')).toMatch(/^Gateway error/)
    expect(completionErrorText('here is your answer')).toBeNull()
    expect(completionErrorText('   ')).toBeNull()
  })
})

describe('settleInterruptedCompletion', () => {
  it('cannot append or duplicate a late final message after interruption', () => {
    const state: ClientSessionState = {
      storedSessionId: 'stored-1',
      messages: [
        {
          id: 'assistant-stream',
          role: 'assistant',
          parts: [{ type: 'text', text: 'kept partial response' }],
          pending: false
        }
      ],
      branch: '',
      cwd: '',
      model: '',
      provider: '',
      reasoningEffort: '',
      serviceTier: '',
      fast: false,
      yolo: false,
      personality: '',
      busy: true,
      awaitingResponse: true,
      streamId: 'assistant-stream',
      sawAssistantPayload: true,
      pendingBranchGroup: null,
      interrupted: true,
      needsInput: false,
      turnStartedAt: 1
    }

    const once = settleInterruptedCompletion(state)
    const twice = settleInterruptedCompletion(once!)

    expect(twice?.messages).toEqual(state.messages)
    expect(twice?.messages).toHaveLength(1)
    expect(twice?.streamId).toBeNull()
    expect(twice?.busy).toBe(false)
  })
})

describe('toTodoPayload', () => {
  it('routes named todo and anonymous todos-bearing events to the todo stream', () => {
    expect(toTodoPayload(payload({ name: 'todo' }))?.tool_id).toBe('todo-live')
    expect(toTodoPayload(payload({ todos: [] }))?.name).toBe('todo')
    expect(toTodoPayload(payload({ name: 'web_search' }))).toBeUndefined()
    expect(toTodoPayload(undefined)).toBeUndefined()
  })
})

describe('sessionInfoStatePatch / hasSessionInfoStatePatch', () => {
  it('extracts only present runtime fields', () => {
    const patch = sessionInfoStatePatch(payload({ model: 'gpt', fast: true, branch: 'main' }))
    expect(patch).toMatchObject({ model: 'gpt', fast: true, branch: 'main' })
    expect(hasSessionInfoStatePatch(patch)).toBe(true)
    expect(hasSessionInfoStatePatch(sessionInfoStatePatch(payload({})))).toBe(false)
  })
})

describe('delegateTaskPayloads', () => {
  it('returns [] for non-delegate events', () => {
    expect(delegateTaskPayloads(payload({ name: 'web_search' }), 'running')).toEqual([])
  })

  it('maps a running tool.start to a subagent.start spec', () => {
    const [spec] = delegateTaskPayloads(
      payload({ name: 'delegate_task', tool_id: 't1', args: { goal: 'do it' } }),
      'running',
      'tool.start'
    )

    expect(spec).toMatchObject({ event_type: 'subagent.start', goal: 'do it', status: 'running' })
  })

  it('maps completion (with error) to a failed subagent.complete', () => {
    const [spec] = delegateTaskPayloads(
      payload({ name: 'delegate_task', error: 'boom', result: { summary: 'failed run' } }),
      'complete'
    )

    expect(spec).toMatchObject({ event_type: 'subagent.complete', status: 'failed' })
  })
})
