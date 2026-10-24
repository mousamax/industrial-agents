// Floating map buttons (bottom-right, above the phone bottom sheet via --sheet-offset).
import { Maximize, Minus, Plus } from 'lucide-react'
import { useEffect, useRef } from 'react'

export interface MapControlsProps {
  onFit: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  /** 2D/3D toggle; omitted when 3D is unavailable (no WebGL). `tilted`: the map is currently tilted. */
  tilt?: { tilted: boolean; onToggle: () => void }
}

const btn =
  'flex h-10 w-10 items-center justify-center bg-surface text-ink-soft active:bg-accent-soft hover:text-ink transition-colors'

export function MapControls({ onFit, onZoomIn, onZoomOut, tilt }: MapControlsProps) {
  // Publish this stack's height as --map-controls-h on the map's <main>, so the floor picker (a sibling) can sit
  // above it instead of overlapping it on short screens.
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    const host = el?.closest('main')
    if (!el || !host) return
    const ro = new ResizeObserver(() => host.style.setProperty('--map-controls-h', `${el.offsetHeight}px`))
    ro.observe(el)
    return () => {
      ro.disconnect()
      host.style.removeProperty('--map-controls-h')
    }
  }, [])

  return (
    <div
      ref={ref}
      className="pointer-events-none absolute right-3 z-10 flex flex-col items-end gap-2"
      style={{ bottom: 'calc(var(--sheet-offset, 0px) + 12px)' }}
    >
      {tilt && (
        <button
          type="button"
          aria-label={tilt.tilted ? 'Flat 2D view' : 'Tilted 3D view'}
          title={tilt.tilted ? 'Flat 2D view' : '3D view (or drag up with two fingers)'}
          aria-pressed={tilt.tilted}
          className={`${btn} pointer-events-auto rounded-full text-[13px] font-bold tracking-tight shadow-md`}
          onClick={tilt.onToggle}
        >
          {tilt.tilted ? '2D' : '3D'}
        </button>
      )}
      {/* Zoom buttons only where there is no pinch (desktop). */}
      <div className="pointer-events-auto hidden flex-col overflow-hidden rounded-lg shadow-md md:flex">
        <button type="button" aria-label="Zoom in" className={btn} onClick={onZoomIn}>
          <Plus size={20} />
        </button>
        <div className="h-px bg-line" />
        <button type="button" aria-label="Zoom out" className={btn} onClick={onZoomOut}>
          <Minus size={20} />
        </button>
      </div>
      <button
        type="button"
        aria-label="Show whole floor"
        title="Show whole floor"
        className={`${btn} pointer-events-auto rounded-full shadow-md`}
        onClick={onFit}
      >
        <Maximize size={20} />
      </button>
    </div>
  )
}
