// Map sync, pure part: tool output -> what the map should show. No store access here; chats.ts applies it.
//
// Preferred path: the tool result carries `view: ViewCommand[]` (contract in src/app/building/view.py).
// Fallback (tools that don't send `view` yet): read ids, routes and "no route" errors out of the raw output.
import type { RouteSegment, ShowRoute, ViewCommand, XY } from '../../api/types'
import { idsIn, type SceneIndex } from '../../lib/sceneIndex'
import { walkInside } from '../../lib/walkInside'

export interface ToolEffect {
  /** Commands the tool sent explicitly; when non-empty, the fallback fields below are ignored. */
  views: ViewCommand[]
  /** Route found in the output (from `view`, or built from a space path). */
  route: ShowRoute | null
  /** walk_directions steps behind `route` (text, distance, own polyline), when the output has them. */
  directions: RouteDirection[] | null
  /** Reason when the tool says no route exists. */
  unreachable: string | null
  /** Fallback ids to highlight (search hits, an edited element...). */
  highlight: string[]
  label: string | null
  /** Tool error text (not necessarily "unreachable"). */
  error: string | null
  /** Short result for the chip: "4 found", "46 m". */
  summary: string | null
}

export interface Parsed {
  data: unknown
  text: string
}

type Rec = Record<string, unknown>

const isRec = (v: unknown): v is Rec => !!v && typeof v === 'object' && !Array.isArray(v)

/** Tool outputs arrive as JSON strings (Pydantic AI tools return str); parse when possible. */
export function parseOutput(output: unknown): Parsed {
  if (typeof output === 'string') {
    try {
      return { data: JSON.parse(output), text: output }
    } catch {
      return { data: output, text: output }
    }
  }
  return { data: output, text: JSON.stringify(output ?? '') }
}

// ---- unreachable -----------------------------------------------------------------------------------------

const NO_ROUTE =
  /\bno (?:step[- ]free |possible |accessible |valid )?(?:route|path|way)\b|\bnot (?:be )?reachable\b|\bunreachable\b|\bcannot be reached\b|\bcan(?:no|')t (?:be )?reach/i

export function saysNoRoute(text: string): boolean {
  return NO_ROUTE.test(text)
}

/** The sentence that says no route exists, for the "No route possible" card. */
export function noRouteSentence(text: string): string | null {
  const sentences = text.split(/(?<=[.!?])\s+|\n+/)
  return sentences.find((s) => saysNoRoute(s))?.trim() ?? null
}

// ---- routes ----------------------------------------------------------------------------------------------

const PATH_KEYS = ['node_path', 'space_path', 'path', 'route', 'space_ids', 'spaces', 'nodes'] as const
const LENGTH_KEYS = ['length_m', 'length', 'distance_m', 'distance', 'total_m', 'total_length_m'] as const

function pathIds(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.length === 0) return null
  const ids = value.map((v) => (typeof v === 'string' ? v : isRec(v) ? (v.id ?? v.space_id) : undefined))
  return ids.every((id) => typeof id === 'string') ? (ids as string[]) : null
}

function dist(a: XY, b: XY): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

/**
 * The door between two neighbouring spaces on one floor: one the route says it used (`used`, its
 * element_path) if any, else the one closest to the straight line between anchors.
 */
export function doorBetween(index: SceneIndex, a: string, b: string, used: ReadonlySet<string> = new Set()): XY | null {
  const sa = index.spaceById.get(a)
  const sb = index.spaceById.get(b)
  if (!sa || !sb || sa.floor_id !== sb.floor_id) return null
  let best: XY | null = null
  let bestCost = Infinity
  for (const o of index.openingsByFloor.get(sa.floor_id) ?? []) {
    if (!o.space_ids.includes(a) || !o.space_ids.includes(b)) continue
    const p = o.position as XY
    const cost = (used.has(o.id) ? 0 : 1e6) + dist(sa.anchor as XY, p) + dist(p, sb.anchor as XY)
    if (cost < bestCost) {
      bestCost = cost
      best = p
    }
  }
  return best
}

