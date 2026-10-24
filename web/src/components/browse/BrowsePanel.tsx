// OWNER: browse agent. Browse-mode content of the bottom sheet (phones) / right sidebar (md+).
// Four states, most specific first: a selected place, a selected thing, a highlighted group (chip or agent), the
// building overview. The first ~100px of each is a one-line summary plus the main action, so 'peek' is useful.
import clsx from 'clsx'
import {
  ArrowLeft, ArrowUpDown, Box, Building2, ChevronDown, ChevronRight, CircleAlert, DoorOpen, Layers, MapPin,
  MessageCircle, Navigation, Ruler, Sparkles, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { SceneAsset, SceneSpace } from '../../api/types'
import { floorShort, spaceLabel, type SceneIndex } from '../../lib/sceneIndex'
import { useApp } from '../../store'
import {
  assetKind, categoriesFor, connectorStops, countRooms, floorName, formatArea, groupAssets, hasLift, neighboursOf,
  openingInside, plural, polygonArea, sourceLabel, spaceKind, type Category,
} from './browseLib'
import { BuildingPicker } from './BuildingPicker'
import { useBrowseUi } from './browseStore'
import { ActionButton, CategoryGlyph, IconButton } from './ui'

export function BrowsePanel() {
  const index = useApp((s) => s.index)
  const loading = useApp((s) => s.loading)
  const error = useApp((s) => s.error)
  const selection = useApp((s) => s.selection)
  const highlight = useApp((s) => s.highlight)
  const scrollRef = useRef<HTMLDivElement>(null)

  const viewKey = selection ? `${selection.kind}:${selection.id}` : highlight.ids.length ? `list:${highlight.label}:${highlight.ids.length}` : 'overview'
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [viewKey])

  let body: ReactNode
  if (!index) body = loading || !error ? <Loading /> : <ErrorNote text={error} />
  else if (selection?.kind === 'space' && index.spaceById.has(selection.id))
    body = <PlaceSheet key={viewKey} index={index} space={index.spaceById.get(selection.id)!} />
  else if (selection?.kind === 'asset' && index.assetById.has(selection.id))
    body = <ThingSheet key={viewKey} index={index} asset={index.assetById.get(selection.id)!} />
  else if (highlight.ids.length) body = <GroupSheet index={index} ids={highlight.ids} label={highlight.label} />
  else body = <Overview index={index} />

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto overscroll-contain bg-surface text-ink">
      {body}
      <BuildingPicker />
    </div>
  )
}

// ------------------------------------------------------------------ shared pieces

function useBrowseActions() {
  const select = useApp((s) => s.select)
  const setHighlight = useApp((s) => s.setHighlight)
  const setSheet = useApp((s) => s.setSheet)
  const ask = useApp((s) => s.ask)
  return {
    open: (kind: 'space' | 'asset', id: string) => {
      select({ kind, id })
      setSheet('half')
    },
    showGroup: (ids: string[], label: string) => {
      select(null)
      setHighlight(ids, label)
      setSheet('half')
    },
    ask,
  }
}

/** Title block: optional back arrow (to the group list), title, subtitle, close. */
function Header({ title, subtitle, onBack, backLabel, onClose }: {
  title: string; subtitle?: ReactNode; onBack?: () => void; backLabel?: string; onClose?: () => void
}) {
  return (
    <div className="flex items-start gap-1 pl-4 pr-2 pt-2">
      {onBack && (
        <div className="-ml-3">
          <IconButton label={backLabel ?? 'Back'} onClick={onBack}>
            <ArrowLeft className="size-5" />
          </IconButton>
        </div>
      )}
      <div className="min-w-0 flex-1 pt-1.5">
        <h2 className="line-clamp-2 text-[22px] leading-7 text-ink">{title}</h2>
        {subtitle && <div className="mt-0.5 truncate text-sm text-ink-soft">{subtitle}</div>}
      </div>
      {onClose && (
        <IconButton label="Close" onClick={onClose}>
          <span className="grid size-8 place-items-center rounded-full bg-[#f1f3f4]">
            <X className="size-5 text-ink" />
          </span>
        </IconButton>
      )}
    </div>
  )
}

