// Google Maps indoor level picker: vertical stack on the right edge of the map, highest level on top.
// Rendered by the layout inside the map's `relative` container. Hidden for single-floor buildings.
import { useMemo } from 'react'
import clsx from 'clsx'
import { floorShort } from '../../lib/sceneIndex'
import { useApp } from '../../store'

export function FloorPicker() {
  const index = useApp((s) => s.index)
  const floorId = useApp((s) => s.floorId)
  const route = useApp((s) => s.routeState.route)
  const setFloor = useApp((s) => s.setFloor)

  const routeFloors = useMemo(() => new Set(route?.segments.map((s) => s.floor_id) ?? []), [route])
  if (!index || index.floors.length <= 1) return null
  const floors = [...index.floors].reverse()

  return (
    <nav
      aria-label="Floors"
      className="absolute right-3 z-10 flex w-11 flex-col overflow-y-auto rounded-lg bg-surface shadow-md"
      style={{
        // Directly above the map controls (Google Maps style), never under the search bar + chips (110px);
        // scrolls when a tall building has more levels than fit.
        bottom: 'calc(var(--sheet-offset, 0px) + 12px + var(--map-controls-h, 0px) + 12px)',
        maxHeight: 'calc(100% - 118px - var(--sheet-offset, 0px) - var(--map-controls-h, 0px) - 24px)',
        scrollbarWidth: 'none',
      }}
    >
      {floors.map((f) => {
        const active = f.id === floorId
        return (
          <button
            key={f.id}
            type="button"
            title={f.name}
            aria-label={f.name}
            aria-pressed={active}
            onClick={() => setFloor(f.id)}
            className={clsx(
              'relative flex h-11 w-11 shrink-0 items-center justify-center text-sm font-semibold transition-colors',
              active ? 'bg-accent text-white' : 'text-ink-soft hover:bg-accent-soft active:bg-accent-soft',
            )}
          >
            {floorShort(f)}
            {routeFloors.has(f.id) && (
              <span
                className={clsx(
                  'absolute right-1.5 top-1.5 h-2 w-2 rounded-full ring-2',
                  active ? 'bg-white ring-accent' : 'bg-[var(--color-route)] ring-white',
                )}
              />
            )}
          </button>
        )
      })}
    </nav>
  )
}
