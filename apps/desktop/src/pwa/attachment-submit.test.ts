import { afterEach, describe, expect, it, vi } from 'vitest'

import { uploadComposerAttachment } from '@/app/session/hooks/use-prompt-actions'
import type { ComposerAttachment } from '@/store/composer'

const sessionId = '20260723_202900_7f3a00'

function stubBridge(platform: string, dataUrl = 'data:text/plain;base64,aGVsbG8=') {
  const readFileDataUrl = vi.fn(async () => dataUrl)

  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: {
      getVersion: vi.fn(async () => ({ platform })),
      readFileDataUrl
    }
  })

  return readFileDataUrl
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('PWA attachment submit routing', () => {
  it('passes an already-uploaded PWA image path directly to the gateway', async () => {
    const readFileDataUrl = stubBridge('web')

    const requestGateway = vi.fn(async (method: string) => {
      expect(method).toBe('image.attach')

      return { attached: true, path: '/session/image.png' } as never
    })

    const attachment: ComposerAttachment = {
      id: 'image:pwa',
      kind: 'image',
      label: 'image.png',
      path: '/Users/test/.hermes/pwa-uploads/image.png'
    }

    await expect(
      uploadComposerAttachment(attachment, { remote: true, requestGateway, sessionId })
    ).resolves.toMatchObject({
      attachedSessionId: sessionId,
      path: '/session/image.png'
    })
    expect(requestGateway).toHaveBeenCalledWith('image.attach', {
      path: attachment.path,
      session_id: sessionId
    })
    expect(readFileDataUrl).not.toHaveBeenCalled()
  })

  it('passes an already-uploaded PWA document path without a second byte upload', async () => {
    const readFileDataUrl = stubBridge('web')

    const requestGateway = vi.fn(async (method: string) => {
      expect(method).toBe('file.attach')

      return { attached: true, ref_text: '@file:document.txt' } as never
    })

    const attachment: ComposerAttachment = {
      id: 'file:pwa',
      kind: 'file',
      label: 'document.txt',
      path: '/Users/test/.hermes/pwa-uploads/document.txt'
    }

    await uploadComposerAttachment(attachment, { remote: true, requestGateway, sessionId })

    expect(requestGateway).toHaveBeenCalledWith('file.attach', {
      name: attachment.label,
      path: attachment.path,
      session_id: sessionId
    })
    expect(readFileDataUrl).not.toHaveBeenCalled()
  })

  it('keeps uploading client bytes for a remote Electron attachment', async () => {
    const readFileDataUrl = stubBridge('darwin')
    const requestGateway = vi.fn(async () => ({ attached: true, ref_text: '@file:document.txt' }) as never)

    const attachment: ComposerAttachment = {
      id: 'file:electron',
      kind: 'file',
      label: 'document.txt',
      path: '/Users/test/Downloads/document.txt'
    }

    await uploadComposerAttachment(attachment, { remote: true, requestGateway, sessionId })

    expect(requestGateway).toHaveBeenCalledWith('file.attach', {
      data_url: 'data:text/plain;base64,aGVsbG8=',
      name: attachment.label,
      path: attachment.path,
      session_id: sessionId
    })
    expect(readFileDataUrl).toHaveBeenCalledWith(attachment.path)
  })
})
