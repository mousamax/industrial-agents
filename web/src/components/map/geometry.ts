// Per-floor map geometry, built once per (scene index, floor) and cached. Pure functions, no React.
// Coordinates: scene metres have y pointing up (matches the kit's matplotlib plans); SVG y points down,
// so every point goes through toSvg() which flips y. All bboxes here are in SVG (flipped) coordinates.
import type { SceneSpace } from '../../api/types'
import { spaceLabel, type SceneIndex } from '../../lib/sceneIndex'

export type Pt = [number, number]
export interface BBox {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

export const toSvg = (p: readonly [number, number]): Pt => [p[0], -p[1]]

export type Tone = 'room' | 'corridor' | 'hall' | 'service' | 'outdoor'
export const TONES: Tone[] = ['hall', 'corridor', 'outdoor', 'service', 'room']
export const TONE_FILL: Record<Tone, string> = {
  room: 'var(--color-map-room)',
  corridor: 'var(--color-map-corridor)',
  hall: 'var(--color-map-hall)',
  service: 'var(--color-map-service)',
  outdoor: '#e8f3ea',
}
/** Lighter tones win when a door sits between two spaces (the door gap takes the lighter fill). */
const TONE_LIGHTNESS: Record<Tone, number> = { room: 5, corridor: 4, outdoor: 3, hall: 2, service: 1 }

export type PoiKind = 'toilet' | 'food' | 'lift' | 'stairs' | 'entrance'

const SERVICE_RE = /toilet|wc|restroom|bath|lavator|shower|server|storage|technical|locker|print|stair|lift|elevator|utility|plant_room|janitor/
const FOOD_RE = /kitchen|coffee|cafe|canteen|cafeteria|pantry|dining|restaurant|bistro/
const TOILET_RE = /toilet|wc|restroom|bath|lavator/
const ENTRANCE_RE = /lobby|entrance|foyer|reception/

export function toneOf(s: Pick<SceneSpace, 'type' | 'room_type'>): Tone {
  const t = s.type.toLowerCase()
  const r = s.room_type.toLowerCase()
  if (t === 'outdoor' || /balcon|terrace|outdoor|garden/.test(r)) return 'outdoor'
  if (t === 'stair' || t === 'lift' || SERVICE_RE.test(r)) return 'service'
  if (t === 'hall' && ENTRANCE_RE.test(r)) return 'corridor'
  if (t === 'hall' || /zone|warehouse|production|logistics|workshop|hangar/.test(r)) return 'hall'
  if (t === 'corridor' || /corridor|hallway|aisle|lobby|foyer/.test(r)) return 'corridor'
  return 'room'
}

export function poiOf(s: Pick<SceneSpace, 'type' | 'room_type'>): PoiKind | null {
  const t = s.type.toLowerCase()
  const r = s.room_type.toLowerCase()
  if (t === 'lift' || /lift|elevator/.test(r)) return 'lift'
  if (t === 'stair' || /stair/.test(r)) return 'stairs'
  if (TOILET_RE.test(r)) return 'toilet'
  if (FOOD_RE.test(r)) return 'food'
  if (ENTRANCE_RE.test(r)) return 'entrance'
  return null
}

export interface SpaceGeo {
  id: string
  poly: Pt[]
  d: string
  tone: Tone
  bbox: BBox
  area: number
  anchor: Pt
  /** Label split into 1-2 lines; empty for corridors (Google does not label them). */
  lines: string[]
  /** Fallback when the full name does not fit: the room ref ("0.33", "H-A1"), if it adds anything. */
  short: string[]
  /** Usable box (m) around the anchor for fitting a label. */
  fitW: number
  fitH: number
  poi: PoiKind | null
}

export interface AssetGeo {
  id: string
  p: Pt
  category: string
  /** 1 = machines / equipment (shown from medium zoom), 2 = furniture / fixtures (close zoom only). */
  detail: 1 | 2
}

export interface FloorGeo {
  floorId: string
  spaces: SpaceGeo[]
  byId: Map<string, SpaceGeo>
  toneD: Record<Tone, string>
  allD: string
  doorD: Partial<Record<Tone, string>>
  footprintD: string
  assets: AssetGeo[]
  assetById: Map<string, AssetGeo>
  bbox: BBox | null
}

const r3 = (n: number) => Math.round(n * 1000) / 1000

export function pathOf(poly: Pt[]): string {
  if (!poly.length) return ''
  return 'M' + poly.map((p) => `${r3(p[0])},${r3(p[1])}`).join('L') + 'Z'
}

export function polyArea(poly: Pt[]): number {
  let a = 0
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) a += (poly[j][0] + poly[i][0]) * (poly[j][1] - poly[i][1])
  return Math.abs(a / 2)
}