function bboxOfPoly(pts: readonly (readonly [number, number])[]) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const [x, y] of pts) {
    if (x < minX) minX = x
    if (y < minY) minY = y
    if (x > maxX) maxX = x
    if (y > maxY) maxY = y
  }
  return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY }
}

/** True when a space is a rectangular pass-through corridor whose centre line routes should follow. */
function isCorridorLike(s: { polygon: readonly (readonly [number, number])[]; type: string }): boolean {
  if (s.polygon.length !== 4 || s.type === 'room') return false
  const bb = bboxOfPoly(s.polygon)
  return Math.max(bb.w, bb.h) / Math.max(0.1, Math.min(bb.w, bb.h)) >= 2.5
}

function onCorridorAxis(s: { polygon: readonly (readonly [number, number])[] }, p: XY): XY {
  const bb = bboxOfPoly(s.polygon)
  if (bb.w >= bb.h) {
    return [Math.min(bb.maxX - 0.6, Math.max(bb.minX + 0.6, p[0])), (bb.minY + bb.maxY) / 2]
  }
  return [(bb.minX + bb.maxX) / 2, Math.min(bb.maxY - 0.6, Math.max(bb.minY + 0.6, p[1]))]
}

/**
 * Per-floor polylines through the nodes in order: door positions (`door:<id>` nodes, or the door between
 * consecutive spaces, preferring the ones in `elements`, the route's element_path), and inside each space the
 * walk from where it enters to where it leaves, round corners rather than through walls; a space's anchor only
 * where the route starts, ends or takes stairs or a lift. Rectangular pass-through corridors follow their
 * centre line (door -> axis -> axis -> door). A new segment starts when the floor changes.
 */
export function segmentsFor(nodes: string[], index: SceneIndex, elements: string[] = []): RouteSegment[] {
  const used = new Set(elements)
  // Expand bare space-to-space transitions with their connecting door position when on the same floor.
  const expanded: { kind: 'space' | 'door'; id: string; floorId: string; pos: XY }[] = []
  let prevSpaceId: string | null = null
  let prevWasDoor = false
  for (const node of nodes) {
    if (node.startsWith('door:')) {
      const o = index.openingById.get(node.slice(5))
      if (o) expanded.push({ kind: 'door', id: o.id, floorId: o.floor_id, pos: o.position as XY })
      prevWasDoor = true
      continue
    }
    const space = index.spaceById.get(node)
    if (!space) continue
    if (prevSpaceId && !prevWasDoor) {
      const doorPos = doorBetween(index, prevSpaceId, node, used)
      if (doorPos) expanded.push({ kind: 'door', id: `${prevSpaceId}-${node}`, floorId: space.floor_id, pos: doorPos })
    }
    expanded.push({ kind: 'space', id: space.id, floorId: space.floor_id, pos: space.anchor as XY })
    prevSpaceId = node
    prevWasDoor = false
  }

  const segments: RouteSegment[] = []
  const push = (floor: string, p: XY) => {
    const last = segments[segments.length - 1]
    if (!last || last.floor_id !== floor) segments.push({ floor_id: floor, points: [p] })
    else last.points.push(p)
  }

  for (let i = 0; i < expanded.length; i++) {
    const item = expanded[i]
    if (item.kind === 'door') {
      push(item.floorId, item.pos)
      continue
    }
    const space = index.spaceById.get(item.id)
    const prev = expanded[i - 1]
    const next = expanded[i + 1]
    const enters = prev?.kind === 'door' && prev.floorId === item.floorId ? prev.pos : null
    const leaves = next?.kind === 'door' && next.floorId === item.floorId ? next.pos : null
    if (!space || (!enters && !leaves)) {
      push(item.floorId, item.pos)
      continue
    }
    if (enters && leaves && isCorridorLike(space)) {
      push(item.floorId, onCorridorAxis(space, enters))
      push(item.floorId, onCorridorAxis(space, leaves))
      continue
    }
    const from = enters ?? item.pos
    const to = leaves ?? item.pos
    if (!enters) push(item.floorId, from)
    for (const p of walkInside(space.polygon as XY[], from, to)) push(item.floorId, p)
    if (!leaves) push(item.floorId, to)
  }
  return segments
}

