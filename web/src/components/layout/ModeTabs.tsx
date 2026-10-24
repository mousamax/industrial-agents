// Explore / Ask tab bar at the top of the sheet (phone) or sidebar (split view).
import clsx from 'clsx'
import { Compass, Sparkles } from 'lucide-react'
import { useApp, type Mode } from '../../store'

const TABS: { mode: Mode; label: string; Icon: typeof Compass }[] = [
  { mode: 'browse', label: 'Explore', Icon: Compass },
  { mode: 'chat', label: 'Ask', Icon: Sparkles },
]

export function ModeTabs() {
  const mode = useApp((s) => s.mode)
  const setMode = useApp((s) => s.setMode)
  return (
    <div role="tablist" aria-label="Panel" className="flex border-b border-line px-2">
      {TABS.map(({ mode: m, label, Icon }) => {
        const active = mode === m
        return (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => setMode(m)}
            className={clsx(
              'relative flex h-12 flex-1 items-center justify-center gap-2 text-sm font-medium transition-colors',
              'outline-none focus-visible:bg-accent-soft',
              active ? 'text-accent' : 'text-ink-soft hover:text-ink',
            )}
          >
            <Icon size={18} strokeWidth={active ? 2.4 : 2} aria-hidden />
            {label}
            <span
              aria-hidden
              className={clsx(
                'absolute inset-x-6 bottom-0 h-[3px] rounded-t-full bg-accent transition-opacity',
                active ? 'opacity-100' : 'opacity-0',
              )}
            />
          </button>
        )
      })}
    </div>
  )
}
