import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { SidebarRowShell } from '@/app/chat/sidebar/chrome'
import { StatusbarControls } from '@/app/shell/statusbar-controls'

const pwaCss = readFileSync(resolve(process.cwd(), 'src/pwa/pwa.css'), 'utf8')
const pwaHtml = readFileSync(resolve(process.cwd(), 'pwa.html'), 'utf8')

afterEach(cleanup)

describe('PWA mobile UX contracts', () => {
  it('marks sidebar rows as mobile-critical interaction surfaces', () => {
    render(
      <SidebarRowShell actions={<button aria-label="Session actions">•••</button>}>
        <button type="button">Open session</button>
      </SidebarRowShell>
    )

    const row = screen.getByText('Open session').closest('[data-slot="sidebar-row"]')
    expect(row).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Session actions' })).toBeTruthy()
  })

  it('enforces 44px critical targets, coarse-pointer reveal, and visible focus in the PWA stylesheet', () => {
    expect(pwaCss).toContain('--composer-control-size: 2.75rem')
    expect(pwaCss).toContain('--titlebar-control-size: 44px')
    expect(pwaCss).toContain("[data-slot='statusbar'] button")
    expect(pwaCss).toContain("[data-slot='aui_message-actions'] button")
    expect(pwaCss).toContain("[data-slot='tool-approval-actions'] button")
    expect(pwaCss).toContain("[data-slot='sidebar-row'] button")
    expect(pwaCss).toContain("[data-slot='right-rail-tabs'] button")
    expect(pwaCss).toMatch(/min-width:\s*44px\s*!important/)
    expect(pwaCss).toMatch(/min-height:\s*44px\s*!important/)
    expect(pwaCss).toMatch(/\[data-pane-reveal-trigger\]\s*\{[^}]*width:\s*44px\s*!important/s)
    expect(pwaCss).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid[^}]*!important/s)
  })

  it('keeps browser zoom available in the mobile viewport contract', () => {
    expect(pwaHtml).toContain('width=device-width, initial-scale=1.0, viewport-fit=cover')
    expect(pwaHtml).not.toMatch(/maximum-scale|user-scalable/)
  })

  it('gives icon-only status-bar actions and menus accessible names', () => {
    render(
      <MemoryRouter>
        <StatusbarControls
          leftItems={[
            {
              icon: <span aria-hidden>⌘</span>,
              id: 'command-center',
              onSelect: () => {},
              title: 'Open command center',
              variant: 'action'
            },
            {
              icon: <span aria-hidden>◎</span>,
              id: 'gateway',
              menuContent: <div>Gateway details</div>,
              title: 'Open gateway status',
              variant: 'menu'
            }
          ]}
        />
      </MemoryRouter>
    )

    expect(screen.getByRole('button', { name: 'Open command center' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open gateway status' })).toBeTruthy()
  })
})
