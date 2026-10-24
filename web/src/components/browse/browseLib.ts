// Pure helpers for browse mode: labels, area, search ranking, categories, neighbours.
// Everything is derived from the scene's own type/room_type/category strings, never from a known building.
import type { SceneAsset, SceneFloor, SceneOpening, SceneSpace } from '../../api/types'
import { spaceLabel, type SceneIndex } from '../../lib/sceneIndex'

export const OUTSIDE = 'outside'

/** "coffee_machine" -> "Coffee machine", "3d_printer" -> "3D printer". */
export function humanize(raw: string): string {
  const s = raw.replace(/[_-]+/g, ' ').trim().replace(/\s+/g, ' ').toLowerCase()
  if (!s) return ''
  const words = s.split(' ').map((w) => (/^\d+d$/.test(w) ? w.toUpperCase() : w === 'tv' || w === 'ups' ? w.toUpperCase() : w))
  const out = words.join(' ')
  return out.charAt(0).toUpperCase() + out.slice(1)
}

/** Very small English plural for headings ("Coffee machine" -> "Coffee machines"). */
export function plural(label: string, n = 2): string {
  if (n === 1) return label
  if (/(s|x|ch|sh)$/i.test(label)) return label + 'es'
  if (/[^aeiou]y$/i.test(label)) return label.slice(0, -1) + 'ies'
  return label + 's'
}

/** What kind of place a space is: the room type, or the coarse type when the room type says nothing. */
export function spaceKind(s: SceneSpace): string {
  const rt = s.room_type && !/^(generic|unknown|other|none)(_room)?$/i.test(s.room_type) ? s.room_type : s.type
  return humanize(rt || 'room')
}

export function assetKind(a: SceneAsset): string {
  return humanize(a.type || a.category || 'thing')
}

export function floorName(index: SceneIndex, floorId: string | undefined): string {
  if (!floorId) return ''
  const f = index.floorById.get(floorId)
  return f ? f.name || levelName(f) : ''
}

export function levelName(f: SceneFloor): string {
  if (f.level === 0) return 'Ground floor'
  return f.level < 0 ? `Basement ${-f.level}` : `Floor ${f.level}`
}

/** Polygon area in m² (shoelace). */
export function polygonArea(poly: [number, number][]): number {
  let sum = 0
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i]
    const [x2, y2] = poly[(i + 1) % poly.length]
    sum += x1 * y2 - x2 * y1
  }
  return Math.abs(sum) / 2
}

export function formatArea(m2: number): string {
  if (!isFinite(m2) || m2 <= 0) return ''
  return m2 >= 100 ? `${Math.round(m2)} m²` : `${m2.toFixed(1)} m²`
}

const SOURCE_LABELS: Record<string, string> = {
  osm_indoor: 'OpenStreetMap',
  swiss_dwellings: 'Swiss dwellings',
  resplan: 'ResPlan',
  procedural: 'Procedural',
  floor_plans: 'From your plans',
}
/** Dataset source -> label for the building picker; unknown sources are humanized. */
export const sourceLabel = (s: string) => SOURCE_LABELS[s] ?? humanize(s)

// ---------------------------------------------------------------- categories

export type CategoryIcon =
  | 'coffee' | 'toilet' | 'kitchen' | 'meeting' | 'entrance' | 'lift' | 'stairs' | 'machine' | 'printer'
  | 'lab' | 'class' | 'office' | 'production' | 'storage' | 'safety' | 'server' | 'bed' | 'living'
  | 'outdoor' | 'library' | 'place'

export interface Category {
  key: string
  label: string
  icon: CategoryIcon
  ids: string[] // spaces, assets and openings that belong to it
}

interface Rule {
  key: string
  label: string
  icon: CategoryIcon
  space?: RegExp // tested against room_type and type
  asset?: (a: SceneAsset) => boolean // tested against the asset
  entrances?: boolean
}