/** One spoken step of building_guide `walk_directions`; `points` is its own polyline on its space's floor. */
export interface RouteDirection {
  action: string
  text: string
  space_id: string
  distance_m: number
  landmarks: string[]
  around: string | null
  points: XY[]
}

const isXY = (v: unknown): v is XY =>
  Array.isArray(v) && v.length >= 2 && typeof v[0] === 'number' && typeof v[1] === 'number'

/** A leg's backend-drawn walk (`path`: RouteSegment[]), or null when the leg doesn't carry one or it's malformed. */
export function legPath(leg: unknown): RouteSegment[] | null {
  if (!isRec(leg) || !Array.isArray(leg.path) || !leg.path.length) return null
  const segments: RouteSegment[] = []
  for (const s of leg.path) {
    if (!isRec(s) || typeof s.floor_id !== 'string' || !Array.isArray(s.points) || !s.points.every(isXY)) return null
    segments.push({ floor_id: s.floor_id, points: s.points.map((p) => [p[0], p[1]] as XY) })
  }
  return segments
}

const SAME_POINT_M = 0.01

/**
 * Legs' paths joined in walking order. A segment continues the previous one when it is on the same floor and
 * starts where that one ended (the shared point is kept once); otherwise it starts a new segment.
 */
export function joinPaths(paths: RouteSegment[][]): RouteSegment[] {
  const out: RouteSegment[] = []
  for (const seg of paths.flat()) {
    if (!seg.points.length) continue
    const last = out[out.length - 1]
    const end = last?.points[last.points.length - 1]
    if (last && end && last.floor_id === seg.floor_id && dist(end, seg.points[0]) <= SAME_POINT_M) {
      last.points.push(...seg.points.slice(1))
    } else {
      out.push({ floor_id: seg.floor_id, points: [...seg.points] })
    }
  }
  return out
}

/** A leg's steps: `walk_directions` legs may be the step list itself, or an object with `steps`. */
function legSteps(leg: unknown): unknown[] {
  if (Array.isArray(leg)) return leg
  return isRec(leg) && Array.isArray(leg.steps) ? leg.steps : []
}

function asDirection(v: unknown): RouteDirection | null {
  if (!isRec(v) || typeof v.text !== 'string' || typeof v.space_id !== 'string') return null
  return {
    action: typeof v.action === 'string' ? v.action : '',
    text: v.text,
    space_id: v.space_id,
    distance_m: typeof v.distance_m === 'number' ? v.distance_m : 0,
    landmarks: Array.isArray(v.landmarks) ? v.landmarks.filter((x): x is string => typeof x === 'string') : [],
    around: typeof v.around === 'string' ? v.around : null,
    points: Array.isArray(v.points) ? v.points.filter(isXY).map((p) => [p[0], p[1]] as XY) : [],
  }
}

/** building_guide `walk_directions` steps (objects with text, space_id, points...) across all legs, or null. */
export function directionsFromData(data: unknown): RouteDirection[] | null {
  if (!isRec(data) || !Array.isArray(data.legs)) return null
  const steps = data.legs.flatMap(legSteps).map(asDirection)
  return steps.length && steps.every((s) => s !== null) ? (steps as RouteDirection[]) : null
}

/** Consecutive distinct space ids the steps happen in (a walk_directions leg's space path). */
function spacesOfSteps(steps: unknown[]): string[] {
  const ids: string[] = []
  for (const s of steps) {
    const id = isRec(s) && typeof s.space_id === 'string' ? s.space_id : null
    if (id && ids[ids.length - 1] !== id) ids.push(id)
  }
  return ids
}

/**
 * building_guide `route` output: {reachable, total_length_m, legs: [{length_m, steps, space_path, element_path,
 * path}]}, and `walk_directions` (same legs, steps are objects). When every leg carries `path` (the backend's
 * walk, round walls and obstacles) the map draws that; otherwise it is rebuilt here from the space path.
 */
