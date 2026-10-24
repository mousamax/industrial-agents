// Loading pill and "can't reach the service" card over the map, centred in the part the sheet leaves visible.
import { CloudOff, Loader2, RotateCw, X } from 'lucide-react'
import { initialBuildingId, useApp } from '../../store'

const API_HINT = 'uv run uvicorn app.api.main:app --reload'

export function StatusOverlay() {
  const loading = useApp((s) => s.loading)
  const error = useApp((s) => s.error)
  const buildingId = useApp((s) => s.buildingId)
  const loadBuilding = useApp((s) => s.loadBuilding)
  const loadBuildings = useApp((s) => s.loadBuildings)

  if (!loading && !error) return null

  const retry = () => {
    void loadBuildings()
    void loadBuilding(buildingId ?? initialBuildingId())
  }

  return (
    <div
      className="pointer-events-none absolute inset-x-0 top-0 z-[25] flex items-center justify-center px-4"
      style={{ bottom: 'var(--sheet-offset, 0px)' }}
    >
      {error ? (
        <div
          role="alert"
          className="pointer-events-auto relative w-full max-w-sm rounded-2xl bg-surface p-5 shadow-[0_4px_24px_rgba(60,64,67,0.22)]"
        >
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => useApp.setState({ error: null })}
            className="absolute top-2 right-2 flex size-11 items-center justify-center rounded-full text-ink-soft hover:bg-map-bg"
          >
            <X size={18} />
          </button>
          <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
            <CloudOff size={20} aria-hidden />
          </div>
          <h2 className="pr-8 text-base font-semibold text-ink">Can't reach the building service</h2>
          <p className="mt-1 line-clamp-3 text-sm break-words text-ink-soft">{error}</p>
          <p className="mt-3 text-xs text-ink-soft">Is the API running? Start it from the repo root:</p>
          <code className="mt-1 block overflow-x-auto rounded-lg bg-map-bg px-3 py-2 font-mono text-xs whitespace-nowrap text-ink select-all">
            {API_HINT}
          </code>
          <button
            type="button"
            onClick={retry}
            disabled={loading}
            className="mt-4 flex h-11 w-full items-center justify-center gap-2 rounded-full bg-accent text-sm font-medium text-white hover:brightness-110 disabled:opacity-60"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <RotateCw size={16} />}
            Retry
          </button>
        </div>
      ) : (
        <div
          role="status"
          className="flex items-center gap-2 rounded-full bg-surface px-4 py-2 text-sm text-ink-soft shadow-[0_2px_8px_rgba(60,64,67,0.25)]"
        >
          <Loader2 size={16} className="animate-spin text-accent" aria-hidden />
          Loading building…
        </div>
      )}
    </div>
  )
}
