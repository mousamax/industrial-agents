// Modal list of every building the API knows: search, filter by source / lift, tap to load. On top: add a new
// building from floor plans (Level 4); the guide then walks you through what the plans don't say.
// Phones: a full-height sheet sliding up; md+: a centered dialog. Rendered into document.body.
import clsx from 'clsx'
import { ArrowUpDown, Building2, Check, FileUp, Loader2, Search, X } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { uploadPlans } from '../../api/client'
import { useApp } from '../../store'
import { sourceLabel } from './browseLib'
import { useBrowseUi } from './browseStore'
import { IconButton } from './ui'

/** Renders the picker when it is open (useBrowseUi.pickerOpen). SearchBar and BrowsePanel both mount one;
 *  only the first mounted instance renders, so it works whichever of them the layout shows. */
export function BuildingPicker() {
  const hostId = useId()
  const open = useBrowseUi((s) => s.pickerOpen)
  const setOpen = useBrowseUi((s) => s.setPickerOpen)
  const isHost = useBrowseUi((s) => s.pickerHosts[0] === hostId)
  const claim = useBrowseUi((s) => s.claimPickerHost)
  const release = useBrowseUi((s) => s.releasePickerHost)
  useEffect(() => {
    claim(hostId)
    return () => release(hostId)
  }, [hostId, claim, release])
  if (!open || !isHost) return null
  return createPortal(<PickerDialog onClose={() => setOpen(false)} />, document.body)
}

