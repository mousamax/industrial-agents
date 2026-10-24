// Directions card for the route on the map (or "No route possible"), shown above the composer.
import { Ban, ChevronDown, ChevronUp, Footprints, X } from 'lucide-react'
import { useState } from 'react'
import { useApp } from '../../store'
import { formatMetres, formatWalk, labelOf, prettifyIds } from './text'

export function RouteCard() {
  const route = useApp((s) => s.routeState.route)
  const unreachable = useApp((s) => s.routeState.unreachable)
  const index = useApp((s) => s.index)
  const clearMap = useApp((s) => s.clearMap)
  const setUnreachable = useApp((s) => s.setUnreachable)
  const [open, setOpen] = useState(false)

  if (unreachable) {
    return (
      <div className="mx-3 mb-2 flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50 p-3" role="alert">
        <Ban className="mt-0.5 size-5 shrink-0 text-red-600" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-red-700">No route possible</p>
          <p className="mt-0.5 text-sm text-ink-soft">{prettifyIds(unreachable, index)}</p>
        </div>
        <button
          type="button"
          onClick={() => setUnreachable(null)}
          className="rounded-full p-1 text-ink-soft hover:bg-red-100"
          aria-label="Dismiss"
        >
          <X className="size-4" />
        </button>
      </div>
    )
  }
  if (!route) return null

  const from = route.space_ids[0]
  const to = route.space_ids[route.space_ids.length - 1]
  const steps = route.steps ?? []
  return (
    <div className="mx-3 mb-2 rounded-xl border border-line bg-surface shadow-sm">
      <div className="flex items-center gap-3 p-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent text-white">
          <Footprints className="size-5" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold text-ink">
            {formatWalk(route.length_m)} <span className="font-normal text-ink-soft">· {formatMetres(route.length_m)}</span>
          </p>
          {from && to && (
            <p className="truncate text-xs text-ink-soft">
              {labelOf(index, from) ?? from} → {labelOf(index, to) ?? to}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={clearMap}
          className="shrink-0 rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft hover:bg-map-bg"
        >
          Clear route
        </button>
      </div>
      {steps.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="flex w-full items-center gap-1 border-t border-line px-3 py-2 text-xs font-medium text-accent"
            aria-expanded={open}
          >
            {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
            {open ? 'Hide steps' : `${steps.length} steps`}
          </button>
          {open && (
            <ol className="max-h-48 space-y-1.5 overflow-y-auto px-3 pb-3 text-sm text-ink">
              {steps.map((s, i) => (
                <li key={i} className="flex gap-2">
                  <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent">
                    {i + 1}
                  </span>
                  <span>{prettifyIds(s, index)}</span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  )
}