function routeFromLegs(data: Rec, index: SceneIndex): ShowRoute | null {
  if (!Array.isArray(data.legs) || !data.legs.length) return null
  const legs = data.legs.filter((l) => isRec(l) || Array.isArray(l))
  const nodes = legs.flatMap((l, i) => {
    const p = (isRec(l) ? pathIds(l.space_path) : null) ?? spacesOfSteps(legSteps(l))
    return i ? p.slice(1) : p // each leg starts where the previous one ended
  })
  const elements = legs
    .flatMap((l) => (isRec(l) && Array.isArray(l.element_path) ? l.element_path : []))
    .filter((e): e is string => typeof e === 'string')
  const spaceIds = nodes.filter((id) => index.spaceById.has(id))
  const paths = legs.map(legPath)
  const backendPath = paths.length && paths.every((p) => p !== null) ? joinPaths(paths as RouteSegment[][]) : null
  if (spaceIds.length < (backendPath?.length ? 1 : 2)) return null
  const length =
    typeof data.total_length_m === 'number'
      ? data.total_length_m
      : legs.reduce((sum: number, l) => sum + (isRec(l) && typeof l.length_m === 'number' ? l.length_m : 0), 0)
  const directions = directionsFromData(data)
  // The route tool's steps are a skeleton for the agent; drop the door-by-door lines a walker doesn't need.
  const steps = directions
    ? directions.map((d) => d.text)
    : legs.flatMap(legSteps).filter((s): s is string => typeof s === 'string' && !/^through the /i.test(s))
  const segments = backendPath?.length ? backendPath : segmentsFor(nodes, index, elements)
  return { kind: 'route', segments, length_m: length, space_ids: spaceIds, steps }
}

/** A ShowRoute built from an output that looks like a route (ordered space path, length in metres). */
export function routeFromData(data: unknown, index: SceneIndex | null, depth = 0): ShowRoute | null {
  if (!index || !isRec(data) || depth > 2) return null
  const fromLegs = routeFromLegs(data, index)
  if (fromLegs) return fromLegs
  for (const nested of ['route', 'result']) {
    if (isRec(data[nested])) {
      const inner = routeFromData(data[nested], index, depth + 1)
      if (inner) return inner
    }
  }
  let nodes: string[] | null = null
  for (const key of PATH_KEYS) {
    const ids = pathIds(data[key])
    if (ids && ids.some((id) => index.spaceById.has(id))) {
      nodes = ids
      break
    }
  }
  if (!nodes) return null
  const spaceIds = nodes.filter((id) => index.spaceById.has(id))
  const lengthKey = LENGTH_KEYS.find((k) => typeof data[k] === 'number')
  const length = lengthKey ? (data[lengthKey] as number) : null
  if (length === null && spaceIds.length < 2) return null
  const steps = ['steps', 'directions'].map((k) => data[k]).find(Array.isArray)
  const elements = (pathIds(data.element_path) ?? []).filter((id) => index.openingById.has(id))
  return {
    kind: 'route',
    segments: segmentsFor(nodes, index, elements),
    length_m: length ?? 0,
    space_ids: spaceIds,
    steps: steps?.filter((s): s is string => typeof s === 'string') ?? [],
  }
}

// ---- view commands ---------------------------------------------------------------------------------------

const VIEW_KINDS = new Set(['highlight', 'route', 'focus_floor', 'scene_changed'])

export function viewCommands(data: unknown): ViewCommand[] {
  if (!isRec(data) || !Array.isArray(data.view)) return []
  return data.view.filter((c): c is ViewCommand => isRec(c) && VIEW_KINDS.has(c.kind as string))
}

// ---- one tool output -------------------------------------------------------------------------------------

const NO_HIGHLIGHT_TOOLS = /overview|show_route/
const MAX_HIGHLIGHT = 20

