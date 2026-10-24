// Floating map buttons (bottom-right, above the phone bottom sheet via --sheet-offset).
import { Maximize, Minus, Plus } from 'lucide-react'

export interface MapControlsProps {
  onFit: () => void
  onZoomIn: () => void
  onZoomOut: () => void
}

const btn =
  'flex h-10 w-10 items-center justify-center bg-surface text-ink-soft active:bg-accent-soft hover:text-ink transition-colors'

export function MapControls({ onFit, onZoomIn, onZoomOut }: MapControlsProps) {
  return (
    <div
      className="pointer-events-none absolute right-3 z-10 flex flex-col items-end gap-2"
      style={{ bottom: 'calc(var(--sheet-offset, 0px) + 12px)' }}
    >
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
