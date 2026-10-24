// One-line summary shown in the peeking sheet: building, selected place, or an invitation to ask.
import { ChevronUp } from 'lucide-react'
import { useApp } from '../../store'

function useSummary(): { title: string; sub: string | null } {
  const mode = useApp((s) => s.mode)
  const scene = useApp((s) => s.scene)
  const index = useApp((s) => s.index)
  const loading = useApp((s) => s.loading)
  const selection = useApp((s) => s.selection)
  const highlight = useApp((s) => s.highlight)
  const route = useApp((s) => s.routeState.route)

  if (loading && !scene) return { title: 'Loading building…', sub: null }
  if (!scene || !index) return { title: 'No building loaded', sub: null }
  const floors = `${index.floors.length} ${index.floors.length === 1 ? 'floor' : 'floors'}`

  if (mode === 'chat') return { title: 'Ask about this building', sub: scene.name }
  if (selection) {
    const item = selection.kind === 'space' ? index.spaceById.get(selection.id) : index.assetById.get(selection.id)
    const name = item ? item.name || selection.id : selection.id
    const floor = item ? index.floorById.get(item.floor_id)?.name : undefined
    return { title: name, sub: floor ?? null }
  }
  if (route) return { title: 'Route', sub: scene.name }
  if (highlight.label) return { title: highlight.label, sub: `${highlight.ids.length} on the map` }
  return { title: scene.name, sub: floors }
}

export function SheetSummary({ onOpen }: { onOpen: () => void }) {
  const { title, sub } = useSummary()
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex min-h-11 w-full items-center gap-3 px-4 text-left outline-none focus-visible:bg-accent-soft"
    >
      <span className="min-w-0 flex-1 truncate">
        <span className="font-medium text-ink">{title}</span>
        {sub && <span className="text-ink-soft"> · {sub}</span>}
      </span>
      <ChevronUp size={18} className="shrink-0 text-ink-soft" aria-hidden />
    </button>
  )
}