const t = (re: RegExp) => (a: SceneAsset) => re.test(a.type)

/** Ordered like Google Maps' chips: everyday needs first, specialist rooms later. */
const RULES: Rule[] = [
  { key: 'coffee', label: 'Coffee', icon: 'coffee', space: /(^|_)(cafe|cafeteria|coffee|espresso)/i, asset: t(/(coffee|espresso)(_?machine|_?maker)?$|vending/i) },
  { key: 'toilets', label: 'Toilets', icon: 'toilet', space: /toilet|wc|restroom|washroom|lavator|bathroom/i },
  { key: 'kitchens', label: 'Kitchens', icon: 'kitchen', space: /kitchen|canteen|pantry|dining|mensa|tea_?room/i },
  { key: 'meeting', label: 'Meeting rooms', icon: 'meeting', space: /meeting|conference|boardroom|huddle/i },
  { key: 'entrances', label: 'Entrances', icon: 'entrance', entrances: true },
  { key: 'lifts', label: 'Lifts', icon: 'lift', space: /(^|_)lift|elevator/i },
  { key: 'stairs', label: 'Stairs', icon: 'stairs', space: /stair/i },
  { key: 'machines', label: 'Machines', icon: 'machine', asset: (a) => /machine/i.test(a.category) && !/coffee|vending|washing/i.test(a.type) },
  { key: 'printers', label: 'Printers', icon: 'printer', asset: (a) => /printer|copier|plotter/i.test(a.type) && !/3d/i.test(a.type) },
  { key: 'labs', label: 'Labs', icon: 'lab', space: /(^|_)lab(oratory)?($|_)|makerspace|workshop/i },
  { key: 'classrooms', label: 'Lecture rooms', icon: 'class', space: /lecture|seminar|classroom|auditorium/i },
  { key: 'offices', label: 'Offices', icon: 'office', space: /office/i },
  { key: 'production', label: 'Production', icon: 'production', space: /production|assembly|additive|manufactur|fabrication/i },
  { key: 'storage', label: 'Storage', icon: 'storage', space: /storage|warehouse|store_?room|logistics|depot/i },
  { key: 'it', label: 'Server rooms', icon: 'server', space: /server|data_?cent|it_room/i },
  { key: 'library', label: 'Library', icon: 'library', space: /library|reading/i },
  { key: 'bedrooms', label: 'Bedrooms', icon: 'bed', space: /bedroom|sleep/i },
  { key: 'living', label: 'Living rooms', icon: 'living', space: /living/i },
  { key: 'outdoor', label: 'Outdoor', icon: 'outdoor', space: /outdoor|balcony|terrace|patio|garden/i },
  { key: 'safety', label: 'Safety', icon: 'safety', asset: t(/fire_ext|extinguisher|eyewash|first_aid|defibrillator|aed|emergency/i) },
]

/** Room types that are infrastructure, not destinations: never their own chip. */
const NOT_A_DESTINATION = /^(corridor|hallway|generic(_room)?|room|unknown|other|shaft|void)$/i

/** Categories with at least one match in this building, in display order. */
export function deriveCategories(index: SceneIndex): Category[] {
  const spaces = [...index.spaceById.values()]
  const assets = [...index.assetById.values()]
  const out: Category[] = []
  const coveredRoomTypes = new Set<string>()

  for (const r of RULES) {
    const ids: string[] = []
    if (r.space) {
      for (const s of spaces) {
        if (r.space.test(s.room_type) || r.space.test(s.type)) {
          ids.push(s.id)
          coveredRoomTypes.add(s.room_type)
        }
      }
    }
    if (r.asset) for (const a of assets) if (r.asset(a)) ids.push(a.id)
    if (r.entrances) {
      for (const list of index.openingsByFloor.values())
        for (const o of list) if (/entrance|exit/i.test(o.kind)) ids.push(o.id)
    }
    if (ids.length) out.push({ key: r.key, label: r.label, icon: r.icon, ids })
  }

  // Any other destination room type this building has (e.g. "Print room", "Locker room").
  const rest = new Map<string, string[]>()
  for (const s of spaces) {
    if (coveredRoomTypes.has(s.room_type) || NOT_A_DESTINATION.test(s.room_type)) continue
    const list = rest.get(s.room_type)
    if (list) list.push(s.id)
    else rest.set(s.room_type, [s.id])
  }
  for (const [rt, ids] of [...rest.entries()].sort((a, b) => b[1].length - a[1].length))
    out.push({ key: `rt:${rt}`, label: plural(humanize(rt)), icon: 'place', ids })

  return out
}