function Actions({ children }: { children: ReactNode }) {
  return (
    // Wraps instead of scrolling: a half-hidden button at the panel edge reads as a layout bug.
    <div className="flex flex-wrap gap-2 px-4 pb-3 pt-3">
      {children}
    </div>
  )
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t border-line px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[15px] font-medium text-ink">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

function Chip({ onClick, children, muted }: { onClick?: () => void; children: ReactNode; muted?: boolean }) {
  const cls = clsx(
    'inline-flex h-9 max-w-full items-center gap-1.5 rounded-full border border-line px-3 text-sm',
    muted ? 'text-ink-soft' : 'text-ink',
    onClick && 'hover:bg-accent-soft active:bg-accent-soft',
  )
  return onClick ? (
    <button type="button" onClick={onClick} className={cls}>
      {children}
    </button>
  ) : (
    <span className={cls}>{children}</span>
  )
}

/** Tappable list row with icon, title, subtitle and chevron (44px+ touch target). */
function Row({ icon, title, sub, onClick, accent }: { icon: ReactNode; title: string; sub?: string; onClick: () => void; accent?: boolean }) {
  return (
    <button type="button" onClick={onClick} className="flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left hover:bg-black/[0.03] active:bg-black/[0.06]">
      <span className={clsx('grid size-9 shrink-0 place-items-center rounded-full', accent ? 'bg-accent-soft text-accent' : 'bg-[#f1f3f4] text-ink-soft')}>{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] text-ink">{title}</span>
        {sub && <span className="block truncate text-xs text-ink-soft">{sub}</span>}
      </span>
      <ChevronRight className="size-4 shrink-0 text-ink-soft" aria-hidden />
    </button>
  )
}

function Loading() {
  return (
    <div className="animate-pulse space-y-3 p-4" aria-label="Loading building">
      <div className="h-6 w-2/3 rounded bg-[#f1f3f4]" />
      <div className="h-4 w-1/2 rounded bg-[#f1f3f4]" />
      <div className="h-11 w-full rounded-full bg-[#f1f3f4]" />
    </div>
  )
}

function ErrorNote({ text }: { text: string }) {
  const openPicker = useBrowseUi((s) => s.setPickerOpen)
  return (
    <div className="space-y-3 p-4">
      <p className="flex items-start gap-2 text-sm text-ink-soft">
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-[#d93025]" /> Couldn't load this building. {text}
      </p>
      <ActionButton onClick={() => openPicker(true)} icon={<Building2 className="size-4" />}>
        Pick another building
      </ActionButton>
    </div>
  )
}

/** "Back to Coffee" when the selection was opened from a highlighted group. */
function useBackToGroup() {
  const highlight = useApp((s) => s.highlight)
  const select = useApp((s) => s.select)
  const setHighlight = useApp((s) => s.setHighlight)
  const setSheet = useApp((s) => s.setSheet)
  const inGroup = highlight.ids.length > 0
  return {
    onBack: inGroup ? () => select(null) : undefined,
    backLabel: inGroup ? `Back to ${highlight.label ?? 'results'}` : undefined,
    // Close clears selection and highlight but keeps any route the chat drew.
    onClose: () => {
      select(null)
      setHighlight([], null)
      setSheet('peek')
    },
  }
}

// ------------------------------------------------------------------ place

function PlaceSheet({ index, space }: { index: SceneIndex; space: SceneSpace }) {
  const { open, showGroup, ask } = useBrowseActions()
  const nav = useBackToGroup()
  const name = spaceLabel(space)
  const kind = spaceKind(space)
  const floor = floorName(index, space.floor_id)
  const area = formatArea(polygonArea(space.polygon))
  const assets = useMemo(() => index.assetsBySpace.get(space.id) ?? [], [index, space.id])
  const groups = useMemo(() => groupAssets(assets), [assets])
  const { rooms, outside } = useMemo(() => neighboursOf(index, space), [index, space])
  const vertical = useMemo(() => connectorStops(index, space), [index, space])
  const sameKind = useMemo(
    () => [...index.spaceById.values()].filter((s) => s.room_type === space.room_type && s.type === space.type),
    [index, space],
  )
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(groups.length === 1 ? [groups[0].type] : []))
  const toggle = (t: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })

  return (
    <div>
      <Header title={name} subtitle={[kind !== name ? kind : null, floor].filter(Boolean).join(' · ')} {...nav} />
      <Actions>
        <ActionButton primary icon={<Navigation className="size-4" />} onClick={() => ask(`How do I get to ${name}?`)}>
          Directions
        </ActionButton>
        <ActionButton icon={<MessageCircle className="size-4" />} onClick={() => ask(`Tell me about ${name}.`)}>
          Ask about this
        </ActionButton>
        {sameKind.length > 1 && (
          <ActionButton onClick={() => showGroup(sameKind.map((s) => s.id), plural(kind))}>
            All {plural(kind).toLowerCase()} · {sameKind.length}
          </ActionButton>
        )}
      </Actions>

      <div className="flex flex-wrap gap-2 px-4 pb-3">
        <Chip muted>
          <Layers className="size-4" /> {floor}
        </Chip>
        {area && (
          <Chip muted>
            <Ruler className="size-4" /> {area}
          </Chip>
        )}
        {space.ref && !name.includes(space.ref) && <Chip muted>Room {space.ref}</Chip>}
        {outside && (
          <Chip muted>
            <DoorOpen className="size-4" /> Door to outside
          </Chip>
        )}
      </div>

      {vertical.map((c) => (
        <Section key={c.connectorName} title={`${c.connectorName}${c.stepFree ? ' · step-free' : ''}`}>
          <p className="mb-2 text-xs text-ink-soft">Goes to</p>
          <div className="flex flex-wrap gap-2">
            {c.stops.map((st) => {
              const f = index.floorById.get(st.floorId)
              return (
                <Chip key={st.spaceId} onClick={() => open('space', st.spaceId)}>
                  <ArrowUpDown className="size-4 text-accent" /> {f ? f.name || floorShort(f) : st.floorId}
                </Chip>
              )
            })}
          </div>
        </Section>
      ))}

      {groups.length > 0 && (
        <Section title={`Things here · ${assets.length}`}>
          <ul className="-mx-4">
            {groups.map((g) => {
              const single = g.items.length === 1
              const isOpen = expanded.has(g.type)
              return (
                <li key={g.type}>
                  <button
                    type="button"
                    onClick={() => (single ? open('asset', g.items[0].id) : toggle(g.type))}
                    aria-expanded={single ? undefined : isOpen}
                    className="flex min-h-11 w-full items-center gap-3 px-4 py-1.5 text-left hover:bg-black/[0.03]"
                  >
                    <Box className="size-4 shrink-0 text-ink-soft" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">{single ? g.items[0].name || g.label : plural(g.label)}</span>
                    {!single && <span className="rounded-full bg-[#f1f3f4] px-2 py-0.5 text-xs text-ink-soft">{g.items.length}</span>}
                    {single ? (
                      <ChevronRight className="size-4 text-ink-soft" aria-hidden />
                    ) : (
                      <ChevronDown className={clsx('size-4 text-ink-soft transition-transform', isOpen && 'rotate-180')} aria-hidden />
                    )}
                  </button>
                  {!single && isOpen && (
                    <ul className="pb-1">
                      {g.items.map((a) => (
                        <li key={a.id}>
                          <button
                            type="button"
                            onClick={() => open('asset', a.id)}
                            className="flex min-h-11 w-full items-center gap-3 py-1.5 pl-11 pr-4 text-left text-sm text-ink-soft hover:bg-black/[0.03] hover:text-ink"
                          >
                            <span className="min-w-0 flex-1 truncate">{a.name || g.label}</span>
                            <ChevronRight className="size-4" aria-hidden />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        </Section>
      )}

      {rooms.length > 0 && (
        <Section title="Connected to">
          <div className="flex flex-wrap gap-2">
            {rooms.map((r) => (
              <Chip key={r.spaceId} onClick={() => open('space', r.spaceId)}>
                <MapPin className="size-4 text-accent" /> <span className="truncate">{r.label}</span>
              </Chip>
            ))}
          </div>
        </Section>
      )}
      <div className="h-6" />
    </div>
  )
}

// ------------------------------------------------------------------ thing

function ThingSheet({ index, asset }: { index: SceneIndex; asset: SceneAsset }) {
  const { open, showGroup, ask } = useBrowseActions()
  const nav = useBackToGroup()
  const name = asset.name || assetKind(asset)
  const kind = assetKind(asset)
  const room = index.spaceById.get(asset.space_id)
  const floor = floorName(index, asset.floor_id)
  const sameType = useMemo(() => [...index.assetById.values()].filter((a) => a.type === asset.type), [index, asset])
  const roomMates = (index.assetsBySpace.get(asset.space_id) ?? []).filter((a) => a.id !== asset.id)

  return (
    <div>
      <Header
        title={name}
        subtitle={[kind !== name ? kind : null, room ? spaceLabel(room) : null, floor].filter(Boolean).join(' · ')}
        {...nav}
      />
      <Actions>
        <ActionButton primary icon={<Navigation className="size-4" />} onClick={() => ask(`How do I get to the ${name}?`)}>
          Directions
        </ActionButton>
        <ActionButton icon={<MessageCircle className="size-4" />} onClick={() => ask(`Tell me about the ${name}.`)}>
          Ask about this
        </ActionButton>
        {sameType.length > 1 && (
          <ActionButton onClick={() => showGroup(sameType.map((a) => a.id), plural(kind))}>
            All {plural(kind).toLowerCase()} · {sameType.length}
          </ActionButton>
        )}
      </Actions>

      {room && (
        <Section title="Located in">
          <div className="-mx-4">
            <Row accent icon={<MapPin className="size-5" />} title={spaceLabel(room)} sub={[spaceKind(room), floor].join(' · ')} onClick={() => open('space', room.id)} />
          </div>
        </Section>
      )}

      {roomMates.length > 0 && room && (
        <Section title={`Also in ${spaceLabel(room)}`}>
          <div className="flex flex-wrap gap-2">
            {groupAssets(roomMates)
              .slice(0, 12)
              .map((g) => (
                <Chip key={g.type} onClick={() => open('asset', g.items[0].id)}>
                  <Box className="size-4 text-ink-soft" /> {g.items.length > 1 ? `${plural(g.label)} · ${g.items.length}` : g.label}
                </Chip>
              ))}
          </div>
        </Section>
      )}
      <div className="h-6" />
    </div>
  )
}

// ------------------------------------------------------------------ group (chip or agent highlight)

interface GroupEntry {
  key: string
  floorId: string
  title: string
  sub: string
  icon: ReactNode
  go: () => void
}

function GroupSheet({ index, ids, label }: { index: SceneIndex; ids: string[]; label: string | null }) {
  const { open } = useBrowseActions()
  const setHighlight = useApp((s) => s.setHighlight)
  const setSheet = useApp((s) => s.setSheet)
  const setFloor = useApp((s) => s.setFloor)
  const currentFloor = useApp((s) => s.floorId)

  const entries = useMemo(() => {
    const out: GroupEntry[] = []
    const seen = new Set<string>()
    for (const id of ids) {
      if (seen.has(id)) continue
      seen.add(id)
      const s = index.spaceById.get(id)
      if (s) {
        out.push({ key: id, floorId: s.floor_id, title: spaceLabel(s), sub: spaceKind(s), icon: <MapPin className="size-5" />, go: () => open('space', id) })
        continue
      }
      const a = index.assetById.get(id)
      if (a) {
        const room = index.spaceById.get(a.space_id)
        out.push({
          key: id, floorId: a.floor_id, title: a.name || assetKind(a),
          sub: [assetKind(a), room ? spaceLabel(room) : null].filter(Boolean).join(' · '),
          icon: <Box className="size-5" />, go: () => open('asset', id),
        })
        continue
      }
      const o = index.openingById.get(id)
      if (o) {
        const inside = openingInside(index, o)
        out.push({
          key: id, floorId: o.floor_id, title: inside ? `${kindTitle(o.kind)} · ${spaceLabel(inside)}` : kindTitle(o.kind),
          sub: o.step_free ? 'Step-free' : 'Has a step',
          icon: <DoorOpen className="size-5" />, go: () => (inside ? open('space', inside.id) : setFloor(o.floor_id)),
        })
        continue
      }
      const c = index.connectorById.get(id)
      if (c && c.stops.length) {
        const stop = c.stops.find(([f]) => f === currentFloor) ?? c.stops[0]
        out.push({
          key: id, floorId: stop[0], title: c.name, sub: `${kindTitle(c.kind)} · ${c.stops.length} floors`,
          icon: <ArrowUpDown className="size-5" />, go: () => open('space', stop[1]),
        })
      }
    }
    return out
  }, [ids, index, open, setFloor, currentFloor])

  const byFloor = useMemo(() => {
    const m = new Map<string, GroupEntry[]>()
    for (const e of entries) {
      const list = m.get(e.floorId)
      if (list) list.push(e)
      else m.set(e.floorId, [e])
    }
    for (const list of m.values()) list.sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true }))
    // Current floor first, then the others bottom-up.
    return index.floors.filter((f) => m.has(f.id)).sort((a, b) => Number(b.id === currentFloor) - Number(a.id === currentFloor) || a.level - b.level).map((f) => ({ floor: f, items: m.get(f.id)! }))
  }, [entries, index, currentFloor])

  const title = label ?? 'Results'
  const floorsTxt = byFloor.length > 1 ? ` on ${byFloor.length} floors` : byFloor[0] ? ` · ${byFloor[0].floor.name}` : ''

  return (
    <div>
      <Header
        title={title}
        subtitle={`${entries.length} ${entries.length === 1 ? 'result' : 'results'}${floorsTxt}`}
        onClose={() => {
          setHighlight([], null)
          setSheet('peek')
        }}
      />
      {entries.length === 0 && <p className="px-4 py-4 text-sm text-ink-soft">Nothing to list for this highlight.</p>}
      <div className="pt-2">
        {byFloor.map(({ floor, items }) => (
          <div key={floor.id}>
            <button
              type="button"
              onClick={() => setFloor(floor.id)}
              className={clsx(
                'sticky top-0 z-10 flex h-9 w-full items-center gap-2 bg-surface/95 px-4 text-left text-xs font-medium uppercase tracking-wide backdrop-blur',
                floor.id === currentFloor ? 'text-accent' : 'text-ink-soft',
              )}
            >
              <Layers className="size-3.5" /> {floor.name}
              <span className="font-normal normal-case tracking-normal">· {items.length}</span>
              {floor.id === currentFloor && <span className="ml-auto font-normal normal-case tracking-normal">on map</span>}
            </button>
            {items.map((e) => (
              <Row key={e.key} icon={e.icon} title={e.title} sub={e.sub} onClick={e.go} />
            ))}
          </div>
        ))}
      </div>
      <div className="h-6" />
    </div>
  )
}

const kindTitle = (k: string) => (k ? k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, ' ') : 'Opening')

// ------------------------------------------------------------------ overview

function Overview({ index }: { index: SceneIndex }) {
  const scene = useApp((s) => s.scene)
  const setMode = useApp((s) => s.setMode)
  const setSheet = useApp((s) => s.setSheet)
  const setFloor = useApp((s) => s.setFloor)
  const currentFloor = useApp((s) => s.floorId)
  const openPicker = useBrowseUi((s) => s.setPickerOpen)
  const { showGroup, ask } = useBrowseActions()

  const categories = categoriesFor(index)
  const rooms = countRooms(index)
  const things = index.assetById.size
  const lift = hasLift(index)
  const suggestions = useMemo(() => suggestedQuestions(index, categories), [index, categories])
  if (!scene) return <Loading />

  const nFloors = index.floors.length
  return (
    <div>
      <Header
        title={scene.name}
        subtitle={`${sourceLabel(scene.source)} · ${nFloors} ${nFloors === 1 ? 'floor' : 'floors'} · ${lift ? 'Lift' : 'No lift'}`}
      />
      <div className="flex gap-2 px-4 pb-3 pt-3">
        <ActionButton
          primary
          className="flex-1"
          icon={<Sparkles className="size-4" />}
          onClick={() => {
            setMode('chat')
            setSheet('half')
          }}
        >
          Ask the building
        </ActionButton>
        <ActionButton icon={<Building2 className="size-4" />} onClick={() => openPicker(true)}>
          Switch
        </ActionButton>
      </div>

      <div className="grid grid-cols-3 gap-2 px-4 pb-3">
        <Stat value={nFloors} label={nFloors === 1 ? 'floor' : 'floors'} />
        <Stat value={rooms} label="places" />
        <Stat value={things} label="things" />
      </div>

      {suggestions.length > 0 && (
        <Section title="Try asking">
          <div className="flex flex-wrap gap-2">
            {suggestions.map((q) => (
              <Chip key={q} onClick={() => ask(q)}>
                <MessageCircle className="size-4 text-accent" /> {q}
              </Chip>
            ))}
          </div>
        </Section>
      )}

      {categories.length > 0 && (
        <Section title="Explore">
          <div className="-mx-4">
            {categories.map((c) => (
              <Row
                key={c.key}
                accent
                icon={<CategoryGlyph icon={c.icon} className="size-5" />}
                title={c.label}
                sub={countSummary(index, c)}
                onClick={() => showGroup(c.ids, c.label)}
              />
            ))}
          </div>
        </Section>
      )}

      <Section title="Floors">
        <div className="-mx-4">
          {[...index.floors].reverse().map((f) => {
            const n = (index.spacesByFloor.get(f.id) ?? []).length
            const on = f.id === currentFloor
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => setFloor(f.id)}
                className={clsx('flex min-h-12 w-full items-center gap-3 px-4 text-left hover:bg-black/[0.03]', on && 'bg-accent-soft/60')}
              >
                <span className={clsx('grid size-9 shrink-0 place-items-center rounded-full text-sm font-medium', on ? 'bg-accent text-white' : 'bg-[#f1f3f4] text-ink-soft')}>
                  {floorShort(f)}
                </span>
                <span className="min-w-0 flex-1 truncate text-[15px] text-ink">{f.name}</span>
                <span className="text-xs text-ink-soft">{n} places</span>
              </button>
            )
          })}
        </div>
      </Section>
      <div className="h-6" />
    </div>
  )
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="rounded-xl bg-[#f8f9fa] px-3 py-2 text-center">
      <div className="text-lg font-medium text-ink">{value.toLocaleString()}</div>
      <div className="text-xs text-ink-soft">{label}</div>
    </div>
  )
}

/** "12 places · 3 things on 4 floors" for a category row. */
function countSummary(index: SceneIndex, c: Category): string {
  let places = 0
  let things = 0
  let doors = 0
  const floors = new Set<string>()
  for (const id of c.ids) {
    const s = index.spaceById.get(id)
    const a = s ? undefined : index.assetById.get(id)
    const o = s || a ? undefined : index.openingById.get(id)
    if (s) places++
    else if (a) things++
    else if (o) doors++
    const f = s?.floor_id ?? a?.floor_id ?? o?.floor_id
    if (f) floors.add(f)
  }
  const parts = [
    places ? `${places} ${places === 1 ? 'place' : 'places'}` : null,
    things ? `${things} ${things === 1 ? 'thing' : 'things'}` : null,
    doors ? `${doors} ${doors === 1 ? 'door' : 'doors'}` : null,
  ].filter(Boolean)
  return `${parts.join(' · ')}${floors.size > 1 ? ` on ${floors.size} floors` : ''}`
}

/** Two or three demo-worthy questions built from what this building actually has. */
function suggestedQuestions(index: SceneIndex, cats: Category[]): string[] {
  const has = (k: string) => cats.find((c) => c.key === k)
  const out: string[] = []
  if (has('toilets')) out.push('Where is the nearest toilet?')
  if (has('coffee')) out.push('Where can I get a coffee?')
  else if (has('kitchens')) out.push('Where is the nearest kitchen?')
  const meeting = has('meeting') ?? has('classrooms')
  const target = meeting?.ids.map((id) => index.spaceById.get(id)).find(Boolean)
  if (target && index.floors.length > 1 && hasLift(index)) out.push(`Step-free route to ${spaceLabel(target)}?`)
  else if (target) out.push(`How do I get to ${spaceLabel(target)}?`)
  if (has('machines')) out.push('Which machines are in this building?')
  return out.slice(0, 3)
}