function resultIds(data: Rec, index: SceneIndex): string[] | null {
  for (const key of ['results', 'matches', 'items', 'hits']) {
    const ids = pathIds(data[key])
    if (ids) return ids.filter((id) => idsIn(index, id).length > 0)
  }
  return null
}

export function toolEffect(
  toolName: string,
  input: unknown,
  output: unknown,
  index: SceneIndex | null,
  errorText?: string,
): ToolEffect {
  const effect: ToolEffect = {
    views: [],
    route: null,
    directions: null,
    unreachable: null,
    highlight: [],
    label: isRec(input) && typeof input.query === 'string' ? input.query : null,
    error: null,
    summary: null,
  }
  if (errorText !== undefined) {
    effect.error = errorText
    if (saysNoRoute(errorText)) effect.unreachable = errorText
    return effect
  }
  const { data, text } = parseOutput(output)

  if (isRec(data) && typeof data.error === 'string') {
    effect.error = data.error
    if (saysNoRoute(data.error)) effect.unreachable = data.error
  }
  // building_guide `route`: {reachable: false, stops, note} is a normal result, not an error.
  if (isRec(data) && data.reachable === false) {
    const stops = Array.isArray(data.stops) ? data.stops.filter((s) => typeof s === 'string').join(' → ') : ''
    const note = typeof data.note === 'string' ? data.note : 'No route under these constraints.'
    effect.unreachable = stops ? `${note} (${stops})` : note
  }

  effect.views = viewCommands(data)
  if (effect.views.length) {
    effect.route = (effect.views.find((v) => v.kind === 'route') as ShowRoute | undefined) ?? null
  } else if (!effect.error && index) {
    effect.route = routeFromData(data, index)
    if (effect.route) effect.directions = directionsFromData(data)
    if (!effect.route && !NO_HIGHLIGHT_TOOLS.test(toolName)) {
      // building_guide tools put the ids to light up in `highlight`.
      const explicit = isRec(data) ? pathIds(data.highlight)?.filter((id) => idsIn(index, id).length > 0) : null
      const ids = (explicit?.length ? explicit : null) ?? ((isRec(data) && resultIds(data, index)) || idsIn(index, text))
      effect.highlight = ids.slice(0, MAX_HIGHLIGHT)
    }
  }

  if (effect.route) effect.summary = `${Math.round(effect.route.length_m * 10) / 10} m`
  else if (effect.unreachable) effect.summary = 'no route'
  else if (effect.error) effect.summary = 'no match'
  else if (isRec(data)) effect.summary = summaryOf(data)
  return effect
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const round1 = (n: number) => Math.round(n * 10) / 10

/** Chip summary for building_guide results (find, nearest, reachable_rooms, what_if, set_closed...). */
function summaryOf(data: Rec): string | null {
  if (num(data.matches) !== null) return `${data.matches} found`
  if (num(data.rooms_total) !== null || num(data.items_total) !== null) {
    const rt = num(data.rooms_total) ?? 0
    const it = num(data.items_total) ?? 0
    if (it > 0 && rt > 0) return `${it} items · ${rt} rooms`
    return `${rt + it} found`
  }
  if (Array.isArray(data.nearest)) {
    const first = data.nearest.find(isRec)
    if (first && num(first.walk_m) !== null) {
      const rm = typeof first.room === 'string' ? first.room.replace(/\s*\([^)]*\)\s*$/, '') : null
      return rm ? `${rm} · ${round1(first.walk_m as number)} m` : `${round1(first.walk_m as number)} m`
    }
    return 'none reachable'
  }
  if (num(data.reachable_count) !== null) return `${data.reachable_count} reachable`
  const shorterKey = Object.keys(data).find((k) => k.startsWith('shorter_by_more_than'))
  const longerKey = Object.keys(data).find((k) => k.startsWith('longer_by_more_than'))
  if (shorterKey) {
    const sh = num(data[shorterKey]) ?? 0
    const lg = longerKey ? (num(data[longerKey]) ?? 0) : 0
    const cut = num(data.no_longer_reachable) ?? 0
    if (cut || lg) return `${cut} cut off · ${lg} longer`
    return `${sh} shorter`
  }
  if (typeof data.status === 'string') {
    if (data.status.startsWith('proposed')) return 'preview only'
    if (data.status === 'done') return 'applied'
  }
  if (typeof data.closed === 'boolean') return data.closed ? 'closed' : 'open again'
  if (num(data.distance_m) !== null) return `${round1(data.distance_m as number)} m`
  if (num(data.total_area_m2) !== null) return `${round1(data.total_area_m2 as number)} m²`
  if (num(data.area_m2) !== null) return `${round1(data.area_m2 as number)} m²`
  if (num(data.count) !== null) return `${data.count}`
  return null
}