export function bboxOf(points: Pt[]): BBox | null {
  if (!points.length) return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const [x, y] of points) {
    if (x < minX) minX = x
    if (y < minY) minY = y
    if (x > maxX) maxX = x
    if (y > maxY) maxY = y
  }
  return { minX, minY, maxX, maxY }
}

export function unionBBox(a: BBox | null, b: BBox | null): BBox | null {
  if (!a) return b
  if (!b) return a
  return {
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  }
}

export function padBBox(b: BBox, m: number): BBox {
  return { minX: b.minX - m, minY: b.minY - m, maxX: b.maxX + m, maxY: b.maxY + m }
}

export function pointInPoly(p: Pt, poly: Pt[]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]
    const [xj, yj] = poly[j]
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/** Smallest space on the floor containing the point (nested polygons: the inner room wins). */
export function spaceAt(geo: FloorGeo, p: Pt): SpaceGeo | null {
  let best: SpaceGeo | null = null
  for (const s of geo.spaces) {
    const b = s.bbox
    if (p[0] < b.minX || p[0] > b.maxX || p[1] < b.minY || p[1] > b.maxY) continue
    if ((!best || s.area < best.area) && pointInPoly(p, s.poly)) best = s
  }
  return best
}

/** Display label: drop a trailing "(apartment 3)"-style qualifier, keep it short. */
export function displayName(s: SceneSpace): string {
  return spaceLabel(s).replace(/\s*\([^)]*\)\s*$/, '').trim()
}

function splitLines(text: string): string[] {
  if (text.length <= 14) return [text]
  const mid = text.length / 2
  let best = -1
  for (let i = 0; i < text.length; i++) if (text[i] === ' ' && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i
  if (best < 0) return [text]
  return [text.slice(0, best), text.slice(best + 1)]
}

function nearestEdgeDir(p: Pt, poly: Pt[]): { dir: Pt; dist: number; len: number } | null {
  let best: { dir: Pt; dist: number; len: number } | null = null
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[j]
    const b = poly[i]
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const len = Math.hypot(dx, dy)
    if (len < 1e-6) continue
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (len * len)))
    const dist = Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
    if (!best || dist < best.dist) best = { dir: [dx / len, dy / len], dist, len }
  }
  return best
}

const cache = new WeakMap<SceneIndex, Map<string, FloorGeo>>()

export function floorGeo(index: SceneIndex, floorId: string): FloorGeo {
  let perIndex = cache.get(index)
  if (!perIndex) cache.set(index, (perIndex = new Map()))
  const hit = perIndex.get(floorId)
  if (hit) return hit
  const geo = buildFloorGeo(index, floorId)
  perIndex.set(floorId, geo)
  return geo
}