// ---------------------------------------------------------------- search

export interface SearchItem {
  kind: 'space' | 'asset'
  id: string
  title: string
  kindLabel: string
  floorId: string
  roomLabel?: string // for assets: the room they stand in
  // lowercased haystacks
  n: string
  r: string
  k: string
}

export function buildSearchItems(index: SceneIndex): SearchItem[] {
  const items: SearchItem[] = []
  for (const s of index.spaceById.values()) {
    const title = spaceLabel(s)
    const kindLabel = spaceKind(s)
    items.push({ kind: 'space', id: s.id, title, kindLabel, floorId: s.floor_id, n: title.toLowerCase(), r: (s.ref || '').toLowerCase(), k: kindLabel.toLowerCase() })
  }
  for (const a of index.assetById.values()) {
    const room = index.spaceById.get(a.space_id)
    const title = a.name || assetKind(a)
    const kindLabel = assetKind(a)
    items.push({
      kind: 'asset', id: a.id, title, kindLabel, floorId: a.floor_id,
      roomLabel: room ? spaceLabel(room) : undefined,
      n: title.toLowerCase(), r: '', k: kindLabel.toLowerCase(),
    })
  }
  return items
}

// Dots stay inside words so room refs like "1.07" match by prefix ("1.0").
const words = (s: string) => s.split(/[\s()\-_,/]+/).filter(Boolean)

/** Score one token against one item: prefix > word prefix > substring. 0 = no match. */
function tokenScore(item: SearchItem, tok: string): number {
  if (item.r && item.r === tok) return 120
  if (item.n.startsWith(tok)) return 100
  if (item.r && item.r.startsWith(tok)) return 90
  if (item.k.startsWith(tok)) return 80
  if (words(item.n).some((w) => w.startsWith(tok))) return 60
  if (words(item.k).some((w) => w.startsWith(tok))) return 50
  if (item.n.includes(tok)) return 30
  if (item.r && item.r.includes(tok)) return 25
  if (item.k.includes(tok)) return 20
  return 0
}

export interface SearchResults {
  places: SearchItem[] // all matches, best first
  things: SearchItem[]
}

export function search(items: SearchItem[], query: string): SearchResults {
  const q = query.trim().toLowerCase()
  if (!q) return { places: [], things: [] }
  const toks = q.split(/\s+/).filter(Boolean)
  const scored: { item: SearchItem; score: number }[] = []
  for (const item of items) {
    let score = 0
    let ok = true
    for (const tok of toks) {
      const s = tokenScore(item, tok)
      if (!s) {
        ok = false
        break
      }
      score += s
    }
    if (!ok) continue
    if (toks.length > 1 && item.n.startsWith(q)) score += 50
    scored.push({ item, score })
  }
  scored.sort(
    (a, b) =>
      b.score - a.score ||
      a.item.title.length - b.item.title.length ||
      a.item.title.localeCompare(b.item.title, undefined, { numeric: true }),
  )
  return {
    places: scored.filter((s) => s.item.kind === 'space').map((s) => s.item),
    things: scored.filter((s) => s.item.kind === 'asset').map((s) => s.item),
  }
}

