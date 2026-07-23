import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

import { SidebarPanelLabel } from '../shell/sidebar-label'

export function RightSidebarSectionHeader({ children, className, ...props }: ComponentProps<'div'>) {
  return (
    <div className={cn('group/project-header flex h-7 shrink-0 items-center px-2.5', className)} {...props}>
      {children}
    </div>
  )
}

// Terse pane empty state ("No files" / "No diffs"): the panel label itself —
// same uppercase/tracking + dither dot — just muted instead of theme-primary,
// centered. Shared by the file tree and review panes so both read identically.
export function PaneEmptyState({ label }: { label: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-4">
      <SidebarPanelLabel className="pl-0 text-(--ui-text-quaternary)">{label}</SidebarPanelLabel>
    </div>
  )
}