function PickerDialog({ onClose }: { onClose: () => void }) {
  const buildings = useApp((s) => s.buildings)
  const currentId = useApp((s) => s.buildingId)
  const loadBuilding = useApp((s) => s.loadBuilding)
  const loadBuildings = useApp((s) => s.loadBuildings)
  const setSheet = useApp((s) => s.setSheet)
  const [query, setQuery] = useState('')
  const [source, setSource] = useState<string | null>(null)
  const [liftOnly, setLiftOnly] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const titleId = useId()

  useEffect(() => {
    if (!buildings.length) void loadBuildings()
  }, [buildings.length, loadBuildings])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    // Autofocus on wide screens only: on phones the keyboard would cover the list.
    if (window.matchMedia('(min-width: 768px)').matches) inputRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const sources = useMemo(() => {
    const c = new Map<string, number>()
    for (const b of buildings) c.set(b.source, (c.get(b.source) ?? 0) + 1)
    return [...c.entries()].sort((a, b) => b[1] - a[1])
  }, [buildings])

  const list = useMemo(() => {
    const q = query.trim().toLowerCase()
    const out = buildings.filter(
      (b) =>
        (!source || b.source === source) &&
        (!liftOnly || b.has_lift) &&
        (!q || b.name.toLowerCase().includes(q) || b.id.toLowerCase().includes(q)),
    )
    // Current building first, then bigger buildings (more to explore) first.
    return out.sort((a, b) => Number(b.id === currentId) - Number(a.id === currentId) || b.floors - a.floors || a.name.localeCompare(b.name, undefined, { numeric: true }))
  }, [buildings, query, source, liftOnly, currentId])

  const pick = (id: string) => {
    onClose()
    if (useApp.getState().mode === 'browse') setSheet('peek')
    if (id !== currentId) void loadBuilding(id)
  }

  return (
    <div className="fixed inset-0 z-[1000] flex items-end justify-center md:items-center" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative flex h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl bg-surface shadow-2xl md:h-[80vh] md:max-w-lg md:rounded-2xl">
        <div className="flex items-center gap-1 px-2 pt-2">
          <h2 id={titleId} className="flex-1 px-2 text-lg font-medium text-ink">
            Choose a building
          </h2>
          <IconButton label="Close" onClick={onClose}>
            <X className="size-5" />
          </IconButton>
        </div>

        <div className="px-4 pb-2">
          <label className="flex h-11 items-center gap-2 rounded-full border border-line bg-surface px-4 focus-within:border-accent focus-within:ring-1 focus-within:ring-accent">
            <Search className="size-4 text-ink-soft" aria-hidden />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search buildings"
              className="min-w-0 flex-1 bg-transparent text-base text-ink outline-none placeholder:text-ink-soft"
              enterKeyHint="search"
            />
            {query && (
              <button type="button" aria-label="Clear" onClick={() => setQuery('')} className="text-ink-soft">
                <X className="size-4" />
              </button>
            )}
          </label>
        </div>

        <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 pb-3" style={{ scrollbarWidth: 'none' }}>
          <FilterChip active={!source} onClick={() => setSource(null)}>
            All {buildings.length ? `· ${buildings.length}` : ''}
          </FilterChip>
          {sources.map(([s, n]) => (
            <FilterChip key={s} active={source === s} onClick={() => setSource(source === s ? null : s)}>
              {sourceLabel(s)} · {n}
            </FilterChip>
          ))}
          <FilterChip active={liftOnly} onClick={() => setLiftOnly(!liftOnly)}>
            <ArrowUpDown className="size-3.5" aria-hidden /> Has lift
          </FilterChip>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain border-t border-line">
          <NewFromPlans onDone={onClose} />
          {!buildings.length && <p className="p-6 text-center text-sm text-ink-soft">Loading buildings…</p>}
          {buildings.length > 0 && !list.length && <p className="p-6 text-center text-sm text-ink-soft">No building matches.</p>}
          <ul>
            {list.map((b) => {
              const current = b.id === currentId
              return (
                <li key={b.id}>
                  <button
                    type="button"
                    onClick={() => pick(b.id)}
                    className={clsx('flex min-h-14 w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-black/[0.03]', current && 'bg-accent-soft/60')}
                  >
                    <span className={clsx('grid size-10 shrink-0 place-items-center rounded-full', current ? 'bg-accent text-white' : 'bg-accent-soft text-accent')}>
                      <Building2 className="size-5" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] text-ink">{b.name}</span>
                      <span className="block truncate text-xs text-ink-soft">
                        {sourceLabel(b.source)} · {b.floors} {b.floors === 1 ? 'floor' : 'floors'} · {b.has_lift ? 'Lift' : 'No lift'}
                      </span>
                    </span>
                    {current && <Check className="size-5 shrink-0 text-accent" aria-label="Current building" />}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
    </div>
  )
}

/** "Add a building from floor plans": pick one plan per floor, name it, and the guide opens on it, asking about
 *  what the plans left open. Drafting takes seconds (POST /buildings/draft). */
function NewFromPlans({ onDone }: { onDone: () => void }) {
  const loadBuildings = useApp((s) => s.loadBuildings)
  const loadBuilding = useApp((s) => s.loadBuilding)
  const ask = useApp((s) => s.ask)
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const build = async () => {
    if (!files.length || busy) return
    setBusy(true)
    setError(null)
    try {
      const label = name.trim() || 'New building'
      const drafted = await uploadPlans(files, label)
      await loadBuildings()
      await loadBuilding(drafted.building_id)
      onDone()
      ask(`I just uploaded the floor plans of ${drafted.name}. What do you need from me?`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex min-h-14 w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-black/[0.03]"
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-white">
          <FileUp className="size-5" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-medium text-ink">Add a building from floor plans</span>
          <span className="block truncate text-xs text-ink-soft">One plan per floor · ready in seconds, then we talk it through</span>
        </span>
      </button>
    )
  }

  return (
    <div className="m-3 rounded-2xl bg-accent-soft/60 p-4">
      <p className="text-[15px] font-medium text-ink">New building from floor plans</p>
      <p className="mt-0.5 text-xs text-ink-soft">
        SVG or PNG, one per floor, named like floor_0.svg, floor_1.svg. I&rsquo;ll draft it and ask about anything the plans don&rsquo;t say.
      </p>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="What do you call it? e.g. Hall 7"
        className="mt-3 h-11 w-full rounded-full bg-surface px-4 text-base text-ink outline-none placeholder:text-ink-soft focus:ring-1 focus:ring-accent"
      />
      <input
        ref={fileRef}
        type="file"
        multiple
        accept=".svg,.png,image/svg+xml,image/png"
        className="hidden"
        onChange={(e) => setFiles([...(e.target.files ?? [])])}
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        className="mt-2 flex h-11 w-full items-center gap-2 rounded-full bg-surface px-4 text-left text-sm text-ink"
      >
        <FileUp className="size-4 shrink-0 text-accent" aria-hidden />
        <span className="truncate">{files.length ? files.map((f) => f.name).join(', ') : 'Choose floor plans'}</span>
      </button>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" onClick={() => setOpen(false)} className="h-10 rounded-full px-4 text-sm text-ink-soft">
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void build()}
          disabled={!files.length || busy}
          className="inline-flex h-10 items-center gap-2 rounded-full bg-accent px-5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {busy ? 'Drafting…' : 'Build it'}
        </button>
      </div>
    </div>
  )
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        'inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3.5 text-sm',
        active ? 'border-transparent bg-accent-soft font-medium text-accent' : 'border-line bg-surface text-ink',
      )}
    >
      {children}
    </button>
  )
}
