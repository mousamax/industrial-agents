// OWNER: browse agent. The floating search box at the top of the map: typeahead over the building's places and
// things, a building switcher, and Google-Maps-style category chips underneath.
// Layout renders it inside the map overlay; it is full width of its container (cap it at ~420px on md+).
import clsx from 'clsx'
import { ArrowLeft, Box, Building2, MapPin, Search, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useApp } from '../../store'
import {
  categoriesFor, floorName, matchCategories, search, searchItemsFor, type Category, type SearchItem,
} from './browseLib'
import { BuildingPicker } from './BuildingPicker'
import { useBrowseUi } from './browseStore'
import { CategoryGlyph } from './ui'

const MAX_PLACES = 6
const MAX_THINGS = 6

type Row =
  | { type: 'category'; cat: Category }
  | { type: 'item'; item: SearchItem }
  | { type: 'all'; ids: string[]; label: string }
  | { type: 'ask'; text: string }

export function SearchBar({ className }: { className?: string }) {
  const index = useApp((s) => s.index)
  const selection = useApp((s) => s.selection)
  const highlight = useApp((s) => s.highlight)
  const select = useApp((s) => s.select)
  const setHighlight = useApp((s) => s.setHighlight)
  const setSheet = useApp((s) => s.setSheet)
  const setMode = useApp((s) => s.setMode)
  const openPicker = useBrowseUi((s) => s.setPickerOpen)

  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const [active, setActive] = useState(0)
  const [committed, setCommitted] = useState<string | null>(null) // id whose name the pill currently shows
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const chipsRef = useRef<HTMLDivElement>(null)
  const [chipFade, setChipFade] = useState({ left: false, right: false })
  const ask = useApp((s) => s.ask)

  // Chips scroll sideways: fade whichever edge has more chips behind it, so the cut reads as "scroll", not a bug.
  const updateChipFade = () => {
    const el = chipsRef.current
    if (!el) return
    const left = el.scrollLeft > 2
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
    setChipFade((f) => (f.left === left && f.right === right ? f : { left, right }))
  }

  const categories = useMemo(() => (index ? categoriesFor(index) : []), [index])
  const items = useMemo(() => (index ? searchItemsFor(index) : []), [index])
  const results = useMemo(() => search(items, query), [items, query])
  const catMatches = useMemo(() => matchCategories(categories, query), [categories, query])

  // New building: start fresh.
  useEffect(() => {
    setQuery('')
    setCommitted(null)
  }, [index])

  // The pill shows the picked result's name until something else gets selected.
  useEffect(() => {
    if (committed && selection?.id !== committed) {
      setQuery('')
      setCommitted(null)
    }
  }, [selection, committed])

  // Keep the fades right on resize, and let a mouse wheel scroll the chip row sideways (desktop has no swipe).
  useEffect(() => {
    const el = chipsRef.current
    if (!el) return
    updateChipFade()
    const ro = new ResizeObserver(updateChipFade)
    ro.observe(el)
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || el.scrollWidth <= el.clientWidth) return
      e.preventDefault()
      el.scrollLeft += e.deltaY
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      ro.disconnect()
      el.removeEventListener('wheel', onWheel)
    }
  }, [categories, focused])

  // Close the dropdown on outside taps.
  useEffect(() => {
    if (!focused) return
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setFocused(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [focused])

  const typing = query.trim().length > 0 && !committed
  const rows: Row[] = useMemo(() => {
    if (!typing) return categories.slice(0, 8).map((cat) => ({ type: 'category' as const, cat }))
    const out: Row[] = catMatches.slice(0, 3).map((cat) => ({ type: 'category' as const, cat }))
    for (const item of results.places.slice(0, MAX_PLACES)) out.push({ type: 'item', item })
    for (const item of results.things.slice(0, MAX_THINGS)) out.push({ type: 'item', item })
    if (results.places.length > MAX_PLACES || results.things.length > MAX_THINGS)
      out.push({ type: 'all', ids: [...results.places, ...results.things].map((i) => i.id), label: `“${query.trim()}”` })
    // Anything the map can't find by name goes to the agent: it's the only row (so Enter asks) when nothing matched.
    out.push({ type: 'ask', text: query.trim() })
    return out
  }, [typing, categories, catMatches, results, query])

  useEffect(() => setActive(0), [query])

  const close = () => {
    setFocused(false)
    inputRef.current?.blur()
  }

  const pickItem = (item: SearchItem) => {
    setMode('browse')
    setHighlight([], null)
    select({ kind: item.kind, id: item.id })
    setSheet('half')
    setQuery(item.title)
    setCommitted(item.id)
    close()
  }

  const showGroup = (ids: string[], label: string) => {
    setMode('browse')
    select(null)
    setHighlight(ids, label)
    setSheet('half')
    setQuery('')
    setCommitted(null)
    close()
  }

  const toggleCategory = (c: Category) => {
    if (highlight.label === c.label && !selection) {
      setHighlight([], null)
      setSheet('peek')
    } else showGroup(c.ids, c.label)
  }

  const runRow = (row: Row) => {
    if (row.type === 'item') pickItem(row.item)
    else if (row.type === 'category') showGroup(row.cat.ids, row.cat.label)
    else if (row.type === 'all') showGroup(row.ids, row.label)
    else {
      ask(row.text)
      setQuery('')
      setCommitted(null)
      close()
    }
  }

  const clear = () => {
    setQuery('')
    if (committed) {
      select(null)
      setCommitted(null)
    }
    inputRef.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, rows.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter') {
      const row = rows[active]
      if (row) runRow(row)
    } else if (e.key === 'Escape') {
      close()
    }
  }

  const open = focused && !!index
  const activeChip = !selection && highlight.ids.length ? highlight.label : null

  return (
    <div ref={rootRef} className={clsx('pointer-events-none relative w-full', className)}>
      {/* The pill */}
      <div
        className={clsx(
          'pointer-events-auto flex h-12 items-center bg-surface pl-1 pr-1 shadow-[0_1px_2px_rgba(60,64,67,.3),0_2px_6px_2px_rgba(60,64,67,.15)]',
          open ? 'rounded-t-3xl' : 'rounded-full',
        )}
      >
        {open ? (
          <button type="button" aria-label="Close search" onClick={close} className="grid size-11 shrink-0 place-items-center rounded-full text-ink-soft hover:bg-black/5">
            <ArrowLeft className="size-5" />
          </button>
        ) : (
          <span className="grid size-11 shrink-0 place-items-center text-ink-soft">
            <Search className="size-5" aria-hidden />
          </span>
        )}
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setCommitted(null)
            setFocused(true)
          }}
          onFocus={() => setFocused(true)}
          onKeyDown={onKeyDown}
          placeholder="Search this building"
          aria-label="Search this building"
          role="combobox"
          aria-expanded={open}
          aria-controls="browse-search-results"
          enterKeyHint="search"
          autoComplete="off"
          className="h-full min-w-0 flex-1 truncate bg-transparent text-base text-ink outline-none placeholder:text-ink-soft"
        />
        {query && (
          <button type="button" aria-label="Clear search" onClick={clear} className="grid size-11 shrink-0 place-items-center rounded-full text-ink-soft hover:bg-black/5">
            <X className="size-5" />
          </button>
        )}
        <span className="mx-0.5 h-6 w-px shrink-0 bg-line" aria-hidden />
        <button
          type="button"
          aria-label="Switch building"
          title="Switch building"
          onClick={() => openPicker(true)}
          className="grid size-11 shrink-0 place-items-center rounded-full text-accent hover:bg-accent-soft"
        >
          <Building2 className="size-5" />
        </button>
      </div>

      {/* Dropdown */}
      {open && (
        <div
          id="browse-search-results"
          role="listbox"
          onPointerDown={(e) => e.preventDefault()} // keep the input focused while tapping a row
          className="pointer-events-auto absolute inset-x-0 top-12 z-30 max-h-[min(65dvh,520px)] overflow-y-auto overscroll-contain rounded-b-3xl border-t border-line bg-surface pb-2 shadow-[0_4px_6px_2px_rgba(60,64,67,.15)]"
        >
          {!typing && rows.length > 0 && <GroupLabel>Explore this building</GroupLabel>}
          {typing && rows.length === 1 && (
            <p className="px-4 pt-4 pb-1 text-sm text-ink-soft">Nothing on the map is called “{query.trim()}”.</p>
          )}
          {rows.map((row, i) => {
            const prev = rows[i - 1]
            const heading =
              typing && row.type === 'item' && (prev?.type !== 'item' || prev.item.kind !== row.item.kind)
                ? row.item.kind === 'space'
                  ? `Places · ${results.places.length}`
                  : `Things · ${results.things.length}`
                : null
            return (
              <div key={row.type === 'item' ? row.item.id : row.type === 'category' ? `c:${row.cat.key}` : row.type}>
                {heading && <GroupLabel>{heading}</GroupLabel>}
                <ResultRow row={row} active={i === active} onHover={() => setActive(i)} onClick={() => runRow(row)} floorOf={(id) => (index ? floorName(index, id) : '')} />
              </div>
            )
          })}
        </div>
      )}

      {/* Category chips */}
      {!open && categories.length > 0 && (
        <div
          ref={chipsRef}
          onScroll={updateChipFade}
          className="chip-row pointer-events-auto -mx-3 mt-2 flex gap-2 overflow-x-auto px-3 pt-0.5 pb-2 md:mx-0 md:px-1"
          data-fade-left={chipFade.left || undefined}
          data-fade-right={chipFade.right || undefined}
          style={{ scrollbarWidth: 'none' }}
        >
          {categories.map((c) => {
            const on = activeChip === c.label
            return (
              <button
                key={c.key}
                type="button"
                onClick={() => toggleCategory(c)}
                aria-pressed={on}
                className={clsx(
                  'inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3.5 text-sm shadow-[0_1px_2px_rgba(60,64,67,.3),0_1px_3px_1px_rgba(60,64,67,.15)]',
                  on ? 'bg-accent text-white' : 'bg-surface text-ink hover:bg-[#f8f9fa]',
                )}
              >
                <CategoryGlyph icon={c.icon} className={clsx('size-4', on ? 'text-white' : 'text-ink-soft')} />
                {c.label}
              </button>
            )
          })}
        </div>
      )}

      <BuildingPicker />
    </div>
  )
}