/** Categories whose label or key matches the query (shown as suggestions above results). */
export function matchCategories(cats: Category[], query: string): Category[] {
  const q = query.trim().toLowerCase()
  if (q.length < 2) return []
  return cats.filter((c) => {
    const l = c.label.toLowerCase()
    return l.startsWith(q) || words(l).some((w) => w.startsWith(q)) || c.key.startsWith(q)
  })
}

// ---------------------------------------------------------------- neighbours

export interface Neighbour {
  spaceId: string
  label: string
  via: string // opening kind, humanized
}

/** Rooms reachable through one opening from this space, plus whether it has a door to the outside. */
export function neighboursOf(index: SceneIndex, space: SceneSpace): { rooms: Neighbour[]; outside: boolean } {
  const seen = new Map<string, Neighbour>()
  let outside = false
  for (const o of index.openingsByFloor.get(space.floor_id) ?? []) {
    if (!o.space_ids.includes(space.id)) continue
    for (const other of o.space_ids) {
      if (other === space.id) continue
      if (other === OUTSIDE || !index.spaceById.has(other)) {
        outside = true
        continue
      }
      if (!seen.has(other))
        seen.set(other, { spaceId: other, label: spaceLabel(index.spaceById.get(other)!), via: humanize(o.kind) })
    }
  }
  const rooms = [...seen.values()].sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }))
  return { rooms, outside }
}

/** Lifts/stairs serving this space: the same connector's stops on other floors. */
export function connectorStops(index: SceneIndex, space: SceneSpace) {
  const out: { connectorName: string; kind: string; stepFree: boolean; stops: { floorId: string; spaceId: string }[] }[] = []
  for (const c of index.connectorById.values()) {
    if (!c.stops.some(([, sid]) => sid === space.id)) continue
    out.push({
      connectorName: c.name,
      kind: c.kind,
      stepFree: c.step_free,
      stops: c.stops.filter(([, sid]) => sid !== space.id).map(([floorId, spaceId]) => ({ floorId, spaceId })),
    })
  }
  return out
}

/** The indoor space an opening (e.g. an entrance) leads into. */
export function openingInside(index: SceneIndex, o: SceneOpening): SceneSpace | undefined {
  for (const id of o.space_ids) {
    const s = index.spaceById.get(id)
    if (s) return s
  }
  return undefined
}

/** Group assets by type, biggest group first. */
export function groupAssets(assets: SceneAsset[]): { type: string; label: string; items: SceneAsset[] }[] {
  const m = new Map<string, SceneAsset[]>()
  for (const a of assets) {
    const list = m.get(a.type)
    if (list) list.push(a)
    else m.set(a.type, [a])
  }
  return [...m.entries()]
    .map(([type, items]) => ({ type, label: assetKind(items[0]), items }))
    .sort((a, b) => b.items.length - a.items.length || a.label.localeCompare(b.label))
}

/** Places count without corridors (what a visitor thinks of as rooms). */
export function countRooms(index: SceneIndex): number {
  let n = 0
  for (const s of index.spaceById.values()) if (!/corridor|hallway/i.test(s.type) && !/corridor|hallway/i.test(s.room_type)) n++
  return n
}

export function hasLift(index: SceneIndex): boolean {
  for (const c of index.connectorById.values()) if (/lift|elevator/i.test(c.kind)) return true
  return false
}

// ---------------------------------------------------------------- per-scene cache

const catCache = new WeakMap<SceneIndex, Category[]>()
const itemCache = new WeakMap<SceneIndex, SearchItem[]>()

export function categoriesFor(index: SceneIndex): Category[] {
  let c = catCache.get(index)
  if (!c) catCache.set(index, (c = deriveCategories(index)))
  return c
}

export function searchItemsFor(index: SceneIndex): SearchItem[] {
  let c = itemCache.get(index)
  if (!c) itemCache.set(index, (c = buildSearchItems(index)))
  return c
}