function buildFloorGeo(index: SceneIndex, floorId: string): FloorGeo {
  const spaces: SpaceGeo[] = []
  const toneParts: Record<Tone, string[]> = { room: [], corridor: [], hall: [], service: [], outdoor: [] }
  const all: string[] = []
  let bbox: BBox | null = null
  for (const s of index.spacesByFloor.get(floorId) ?? []) {
    const poly = s.polygon.map(toSvg)
    if (poly.length < 3) continue
    const d = pathOf(poly)
    const tone = toneOf(s)
    const b = bboxOf(poly)!
    const area = polyArea(poly)
    const w = b.maxX - b.minX
    const h = b.maxY - b.minY
    // Shrink the bbox by how much of it the polygon actually fills (L-shapes, slanted OSM rooms).
    const fill = Math.sqrt(Math.min(1, area / Math.max(w * h, 1e-6)))
    const isCorridor = s.type.toLowerCase() === 'corridor' && !/stair/.test(s.room_type.toLowerCase())
    spaces.push({
      id: s.id,
      poly,
      d,
      tone,
      bbox: b,
      area,
      anchor: toSvg(s.anchor),
      lines: isCorridor ? [] : splitLines(displayName(s)),
      short: isCorridor || !s.ref || s.ref === displayName(s) ? [] : [s.ref],
      fitW: w * fill,
      fitH: h * fill,
      poi: poiOf(s),
    })
    toneParts[tone].push(d)
    all.push(d)
    bbox = unionBBox(bbox, b)
  }
  const byId = new Map(spaces.map((s) => [s.id, s]))

  // Doors: a short segment along the nearest wall of an adjacent room, painted in the lighter
  // neighbour's fill so it reads as a gap in the outline.
  const doorParts: Partial<Record<Tone, string[]>> = {}
  for (const o of index.openingsByFloor.get(floorId) ?? []) {
    const p = toSvg(o.position)
    let edge: { dir: Pt; dist: number; len: number } | null = null
    let tone: Tone | null = null
    for (const sid of o.space_ids) {
      const sg = byId.get(sid)
      if (!sg) continue
      const e = nearestEdgeDir(p, sg.poly)
      if (e && (!edge || e.dist < edge.dist - 1e-3)) edge = e
      if (!tone || TONE_LIGHTNESS[sg.tone] > TONE_LIGHTNESS[tone]) tone = sg.tone
    }
    if (!edge || !tone || edge.dist > 1.5) continue
    const half = Math.min(o.width_m || 0.9, edge.len) / 2 - 0.05
    if (half <= 0) continue
    const [dx, dy] = edge.dir
    ;(doorParts[tone] ??= []).push(
      `M${r3(p[0] - dx * half)},${r3(p[1] - dy * half)}L${r3(p[0] + dx * half)},${r3(p[1] + dy * half)}`,
    )
  }
  const doorD: Partial<Record<Tone, string>> = {}
  for (const t of TONES) if (doorParts[t]) doorD[t] = doorParts[t]!.join('')

  // Assets: dots for tapping, faint footprints for context.
  const assets: AssetGeo[] = []
  const foot: string[] = []
  for (const s of index.spacesByFloor.get(floorId) ?? []) {
    for (const a of index.assetsBySpace.get(s.id) ?? []) {
      const p = toSvg(a.position)
      const cat = a.category.toLowerCase()
      assets.push({ id: a.id, p, category: cat, detail: cat === 'machine' || cat === 'equipment' ? 1 : 2 })
      const w = a.width_m || 0
      const dp = a.depth_m || 0
      if (w * dp < 0.15) continue
      const th = (-(a.rotation_deg || 0) * Math.PI) / 180 // CCW in metres = CW in flipped SVG space
      const c = Math.cos(th)
      const sn = Math.sin(th)
      const corners: Pt[] = [
        [-w / 2, -dp / 2],
        [w / 2, -dp / 2],
        [w / 2, dp / 2],
        [-w / 2, dp / 2],
      ].map(([x, y]) => [p[0] + x * c - y * sn, p[1] + x * sn + y * c])
      foot.push(pathOf(corners))
    }
  }

  const toneD = {} as Record<Tone, string>
  for (const t of TONES) toneD[t] = toneParts[t].join('')
  return {
    floorId,
    spaces,
    byId,
    toneD,
    allD: all.join(''),
    doorD,
    footprintD: foot.join(''),
    assets,
    assetById: new Map(assets.map((a) => [a.id, a])),
    bbox,
  }
}

// ---------- lower floors ----------

export interface LowerFloor {
  floorId: string
  /** All of that floor's spaces as one path (SVG coordinates). */
  d: string
  /** 0 = deepest drawn floor ... 1 = the floor directly below the current one. */
  nearness: number
}

const lowerCache = new WeakMap<SceneIndex, Map<string, LowerFloor[]>>()

/** Floors below `floorId`, lowest first, for the greyed-out context under the current floor. */
export function lowerFloors(index: SceneIndex, floorId: string): LowerFloor[] {
  let perIndex = lowerCache.get(index)
  if (!perIndex) lowerCache.set(index, (perIndex = new Map()))
  const hit = perIndex.get(floorId)
  if (hit) return hit
  const level = index.floorById.get(floorId)?.level ?? -Infinity
  const below = index.floors.filter((f) => f.level < level) // index.floors is ordered lowest first
  const out = below
    .map((f, i) => ({ floorId: f.id, d: floorGeo(index, f.id).allD, nearness: below.length > 1 ? i / (below.length - 1) : 1 }))
    .filter((f) => f.d)
  perIndex.set(floorId, out)
  return out
}

// ---------- camera ----------

/** screen = svg * k + t (CSS px inside the map container). */
export interface Camera {
  k: number
  tx: number
  ty: number
}

export interface Insets {
  top: number
  right: number
  bottom: number
  left: number
}

export function fitCamera(b: BBox, w: number, h: number, ins: Insets, maxK: number): Camera {
  const aw = Math.max(w - ins.left - ins.right, 40)
  const ah = Math.max(h - ins.top - ins.bottom, 40)
  const bw = Math.max(b.maxX - b.minX, 0.5)
  const bh = Math.max(b.maxY - b.minY, 0.5)
  const k = Math.min(aw / bw, ah / bh, maxK)
  const cx = (b.minX + b.maxX) / 2
  const cy = (b.minY + b.maxY) / 2
  return { k, tx: ins.left + aw / 2 - cx * k, ty: ins.top + ah / 2 - cy * k }
}