function GroupLabel({ children }: { children: ReactNode }) {
  return <div className="px-4 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-ink-soft">{children}</div>
}

function ResultRow({
  row, active, onHover, onClick, floorOf,
}: { row: Row; active: boolean; onHover: () => void; onClick: () => void; floorOf: (floorId: string) => string }) {
  let icon: ReactNode
  let title: string
  let sub: string
  if (row.type === 'category') {
    icon = <CategoryGlyph icon={row.cat.icon} className="size-5" />
    title = row.cat.label
    sub = `${row.cat.ids.length} in this building · show on map`
  } else if (row.type === 'all') {
    icon = <Search className="size-5" />
    title = `See all results for ${row.label}`
    sub = `${row.ids.length} matches`
  } else if (row.type === 'ask') {
    icon = <Sparkles className="size-5" />
    title = `Ask the building: “${row.text}”`
    sub = 'Directions, questions, anything not on the map'
  } else {
    const it = row.item
    icon = it.kind === 'space' ? <MapPin className="size-5" /> : <Box className="size-5" />
    title = it.title
    sub = [it.kindLabel !== it.title ? it.kindLabel : null, it.roomLabel, floorOf(it.floorId)].filter(Boolean).join(' · ')
  }
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onMouseEnter={onHover}
      onClick={onClick}
      className={clsx('flex min-h-12 w-full items-center gap-3 px-4 py-2 text-left', active && 'bg-black/[0.04]')}
    >
      <span className={clsx('grid size-9 shrink-0 place-items-center rounded-full', row.type === 'item' ? 'bg-[#f1f3f4] text-ink-soft' : 'bg-accent-soft text-accent')}>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] text-ink">{title}</span>
        {sub && <span className="block truncate text-xs text-ink-soft">{sub}</span>}
      </span>
    </button>
  )
}