// ---- end of an answer: which route / highlight is the one the agent settled on ---------------------------

export interface Resolution {
  /** Index into the message's effects of the route to show, or null. */
  routeAt: number | null
  unreachable: string | null
  highlight: string[]
}

function numbersIn(text: string): number[] {
  return [...text.matchAll(/(\d+(?:\.\d+)?)\s*(?:m\b|metres|meters)/gi)].map((m) => Number(m[1]))
}

/**
 * Agents often try several routes in one turn (two lobbies, step-free and not). Pick the one whose length
 * matches the answer (the `Answer:` value first, else any "NN m" in the prose), else the last route; if the
 * last route-ish result was "no route" and nothing matched, report unreachable. Without routes, highlight the
 * search hits the final answer actually names.
 */
export function resolveMessage(
  effects: ToolEffect[],
  finalText: string,
  answer: string | null,
  index: SceneIndex | null,
): Resolution {
  const res: Resolution = { routeAt: null, unreachable: null, highlight: [] }
  const routeIdx = effects.map((e, i) => (e.route ? i : -1)).filter((i) => i >= 0)
  const lastRouteish = [...effects].reverse().find((e) => e.route || e.unreachable)
  const named = index ? namedIn(effects, finalText, index) : []

  if (routeIdx.length) {
    const candidates = answer ? [...numbersIn(answer), ...(Number.isFinite(Number(answer)) ? [Number(answer)] : [])] : []
    const numbers = candidates.length ? candidates : numbersIn(finalText)
    let best: number | null = null
    let bestGap = 0.6
    for (const n of numbers) {
      for (const i of routeIdx) {
        const gap = Math.abs(effects[i].route!.length_m - n)
        // route and walk_directions for the same walk tie; keep the one with spoken directions.
        if (gap < bestGap || (gap === bestGap && best !== null && !effects[best].directions && effects[i].directions)) {
          bestGap = gap
          best = i
        }
      }
    }
    if (best !== null) res.routeAt = best
    else if (lastRouteish?.unreachable && saysNoRoute(answer ?? finalText)) res.unreachable = lastRouteish.unreachable
    else res.routeAt = routeIdx[routeIdx.length - 1]
    if (res.routeAt !== null && index) {
      // Keep only what sits at the destination (the 3D printers in the target room), not stale search hits.
      const route = effects[res.routeAt].route!
      const dest = route.space_ids[route.space_ids.length - 1]
      res.highlight = named.filter((id) => id === dest || index.assetById.get(id)?.space_id === dest)
    }
    return res
  }

  if (lastRouteish?.unreachable) res.unreachable = lastRouteish.unreachable
  else if (effects.length && saysNoRoute(answer ?? finalText)) res.unreachable = noRouteSentence(answer ?? finalText)
  if (!res.unreachable) res.highlight = named
  return res
}

/** Ids the final answer refers to: ids written in it, else search hits whose name it mentions. */
function namedIn(effects: ToolEffect[], finalText: string, index: SceneIndex): string[] {
  const written = idsIn(index, finalText)
  if (written.length) return written
  const lower = finalText.toLowerCase()
  const candidates = [...new Set(effects.flatMap((e) => e.highlight))]
  return candidates.filter((id) => {
    const name = index.spaceById.get(id)?.name ?? index.assetById.get(id)?.name
    return !!name && lower.includes(name.toLowerCase())
  })
}
