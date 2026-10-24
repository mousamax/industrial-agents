// Deterministic client-side building engine ported from Claude Design's building.js & scenarios.js,
// operating directly on our typed SceneIndex (GET /buildings/{id}/scene).
// Computes:
// 1. Followable walk steps (with corridor centre-line left/right turn detection) for any route
// 2. Shared wall midpoint & orientation for proposed doors (ghostDoor)
// 3. Kit-model routing & reachability for instant option distances, impact previews, and pitch scenarios
import type { RouteSegment, SceneOpening, SceneSpace, ShowRoute, XY } from '../api/types'
import { spaceLabel, type SceneIndex } from './sceneIndex'

export type StepIcon =
  | 'circle-dot'
  | 'arrow-right'
  | 'arrow-up'
  | 'arrow-down'
  | 'corner-up-left'
  | 'corner-up-right'
  | 'door-open'
  | 'flag'

export interface WalkStep {
  icon: StepIcon
  text: string
  d: number
  floorId: string
  spaceId: string
  connector?: string
  kind?: string
  outdoor?: boolean
}

export interface ComputedRoute {
  start: string
  goal: string
  nodes: string[]
  length: number
  spaces: string[]
  connectors: string[]
  doors: string[]
  segments: RouteSegment[]
  floors: string[]
  steps: WalkStep[]
  showRoute: ShowRoute
}

export interface RouteOptions {
  indoor?: boolean
  stepFree?: boolean
  minWidth?: number
  closed?: Set<string>
  noLifts?: boolean
  via?: string[]
  extraOpenings?: SceneOpening[]
}

export interface SharedWall {
  len: number
  pos: XY
  vertical: boolean
}

export interface TraceItem {
  tool: string
  args: string
  result: string
  running?: boolean
}

export interface MemoryChip {
  icon: 'map-pin' | 'accessibility' | 'forklift' | 'volume-2'
  text: string
  spaceId?: string
  stepFree?: boolean
  minWidth?: number
  voice?: boolean
}

export interface AskOption {
  label: string
  sub: string
  disabled?: boolean
  replyText: string
  post?: TraceItem[]
  result?: ScenarioResult
}

export interface ScenarioAsk {
  q: string
  why: string
  options: AskOption[]
  scan?: boolean
}

export interface ScenarioResult {
  kind: 'route' | 'impact' | 'edit'
  buildingId: string
  buildingName: string
  title: string
  sub: string
  understood: string
  floors: string[]
  focusFloor: string
  route?: ComputedRoute
  steps?: WalkStep[]
  say?: string
  followUp?: string
  followLabel?: string
  assetIds?: string[]
  focusIds?: string[] | null
  closed?: string[]
  affected?: string[]
  ghostDoor?: { floorId: string; pos: XY; vertical: boolean }
  stats?: { n: string; label: string }[]
  note?: string
  confirm?: string
  applied?: string
  appliedTrace?: TraceItem
  undo?: string
  remember?: MemoryChip[]
}

export interface Scenario {
  id: string
  buildingId: string
  buildingName: string
  persona: string
  icon: 'coffee' | 'accessibility' | 'wrench' | 'forklift' | 'triangle-alert' | 'door-open'
  chip: string
  user: string
  voice?: boolean
  pre: TraceItem[]
  ask?: ScenarioAsk
  result?: ScenarioResult
}

const dist = (a: readonly [number, number], b: readonly [number, number]) => Math.hypot(a[0] - b[0], a[1] - b[1])

export const fmtM = (m: number): string => `${Math.round(m)} m`
export const fmtWalk = (m: number, speed = 1.2): string => {
  const s = m / speed
  return s < 60 ? `${Math.max(5, Math.round(s / 5) * 5)} s` : `${Math.round(s / 60)} min`
}

function bboxOf(pts: readonly (readonly [number, number])[]) {
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

export const floorNameOf = (index: SceneIndex, fid: string): string => index.floorById.get(fid)?.name ?? ''

export function findSpaces(index: SceneIndex, re: RegExp): SceneSpace[] {
  return [...index.spaceById.values()].filter((s) => re.test(s.name) || re.test(s.room_type) || re.test(s.ref))
}

export function findAssets(index: SceneIndex, re: RegExp) {
  return [...index.assetById.values()].filter((a) => re.test(a.name) || re.test(a.type))
}

export function spaceByName(index: SceneIndex, name: string): SceneSpace | undefined {
  const clean = name.replace(/\s*\([^)]*\)\s*$/, '').trim().toLowerCase()
  return [...index.spaceById.values()].find(
    (s) =>
      s.id === name ||
      s.name.toLowerCase() === clean ||
      `${s.name} ${s.ref}`.toLowerCase() === clean ||
      spaceLabel(s).toLowerCase() === clean ||
      s.ref.toLowerCase() === clean,
  )
}

function entrancesOf(index: SceneIndex): { opening: SceneOpening; space: SceneSpace }[] {
  const out: { opening: SceneOpening; space: SceneSpace }[] = []
  for (const o of index.openingById.values()) {
    if (!o.space_ids.includes('outside')) continue
    const sid = o.space_ids.find((x) => x !== 'outside')
    const space = sid ? index.spaceById.get(sid) : undefined
    if (space) out.push({ opening: o, space })
  }
  return out
}

// ---- Kit routing model -----------------------------------------------------------------------------------

interface Edge {
  to: string
  w: number
  via: string
}

function buildGraph(index: SceneIndex, o: RouteOptions = {}): Map<string, Edge[]> {
  const adj = new Map<string, Edge[]>()
  const add = (u: string, v: string, w: number, via: string) => {
    let lu = adj.get(u)
    if (!lu) adj.set(u, (lu = []))
    let lv = adj.get(v)
    if (!lv) adj.set(v, (lv = []))
    lu.push({ to: v, w, via })
    lv.push({ to: u, w, via })
  }
  const closed = o.closed ?? new Set<string>()
  const allOpenings = [...index.openingById.values(), ...(o.extraOpenings ?? [])]
  for (const op of allOpenings) {
    if (closed.has(op.id)) continue
    if (o.stepFree && !op.step_free) continue
    if (o.minWidth && op.width_m < o.minWidth) continue
    const door = 'door:' + op.id
    for (const sid of op.space_ids) {
      if (sid === 'outside') {
        if (!o.indoor) add('outside', door, 0, op.id)
        continue
      }
      const s = index.spaceById.get(sid)
      if (!s || closed.has(sid)) continue
      add(sid, door, dist(s.anchor, op.position), op.id)
    }
  }
  for (const c of index.connectorById.values()) {
    if (closed.has(c.id) || (o.stepFree && !c.step_free)) continue
    if (o.noLifts && c.kind === 'lift') continue
    for (let i = 0; i < c.stops.length; i++) {
      for (let j = i + 1; j < c.stops.length; j++) {
        const fi = index.floorById.get(c.stops[i][0])
        const fj = index.floorById.get(c.stops[j][0])
        if (!fi || !fj) continue
        const span = Math.abs(fj.level - fi.level)
        const w = c.kind === 'lift' ? 15 + 6 * span : 12 * span
        add(c.stops[i][1], c.stops[j][1], w, c.id)
      }
    }
  }
  return adj
}

function dijkstra(adj: Map<string, Edge[]>, src: string) {
  const d = new Map<string, number>([[src, 0]])
  const prev = new Map<string, { from: string; via: string; w: number }>()
  const done = new Set<string>()
  const q: [number, string][] = [[0, src]]
  while (q.length) {
    let bi = 0
    for (let i = 1; i < q.length; i++) if (q[i][0] < q[bi][0]) bi = i
    const [du, u] = q.splice(bi, 1)[0]
    if (done.has(u)) continue
    done.add(u)
    for (const e of adj.get(u) ?? []) {
      const nd = du + e.w
      if (nd < (d.get(e.to) ?? Infinity)) {
        d.set(e.to, nd)
        prev.set(e.to, { from: u, via: e.via, w: e.w })
        q.push([nd, e.to])
      }
    }
  }
  return { d, prev }
}

export function distancesFrom(index: SceneIndex, start: string, o: RouteOptions = {}): Map<string, number> {
  return dijkstra(buildGraph(index, o), start).d
}

export function nearestWith(
  index: SceneIndex,
  start: string,
  test: (s: SceneSpace) => boolean,
  o: RouteOptions = {},
): { space: SceneSpace; d: number } | null {
  const d = distancesFrom(index, start, { indoor: true, ...o })
  let best: { space: SceneSpace; d: number } | null = null
  for (const s of index.spaceById.values()) {
    if (!test(s)) continue
    const v = d.get(s.id)
    if (v === undefined) continue
    if (!best || v < best.d) best = { space: s, d: v }
  }
  return best
}

const isCorridorLike = (s: SceneSpace): boolean => {
  if (s.polygon.length !== 4 || s.type === 'room') return false
  const bb = bboxOf(s.polygon)
  return Math.max(bb.w, bb.h) / Math.max(0.1, Math.min(bb.w, bb.h)) >= 2.5
}

const onAxis = (s: SceneSpace, p: readonly [number, number]): XY => {
  const bb = bboxOf(s.polygon)
  return bb.w >= bb.h
    ? [Math.min(bb.maxX - 0.6, Math.max(bb.minX + 0.6, p[0])), (bb.minY + bb.maxY) / 2]
    : [(bb.minX + bb.maxX) / 2, Math.min(bb.maxY - 0.6, Math.max(bb.minY + 0.6, p[1]))]
}

const getOpening = (index: SceneIndex, id: string, extra?: SceneOpening[]): SceneOpening | undefined =>
  index.openingById.get(id) ?? extra?.find((x) => x.id === id)

function routeSegments(index: SceneIndex, nodes: string[], extra?: SceneOpening[]): RouteSegment[] {
  let brk = false
  const segs: RouteSegment[] = []
  const add = (fid: string, p: XY) => {
    if (brk || !segs.length || segs[segs.length - 1].floor_id !== fid) {
      segs.push({ floor_id: fid, points: [] })
    }
    brk = false
    segs[segs.length - 1].points.push(p)
  }
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i]
    if (n === 'outside') {
      brk = true
      continue
    }
    if (n.startsWith('door:')) {
      const o = getOpening(index, n.slice(5), extra)
      if (o) add(o.floor_id, o.position as XY)
      continue
    }
    const s = index.spaceById.get(n)
    if (!s) continue
    const prev = nodes[i - 1]
    const next = nodes[i + 1]
    const viaConn = (prev && index.spaceById.has(prev)) || (next && index.spaceById.has(next))
    if (i === 0 || i === nodes.length - 1 || viaConn) {
      add(s.floor_id, s.anchor as XY)
      continue
    }
    if (isCorridorLike(s) && prev?.startsWith('door:') && next?.startsWith('door:')) {
      const opPrev = getOpening(index, prev.slice(5), extra)
      const opNext = getOpening(index, next.slice(5), extra)
      if (opPrev && opNext) {
        add(s.floor_id, onAxis(s, opPrev.position))
        add(s.floor_id, onAxis(s, opNext.position))
        continue
      }
    }
  }
  return segs
}

const theName = (n: string) => (/^[A-Z][a-z]+ \d/.test(n) ? n : 'the ' + n)

function buildRouteSteps(
  index: SceneIndex,
  nodes: string[],
  edges: { node: string; from: string; via: string; w: number }[],
  extra?: SceneOpening[],
): WalkStep[] {
  const out: WalkStep[] = []
  const cum = [0]
  for (const e of edges) cum.push(cum[cum.length - 1] + e.w)
  const sp = (i: number) => index.spaceById.get(nodes[i])
  const start = sp(0)
  const end = sp(nodes.length - 1)
  if (!start || !end) return out
  out.push({ icon: 'circle-dot', text: `Start at ${spaceLabel(start)}`, d: 0, floorId: start.floor_id, spaceId: start.id })
  let i = 1
  while (i < nodes.length - 1) {
    const n = nodes[i]
    if (n === 'outside') {
      const back = nodes.slice(i + 1).find((x) => index.spaceById.has(x))
      const bs = back ? index.spaceById.get(back) : undefined
      const wasId = [...nodes.slice(0, i)].reverse().find((x) => index.spaceById.has(x))
      const was = wasId ? index.spaceById.get(wasId) : undefined
      if (bs && was) {
        out.push({
          icon: 'door-open',
          text: `Go outside from ${spaceLabel(was)} and come back in at ${spaceLabel(bs)}`,
          d: 0,
          floorId: bs.floor_id,
          spaceId: bs.id,
          outdoor: true,
        })
      }
      i++
      continue
    }
    if (!index.spaceById.has(n)) {
      i++
      continue
    }
    const s = sp(i)!
    if (index.spaceById.has(nodes[i + 1])) {
      // Connector edge
      const e = edges[i]
      const c = e ? index.connectorById.get(e.via) : undefined
      const to = sp(i + 1)!
      const up = (index.floorById.get(to.floor_id)?.level ?? 0) > (index.floorById.get(s.floor_id)?.level ?? 0)
      out.push({
        icon: up ? 'arrow-up' : 'arrow-down',
        text: `Take ${theName(c?.name ?? s.name)} ${up ? 'up' : 'down'} to ${floorNameOf(index, to.floor_id)}`,
        d: e?.w ?? 15,
        floorId: to.floor_id,
        spaceId: to.id,
        connector: c?.id,
        kind: c?.kind,
      })
      i += 1
      continue
    }
    if (s.type === 'stair' || s.type === 'lift') {
      i++
      continue
    }
    const enter = cum[i - 1] ?? cum[i] ?? 0
    const exit = cum[i + 1] ?? cum[i] ?? enter
    const nextS = nodes.slice(i + 1).find((x) => index.spaceById.has(x))
    const nextSpace = nextS ? index.spaceById.get(nextS) : undefined
    let text = `Go through ${spaceLabel(s)}`
    let icon: StepIcon = 'arrow-right'
    if (nextSpace && (nextSpace.type === 'lift' || nextSpace.type === 'stair')) {
      const conn = [...index.connectorById.values()].find((c) => c.stops.some(([, sid]) => sid === nextSpace.id))
      text = `Follow ${spaceLabel(s)} to ${theName(conn ? conn.name : spaceLabel(nextSpace))}`
    } else if (nextS === end.id && isCorridorLike(s) && nodes[i - 1]?.startsWith('door:') && nodes[i + 1]?.startsWith('door:')) {
      const opIn = getOpening(index, nodes[i - 1].slice(5), extra)
      const opOut = getOpening(index, nodes[i + 1].slice(5), extra)
      if (opIn && opOut) {
        const a = onAxis(s, opIn.position)
        const dp = opOut.position
        const bp = onAxis(s, dp)
        const t = [bp[0] - a[0], bp[1] - a[1]]
        const off = [dp[0] - bp[0], dp[1] - bp[1]]
        const cr = t[0] * off[1] - t[1] * off[0]
        const side = Math.hypot(t[0], t[1]) < 0.8 ? 'ahead' : cr > 0 ? 'left' : 'right'
        text =
          side === 'ahead'
            ? `Cross ${spaceLabel(s)}, ${spaceLabel(end)} is straight ahead`
            : `Follow ${spaceLabel(s)}, ${spaceLabel(end)} is on your ${side}`
        icon = side === 'left' ? 'corner-up-left' : side === 'right' ? 'corner-up-right' : 'arrow-up'
      }
    }
    out.push({ icon, text, d: Math.max(0, exit - enter), floorId: s.floor_id, spaceId: s.id })
    i++
  }
  out.push({ icon: 'flag', text: `Arrive at ${spaceLabel(end)}`, d: 0, floorId: end.floor_id, spaceId: end.id })
  return out
}

export function computeRoute(
  index: SceneIndex,
  start: string,
  goal: string,
  opts: RouteOptions = {},
): ComputedRoute | null {
  const o: RouteOptions = { indoor: true, ...opts }
  const stops = [start, ...(o.via ?? []), goal]
  const adj = buildGraph(index, o)
  const nodes = [start]
  const edges: { node: string; from: string; via: string; w: number }[] = []
  let length = 0
  for (let k = 0; k + 1 < stops.length; k++) {
    const { d, prev } = dijkstra(adj, stops[k])
    const legDist = d.get(stops[k + 1])
    if (legDist === undefined) return null
    const leg: { node: string; from: string; via: string; w: number }[] = []
    let n = stops[k + 1]
    while (n !== stops[k]) {
      const p = prev.get(n)!
      leg.unshift({ node: n, ...p })
      n = p.from
    }
    for (const e of leg) {
      nodes.push(e.node)
      edges.push(e)
    }
    length += legDist
  }
  const spaces = nodes.filter((n) => index.spaceById.has(n))
  const connectors = [...new Set(edges.map((e) => e.via).filter((v) => index.connectorById.has(v)))]
  const doors = edges
    .map((e) => e.via)
    .filter((v) => index.openingById.has(v) || (o.extraOpenings ?? []).some((x) => x.id === v))
  const segments = routeSegments(index, nodes, o.extraOpenings)
  const floors = [...new Set(segments.map((l) => l.floor_id))]
  const steps = buildRouteSteps(index, nodes, edges, o.extraOpenings)
  const showRoute: ShowRoute = {
    kind: 'route',
    segments,
    length_m: length,
    space_ids: spaces,
    steps: steps.map((s) => s.text),
  }
  return { start, goal, nodes, length, spaces, connectors, doors, segments, floors, steps, showRoute }
}

/**
 * Given a `ShowRoute` from the backend agent (`space_ids`, `length_m`), reconstruct rich `WalkStep[]`
 * (including corridor left/right turn instructions and per-step distances) for the Result sheet & Walk Mode.
 */
export function enrichShowRoute(index: SceneIndex, show: ShowRoute): { steps: WalkStep[]; floors: string[]; connectors: string[] } {
  const spaceIds = show.space_ids.filter((id) => index.spaceById.has(id))
  if (spaceIds.length >= 2) {
    // Reconstruct node/door/connector sequence along the backend's exact space_path
    const nodes: string[] = [spaceIds[0]]
    const edges: { node: string; from: string; via: string; w: number }[] = []
    for (let i = 0; i + 1 < spaceIds.length; i++) {
      const a = index.spaceById.get(spaceIds[i])!
      const b = index.spaceById.get(spaceIds[i + 1])!
      if (a.floor_id === b.floor_id) {
        // Find shared door
        let bestOp: SceneOpening | null = null
        let bestCost = Infinity
        for (const op of index.openingsByFloor.get(a.floor_id) ?? []) {
          if (!op.space_ids.includes(a.id) || !op.space_ids.includes(b.id)) continue
          const c = dist(a.anchor, op.position) + dist(op.position, b.anchor)
          if (c < bestCost) {
            bestCost = c
            bestOp = op
          }
        }
        if (bestOp) {
          const d1 = dist(a.anchor, bestOp.position)
          const d2 = dist(bestOp.position, b.anchor)
          const doorNode = 'door:' + bestOp.id
          nodes.push(doorNode, b.id)
          edges.push({ node: doorNode, from: a.id, via: bestOp.id, w: d1 })
          edges.push({ node: b.id, from: doorNode, via: bestOp.id, w: d2 })
          continue
        }
      } else {
        // Find connector between a and b
        const conn = [...index.connectorById.values()].find(
          (c) => c.stops.some(([, sid]) => sid === a.id) && c.stops.some(([, sid]) => sid === b.id),
        )
        const fa = index.floorById.get(a.floor_id)
        const fb = index.floorById.get(b.floor_id)
        const span = Math.abs((fb?.level ?? 0) - (fa?.level ?? 0))
        const w = conn?.kind === 'lift' ? 15 + 6 * span : 12 * Math.max(1, span)
        nodes.push(b.id)
        edges.push({ node: b.id, from: a.id, via: conn?.id ?? '', w })
        continue
      }
      const w = dist(a.anchor, b.anchor)
      nodes.push(b.id)
      edges.push({ node: b.id, from: a.id, via: '', w })
    }
    const steps = buildRouteSteps(index, nodes, edges)
    const floors = [...new Set(show.segments.map((s) => s.floor_id))]
    const connectors = [...new Set(edges.map((e) => e.via).filter((v) => index.connectorById.has(v)))]
    return { steps, floors: floors.length ? floors : [spaceIds[0] ? index.spaceById.get(spaceIds[0])!.floor_id : ''], connectors }
  }
  const fallbackSteps: WalkStep[] = (show.steps ?? []).map((text, idx) => {
    const sid = spaceIds[Math.min(idx, spaceIds.length - 1)] ?? spaceIds[0] ?? ''
    const fid = index.spaceById.get(sid)?.floor_id ?? show.segments[0]?.floor_id ?? ''
    const icon: StepIcon = idx === 0 ? 'circle-dot' : idx === (show.steps?.length ?? 1) - 1 ? 'flag' : 'arrow-right'
    return { icon, text, d: 0, floorId: fid, spaceId: sid }
  })
  return {
    steps: fallbackSteps,
    floors: [...new Set(show.segments.map((s) => s.floor_id))],
    connectors: [],
  }
}

/** Midpoint and direction of the longest wall two spaces share (>= 1 m within 0.35 m). */
export function sharedWall(index: SceneIndex, aId: string, bId: string): SharedWall | null {
  const A = index.spaceById.get(aId)
  const B = index.spaceById.get(bId)
  if (!A || !B || A.floor_id !== B.floor_id) return null
  const tol = 0.35
  const edges = (p: readonly (readonly [number, number])[]) => p.map((q, i) => [q, p[(i + 1) % p.length]] as const)
  let best: SharedWall | null = null
  for (const [p1, p2] of edges(A.polygon)) {
    for (const [q1, q2] of edges(B.polygon)) {
      const vert = Math.abs(p1[0] - p2[0]) < 1e-4 && Math.abs(q1[0] - q2[0]) < 1e-4 && Math.abs(p1[0] - q1[0]) <= tol
      const hor = Math.abs(p1[1] - p2[1]) < 1e-4 && Math.abs(q1[1] - q2[1]) < 1e-4 && Math.abs(p1[1] - q1[1]) <= tol
      if (!vert && !hor) continue
      const k = vert ? 1 : 0
      const lo = Math.max(Math.min(p1[k], p2[k]), Math.min(q1[k], q2[k]))
      const hi = Math.min(Math.max(p1[k], p2[k]), Math.max(q1[k], q2[k]))
      if (hi - lo < 1) continue
      const mid = (lo + hi) / 2
      const pos: XY = vert ? [(p1[0] + q1[0]) / 2, mid] : [mid, (p1[1] + q1[1]) / 2]
      if (!best || hi - lo > best.len) best = { len: hi - lo, pos, vertical: vert }
    }
  }
  return best
}

// ---- Claude Design Scenarios (computed dynamically from SceneIndex) --------------------------------------

const T = (tool: string, args: string, result: string): TraceItem => ({ tool, args, result })
const q = (s: string) => `"${s}"`

function makeRouteResult(
  index: SceneIndex,
  buildingId: string,
  buildingName: string,
  r: ComputedRoute,
  extra: Partial<ScenarioResult> = {},
): ScenarioResult {
  const end = index.spaceById.get(r.goal)!
  return {
    kind: 'route',
    buildingId,
    buildingName,
    route: r,
    steps: r.steps,
    title: extra.title ?? spaceLabel(end),
    sub: extra.sub ?? `${floorNameOf(index, end.floor_id)} · ${fmtM(r.length)} · ${fmtWalk(r.length)}`,
    understood: extra.understood ?? `${spaceLabel(index.spaceById.get(r.start)!)} → ${spaceLabel(end)}`,
    floors: r.floors,
    focusFloor: r.floors[0] ?? end.floor_id,
    ...extra,
  }
}

export function buildCoffeeScenario(index: SceneIndex, buildingId: string, buildingName: string): Scenario | null {
  const hasCoffee = (s: SceneSpace) =>
    (index.assetsBySpace.get(s.id) ?? []).some((a) => /coffee|espresso/i.test(a.type) || /coffee/i.test(a.name))
  let rooms = [...index.spaceById.values()].filter(hasCoffee)
  const itemLabel = rooms.length > 0 ? 'coffee_machine' : 'kitchen'
  if (!rooms.length) {
    rooms = [...index.spaceById.values()].filter((s) => /kitchen|canteen|cafe|pantry/i.test(s.room_type) || /kitchen|coffee/i.test(s.name))
  }
  if (!rooms.length) return null
  const machines = findAssets(index, /^coffee_machine$/i).length || rooms.length
  const seen = new Set<string>()
  const starts: SceneSpace[] = []
  for (const sp of [...findSpaces(index, /^lobby$/i), ...entrancesOf(index).map((e) => e.space)]) {
    if (!seen.has(sp.id)) {
      seen.add(sp.id)
      starts.push(sp)
    }
  }
  if (!starts.length) {
    const first = [...index.spaceById.values()][0]
    if (first) starts.push(first)
  }
  const targetSet = new Set(rooms.map((r) => r.id))
  const options: AskOption[] = starts.slice(0, 3).flatMap((s) => {
    const near = nearestWith(index, s.id, (sp) => targetSet.has(sp.id))
    if (!near) return []
    const r = computeRoute(index, s.id, near.space.id)
    if (!r) return []
    const sName = spaceLabel(s)
    const nName = spaceLabel(near.space)
    return [
      {
        label: sName,
        sub: `${floorNameOf(index, s.floor_id)} · ${s.room_type === 'lobby' ? 'lobby' : 'entrance'}`,
        replyText: `I'm at ${sName}.`,
        post: [
          T('nearest', `start=${q(sName)}, item_type="${itemLabel}"`, `${nName} · ${fmtM(near.d)}`),
          T('route', `start=${q(sName)}, destination=${q(nName)}`, `${fmtM(r.length)} · ${r.floors.length} floor`),
        ],
        result: makeRouteResult(index, buildingId, buildingName, r, {
          understood: `From ${sName} to the closest coffee machine, walking.`,
          say: `${nName}, ${fmtWalk(r.length)} from you on the ${floorNameOf(index, near.space.floor_id).toLowerCase()}.`,
          remember: [{ icon: 'map-pin', text: `At ${sName}`, spaceId: s.id }],
        }),
      },
    ]
  })
  if (!options.length) return null
  return {
    id: 'coffee',
    buildingId,
    buildingName,
    persona: 'New employee',
    icon: 'coffee',
    chip: 'Where can I get a coffee?',
    user: "Hi, it's my first day here. Where can I get a coffee?",
    pre: [T('find', 'query="coffee machine"', `${machines} machines in ${rooms.length} rooms`)],
    ask: {
      q: 'Where are you right now?',
      why: `Then I'll pick the closest of the ${rooms.length} kitchens. I'll remember it for the rest of your visit.`,
      options,
      scan: true,
    },
  }
}

export function buildWheelchairScenario(
  index: SceneIndex,
  buildingId: string,
  buildingName: string,
  fromName: string,
  toName: string,
  id: 'wheelchair' | 'wheelchair2',
  followUp?: string,
  followLabel?: string,
): Scenario | null {
  const A = spaceByName(index, fromName)
  const B = spaceByName(index, toName)
  if (!A || !B) return null
  const r = computeRoute(index, A.id, B.id, { stepFree: true })
  if (!r) return null
  const lift = r.connectors[0] ? index.connectorById.get(r.connectors[0]) : undefined
  const aLabel = spaceLabel(A)
  const bLabel = spaceLabel(B)
  return {
    id,
    buildingId,
    buildingName,
    persona: 'Wheelchair user',
    icon: 'accessibility',
    chip: id === 'wheelchair' ? `Step-free from ${aLabel} to ${bLabel}` : `Then to ${bLabel}`,
    user: id === 'wheelchair' ? `I use a wheelchair. How do I get from ${aLabel} to ${bLabel}?` : `Great, and from there to ${bLabel}?`,
    pre: [T('route', `start=${q(aLabel)}, destination=${q(bLabel)}, step_free=true`, `${fmtM(r.length)} · via ${lift?.name ?? 'step-free path'}`)],
    result: makeRouteResult(index, buildingId, buildingName, r, {
      understood:
        id === 'wheelchair'
          ? `${aLabel} → ${bLabel}, step-free. I'll keep every route step-free from now on.`
          : `From ${aLabel}, where you are now. Still step-free.`,
      say: `${bLabel}, ${fmtWalk(r.length)}${lift ? ` using ${lift.name}` : ''}.`,
      remember: [
        { icon: 'accessibility', text: 'Step-free routes', stepFree: true },
        { icon: 'map-pin', text: `At ${bLabel}`, spaceId: B.id },
      ],
      followUp,
      followLabel,
    }),
  }
}

export function buildTechnicianScenario(index: SceneIndex, buildingId: string, buildingName: string): Scenario | null {
  const asset = findAssets(index, /^CNC mill .*-B$/i)[0] ?? findAssets(index, /cnc|mill|printer|press|robot|lathe/i)[0]
  if (!asset) return null
  const room = index.spaceById.get(asset.space_id)
  if (!room) return null
  const start = spaceByName(index, 'Main aisle H-B5') ?? entrancesOf(index)[0]?.space ?? [...index.spaceById.values()][0]
  if (!start) return null
  const r = computeRoute(index, start.id, room.id)
  if (!r) return null
  const startLabel = spaceLabel(start)
  const roomLabel = spaceLabel(room)
  const via = r.steps
    .filter((s) => s.icon !== 'circle-dot' && s.icon !== 'flag')
    .map((s) => s.text.replace(/^(Go through|Follow|Cross) /, ''))
  return {
    id: 'technician',
    buildingId,
    buildingName,
    persona: 'Service technician',
    icon: 'wrench',
    voice: true,
    chip: `${asset.name.includes('-B') ? 'CNC mill B' : asset.name}, from ${startLabel}`,
    user: `Got a call about ${asset.name.includes('-B') ? 'CNC mill B' : asset.name}. How do I get there from ${startLabel}? Keep it short.`,
    pre: [
      T('find', `query="${asset.name.includes('-B') ? 'cnc mill b' : asset.name.toLowerCase()}"`, `${asset.name} · ${roomLabel}`),
      T('route', `start=${q(startLabel)}, destination=${q(asset.name)}`, `${fmtM(r.length)}`),
    ],
    result: makeRouteResult(index, buildingId, buildingName, r, {
      title: asset.name,
      sub: `${roomLabel} · ${fmtM(r.length)} · ${fmtWalk(r.length)}`,
      assetIds: [asset.id],
      understood: `${asset.name}${asset.name.includes('-B') ? ' (the only "B" mill)' : ''} from ${startLabel}. Short spoken directions.`,
      say: `${via.length > 1 ? `Through ${via.slice(0, -1).join(', ')} and ${via[via.length - 1]}` : via[0] ? `Through ${via[0]}` : 'Direct walk'}. The ${asset.name.toLowerCase().includes('mill') ? 'mill' : 'item'} is in ${roomLabel}, ${fmtM(r.length).replace(' m', ' metres')}.`,
      remember: [{ icon: 'volume-2', text: 'Short spoken answers', voice: true }],
    }),
  }
}

export function buildForkliftScenario(index: SceneIndex, buildingId: string, buildingName: string): Scenario | null {
  const from = findSpaces(index, /^logistics_zone$/i)[0] ?? entrancesOf(index)[0]?.space
  const zones = findSpaces(index, /^warehouse_zone$/i)
  if (!from || zones.length < 2) return null
  const o: RouteOptions = { stepFree: true, minWidth: 3 }
  const opts = zones
    .map((z) => ({ z, r: computeRoute(index, from.id, z.id, o) }))
    .sort((a, c) => (a.r?.length ?? 1e9) - (c.r?.length ?? 1e9))
  const fromLabel = spaceLabel(from)
  return {
    id: 'forklift',
    buildingId,
    buildingName,
    persona: 'Forklift driver',
    icon: 'forklift',
    chip: 'Forklift run to the warehouse',
    user: 'Forklift run from the logistics zone to the warehouse. Which way?',
    pre: [
      T('find', 'query="logistics zone"', fromLabel),
      T('find', 'query="warehouse"', `${zones.length} warehouse zones`),
    ],
    ask: {
      q: 'Which warehouse zone?',
      why: `There are ${zones.length}. Distances use openings 3 m or wider and no steps.`,
      options: opts.map(({ z, r }) => {
        const zLabel = spaceLabel(z)
        const minDoorW = r
          ? Math.min(...r.doors.map((d) => index.openingById.get(d)?.width_m ?? 3))
          : 0
        return {
          label: zLabel,
          sub: r ? `${fmtM(r.length)} for the forklift` : 'No opening wide enough',
          disabled: !r,
          replyText: zLabel,
          post: r
            ? [
                T(
                  'route',
                  `start=${q(fromLabel)}, destination=${q(zLabel)}, step_free=true, min_width_m=3`,
                  `${fmtM(r.length)} · narrowest ${minDoorW.toFixed(1)} m`,
                ),
              ]
            : [],
          result: r
            ? makeRouteResult(index, buildingId, buildingName, r, {
                understood: `Forklift: ${fromLabel} → ${zLabel}. Openings 3 m or wider, no steps.`,
                say: `${zLabel}, ${fmtM(r.length)}. Every opening on the way is at least 3 metres wide.`,
                remember: [{ icon: 'forklift', text: 'Forklift · 3 m openings', stepFree: true, minWidth: 3 }],
              })
            : undefined,
        }
      }),
    },
  }
}

export function buildLiftClosedScenario(
  index: SceneIndex,
  buildingId: string,
  buildingName: string,
  liftName?: string,
): Scenario | null {
  const lifts = [...index.connectorById.values()].filter((c) => c.kind === 'lift')
  const lift = (liftName ? lifts.find((c) => c.name.toLowerCase() === liftName.toLowerCase()) : undefined) ?? lifts[0]
  if (!lift) return null
  const before = distancesFrom(index, 'outside', { stepFree: true })
  const after = distancesFrom(index, 'outside', { stepFree: true, closed: new Set([lift.id]) })
  const rooms = [...index.spaceById.values()].filter((s) => s.type === 'room')
  const cut = rooms.filter((s) => before.has(s.id) && !after.has(s.id))
  const longer = rooms.filter((s) => before.has(s.id) && after.has(s.id) && after.get(s.id)! - before.get(s.id)! > 1)
  const avg = longer.length ? longer.reduce((a, s) => a + (after.get(s.id)! - before.get(s.id)!), 0) / longer.length : 0
  const others = lifts.filter((c) => c.id !== lift.id).map((c) => c.name)
  const byFloor: Record<string, number> = {}
  for (const s of [...cut, ...longer]) byFloor[s.floor_id] = (byFloor[s.floor_id] || 0) + 1
  const focusFloor = Object.entries(byFloor).sort((a, c) => c[1] - a[1])[0]?.[0] ?? lift.stops[1]?.[0] ?? lift.stops[0]?.[0]
  if (!focusFloor) return null
  return {
    id: 'lift',
    buildingId,
    buildingName,
    persona: 'Facility manager',
    icon: 'triangle-alert',
    chip: `The ${lift.name.toLowerCase()} is out of service today`,
    user: `Heads up, the ${lift.name.toLowerCase()} is out of service today.`,
    pre: [T('what_if', `floor="all", close=["${lift.name}"], step_free=true`, `${cut.length} cut off · ${longer.length} longer`)],
    result: {
      kind: 'impact',
      buildingId,
      buildingName,
      title: `${lift.name} out of service`,
      sub: `${lift.stops.length} floors · not applied yet`,
      understood: `The ${lift.name.toLowerCase()} is closed today. Before I change the map, here is who it affects.`,
      stats: [
        { n: String(cut.length), label: cut.length === 1 ? 'room loses step-free access' : 'rooms lose step-free access' },
        { n: String(longer.length), label: `rooms get a longer step-free walk${longer.length ? ` (+${Math.round(avg)} m on average)` : ''}` },
      ].filter((x, i) => i === 0 || x.n !== '0'),
      note: others.length ? `Step-free routes switch to ${others.join(' and ')}.` : 'No other lift serves these floors.',
      floors: [focusFloor],
      focusFloor,
      closed: [lift.id],
      affected: [...cut, ...longer].map((s) => s.id),
      focusIds: null,
      confirm: `Mark ${lift.name.toLowerCase()} closed`,
      applied: `${lift.name} closed. Every route avoids it until you reopen it.`,
      appliedTrace: T('set_closed', `element="${lift.name}", closed=true`, 'closed'),
      undo: 'Reopen',
      remember: [],
    },
  }
}

export function buildAddDoorScenario(
  index: SceneIndex,
  buildingId: string,
  buildingName: string,
  roomAName = 'Seminar room 2.06',
  roomBName = 'Kitchen 2.07',
): Scenario | null {
  let A = spaceByName(index, roomAName)
  let B = spaceByName(index, roomBName)
  let wall = A && B ? sharedWall(index, A.id, B.id) : null
  if (!A || !B || !wall) {
    // Find any two rooms on the same floor that share a wall but don't already have a direct door
    const rooms = [...index.spaceById.values()].filter((s) => s.type === 'room')
    outer: for (let i = 0; i < rooms.length; i++) {
      for (let j = i + 1; j < rooms.length; j++) {
        if (rooms[i].floor_id !== rooms[j].floor_id) continue
        const hasDoor = (index.openingsByFloor.get(rooms[i].floor_id) ?? []).some(
          (o) => o.space_ids.includes(rooms[i].id) && o.space_ids.includes(rooms[j].id),
        )
        if (hasDoor) continue
        const w = sharedWall(index, rooms[i].id, rooms[j].id)
        if (w) {
          A = rooms[i]
          B = rooms[j]
          wall = w
          break outer
        }
      }
    }
  }
  if (!A || !B || !wall) return null
  const r0 = computeRoute(index, A.id, B.id)
  if (!r0) return null
  const door: SceneOpening = {
    id: 'NEW-door',
    floor_id: A.floor_id,
    space_ids: [A.id, B.id],
    kind: 'door',
    position: wall.pos,
    width_m: 0.9,
    step_free: true,
  }
  const r1 = computeRoute(index, A.id, B.id, { extraOpenings: [door] })
  if (!r1) return null
  const aLabel = spaceLabel(A)
  const bLabel = spaceLabel(B)
  const fLevel = index.floorById.get(A.floor_id)?.level ?? 0
  return {
    id: 'door',
    buildingId,
    buildingName,
    persona: 'Facility manager',
    icon: 'door-open',
    chip: `Add a door: ${aLabel} ↔ ${bLabel}`,
    user: `Add a door between ${aLabel} and ${bLabel}.`,
    pre: [
      T('room_details', `room=${q(aLabel)}`, `shares ${wall.len.toFixed(1)} m of wall with ${bLabel}`),
      T('what_if', `floor="${fLevel}", add_door_between=[${q(aLabel)}, ${q(bLabel)}]`, 'preview only'),
      T('route', `start=${q(aLabel)}, destination=${q(bLabel)}`, `${fmtM(r0.length)} today`),
    ],
    result: {
      kind: 'edit',
      buildingId,
      buildingName,
      title: `New door, ${floorNameOf(index, A.floor_id)}`,
      sub: `${aLabel} ↔ ${bLabel} · 0.9 m · step-free`,
      understood: `A 0.9 m step-free door in the middle of the ${wall.len.toFixed(1)} m wall the two rooms share.`,
      stats: [{ n: `${fmtM(r0.length)} → ${fmtM(r1.length)}`, label: `${aLabel} to ${bLabel}` }],
      note: 'Nothing changes on the map until you confirm.',
      floors: [A.floor_id],
      focusFloor: A.floor_id,
      ghostDoor: { floorId: A.floor_id, pos: wall.pos, vertical: wall.vertical },
      focusIds: [A.id, B.id],
      confirm: 'Add the door',
      applied: 'Door added. Every later route can use it.',
      appliedTrace: T('add_door', `between=[${q(aLabel)}, ${q(bLabel)}]`, 'scene_changed'),
      undo: 'Undo',
      remember: [],
    },
  }
}

export const STARTER_ORDER = ['coffee', 'wheelchair', 'technician', 'forklift', 'lift', 'door'] as const

export function buildScenarioSet(
  campusIndex: SceneIndex | null,
  factoryIndex: SceneIndex | null,
  activeIndex: SceneIndex | null,
  activeId: string | null,
  activeName: string,
): Record<string, Scenario> {
  const out: Record<string, Scenario> = {}
  const isPitchBuilding = activeId === 'proc-campus-dev-000' || activeId === 'proc-factory-dev-003'
  const cIdx = (isPitchBuilding ? campusIndex : activeIndex) ?? campusIndex ?? activeIndex
  const cId = isPitchBuilding && campusIndex ? 'proc-campus-dev-000' : (activeId ?? 'proc-campus-dev-000')
  const cName = isPitchBuilding && campusIndex ? 'Office campus' : activeName

  const fIdx = (isPitchBuilding ? factoryIndex : activeIndex) ?? factoryIndex ?? activeIndex
  const fId = isPitchBuilding && factoryIndex ? 'proc-factory-dev-003' : (activeId ?? 'proc-factory-dev-003')
  const fName = isPitchBuilding && factoryIndex ? 'Factory & office' : activeName

  if (cIdx) {
    const coffee = buildCoffeeScenario(cIdx, cId, cName)
    if (coffee) out.coffee = coffee

    const rooms = [...cIdx.spaceById.values()].filter((s) => s.type === 'room')
    const lobby = spaceByName(cIdx, 'Lobby 0.01') ?? findSpaces(cIdx, /^lobby$/i)[0] ?? entrancesOf(cIdx)[0]?.space ?? rooms[0]
    const meet = spaceByName(cIdx, 'Meeting room 1.13') ?? rooms.find((s) => s.floor_id !== lobby?.floor_id) ?? rooms[1]
    const kitch =
      spaceByName(cIdx, 'Kitchen 1.07') ??
      rooms.find((s) => s.id !== meet?.id && s.floor_id === meet?.floor_id) ??
      rooms[2]

    if (lobby && meet) {
      const w2 = kitch ? buildWheelchairScenario(cIdx, cId, cName, meet.name, kitch.name, 'wheelchair2') : null
      if (w2) out.wheelchair2 = w2
      const w1 = buildWheelchairScenario(
        cIdx,
        cId,
        cName,
        lobby.name,
        meet.name,
        'wheelchair',
        w2 ? 'wheelchair2' : undefined,
        w2 && kitch ? `Then to ${spaceLabel(kitch)}` : undefined,
      )
      if (w1) out.wheelchair = w1
    }

    const lift = buildLiftClosedScenario(cIdx, cId, cName, 'West lift')
    if (lift) out.lift = lift

    const door = buildAddDoorScenario(cIdx, cId, cName, 'Seminar room 2.06', 'Kitchen 2.07')
    if (door) out.door = door
  }

  if (fIdx) {
    const tech = buildTechnicianScenario(fIdx, fId, fName)
    if (tech) out.technician = tech

    const fork = buildForkliftScenario(fIdx, fId, fName)
    if (fork) out.forklift = fork
  }

  return out
}

export function matchScenario(scenarios: Record<string, Scenario>, text: string): Scenario | null {
  const s = text.trim()
  if (scenarios[s]) return scenarios[s]
  const lower = s.toLowerCase()
  for (const sc of Object.values(scenarios)) {
    if (sc.user.toLowerCase() === lower || sc.chip.toLowerCase() === lower) return sc
  }
  if (/coffee|espresso|canteen/.test(lower) && scenarios.coffee) return scenarios.coffee
  if (/1\.07/.test(lower) && scenarios.wheelchair2) return scenarios.wheelchair2
  if ((/wheelchair|step[- ]free/.test(lower) || /1\.13/.test(lower)) && scenarios.wheelchair) return scenarios.wheelchair
  if (/cnc|mill|technician|aisle/.test(lower) && scenarios.technician) return scenarios.technician
  if (/forklift|warehouse|logistics/.test(lower) && scenarios.forklift) return scenarios.forklift
  if ((/lift|elevator|out of service/.test(lower)) && scenarios.lift) return scenarios.lift
  if (/add.*door|door.*between|2\.06|2\.07/.test(lower) && scenarios.door) return scenarios.door
  return null
}

export function solveClientQuery(
  index: SceneIndex,
  buildingId: string,
  buildingName: string,
  query: string,
  memory: MemoryChip[],
  closedIds: string[],
): Scenario | null {
  const lower = query.toLowerCase()
  const stepFree = memory.some((m) => m.stepFree) || /wheelchair|step[- ]free|without stairs|no stairs/i.test(lower)
  const minWidth = memory.find((m) => m.minWidth)?.minWidth ?? (/forklift|pallet/i.test(lower) ? 3 : 0)
  const rememberedStartId = memory.find((m) => m.spaceId)?.spaceId
  const closed = new Set(closedIds)

  // Check if spaces or assets in index are mentioned by name
  const mentionedSpaces = [...index.spaceById.values()]
    .filter((s) => s.name && lower.includes(s.name.toLowerCase()))
    .sort((a, b) => b.name.length - a.name.length)

  if (mentionedSpaces.length >= 2) {
    const [A, B] = mentionedSpaces
    if (/door|connect|wall/i.test(lower)) {
      return buildAddDoorScenario(index, buildingId, buildingName, A.name, B.name)
    }
    const r = computeRoute(index, A.id, B.id, { stepFree, minWidth, closed })
    if (r) {
      const aLabel = spaceLabel(A)
      const bLabel = spaceLabel(B)
      return {
        id: 'custom-route',
        buildingId,
        buildingName,
        persona: 'Visitor',
        icon: stepFree ? 'accessibility' : 'coffee',
        chip: `${aLabel} → ${bLabel}`,
        user: query,
        pre: [
          T(
            'route',
            `start=${q(aLabel)}, destination=${q(bLabel)}${stepFree ? ', step_free=true' : ''}`,
            `${fmtM(r.length)}`,
          ),
        ],
        result: makeRouteResult(index, buildingId, buildingName, r, {
          understood: `${aLabel} → ${bLabel}${stepFree ? ', step-free' : ''}.`,
          say: `${bLabel}, ${fmtWalk(r.length)} from ${aLabel}.`,
          remember: [
            ...(stepFree ? [{ icon: 'accessibility' as const, text: 'Step-free routes', stepFree: true }] : []),
            { icon: 'map-pin' as const, text: `At ${bLabel}`, spaceId: B.id },
          ],
        }),
      }
    }
  }

  // Match target spaces or assets by keyword
  const keywords = lower
    .replace(/where|can|get|find|take|nearest|closest|how|from|there|the|please|show|route|way/g, ' ')
    .trim()
    .split(/\s+/)
    .filter((w) => w.length >= 3)
  const re = keywords.length ? new RegExp(keywords.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'i') : null

  const matchedAssets = re ? findAssets(index, re) : []
  const matchedSpaces =
    mentionedSpaces.length === 1
      ? mentionedSpaces
      : re
        ? findSpaces(index, re).filter((s) => s.type === 'room')
        : []

  const targetSpaceIds = new Set<string>([
    ...matchedSpaces.map((s) => s.id),
    ...matchedAssets.map((a) => a.space_id),
  ])

  if (targetSpaceIds.size > 0) {
    const label = matchedAssets[0]?.name ?? (matchedSpaces[0] ? spaceLabel(matchedSpaces[0]) : query)
    if (rememberedStartId && index.spaceById.has(rememberedStartId)) {
      const startSpace = index.spaceById.get(rememberedStartId)!
      const near = nearestWith(index, startSpace.id, (sp) => targetSpaceIds.has(sp.id), { stepFree, minWidth, closed })
      if (near) {
        const r = computeRoute(index, startSpace.id, near.space.id, { stepFree, minWidth, closed })
        if (r) {
          const sName = spaceLabel(startSpace)
          const nName = spaceLabel(near.space)
          return {
            id: 'custom-nearest',
            buildingId,
            buildingName,
            persona: 'Visitor',
            icon: 'coffee',
            chip: query,
            user: query,
            pre: [
              T('find', `query=${q(keywords.join(' ') || query)}`, `${targetSpaceIds.size} match${targetSpaceIds.size === 1 ? '' : 'es'}`),
              T('route', `start=${q(sName)}, destination=${q(nName)}${stepFree ? ', step_free=true' : ''}`, `${fmtM(r.length)}`),
            ],
            result: makeRouteResult(index, buildingId, buildingName, r, {
              title: matchedAssets.length === 1 ? matchedAssets[0].name : nName,
              assetIds: matchedAssets.map((a) => a.id),
              understood: `From ${sName} (where you are) to ${nName}${stepFree ? ', step-free' : ''}.`,
              say: `${nName}, ${fmtWalk(r.length)} from ${sName}.`,
              remember: [{ icon: 'map-pin', text: `At ${nName}`, spaceId: near.space.id }],
            }),
          }
        }
      }
    }

    // Ask where the user is from building lobbies/entrances
    const seen = new Set<string>()
    const starts: SceneSpace[] = []
    for (const sp of [...findSpaces(index, /^lobby$/i), ...entrancesOf(index).map((e) => e.space)]) {
      if (!seen.has(sp.id)) {
        seen.add(sp.id)
        starts.push(sp)
      }
    }
    if (!starts.length) {
      const first = [...index.spaceById.values()][0]
      if (first) starts.push(first)
    }
    const options: AskOption[] = starts.slice(0, 3).flatMap((s) => {
      const near = nearestWith(index, s.id, (sp) => targetSpaceIds.has(sp.id), { stepFree, minWidth, closed })
      if (!near) return []
      const r = computeRoute(index, s.id, near.space.id, { stepFree, minWidth, closed })
      if (!r) return []
      const sName = spaceLabel(s)
      const nName = spaceLabel(near.space)
      return [
        {
          label: sName,
          sub: `${floorNameOf(index, s.floor_id)} · ${fmtM(r.length)} away`,
          replyText: `I'm at ${sName}.`,
          post: [
            T('route', `start=${q(sName)}, destination=${q(nName)}${stepFree ? ', step_free=true' : ''}`, `${fmtM(r.length)}`),
          ],
          result: makeRouteResult(index, buildingId, buildingName, r, {
            title: matchedAssets.length === 1 ? matchedAssets[0].name : nName,
            assetIds: matchedAssets.map((a) => a.id),
            understood: `From ${sName} to ${nName}${stepFree ? ', step-free' : ''}.`,
            say: `${nName}, ${fmtWalk(r.length)} from ${sName}.`,
            remember: [{ icon: 'map-pin', text: `At ${sName}`, spaceId: s.id }],
          }),
        },
      ]
    })
    if (options.length > 0) {
      return {
        id: 'custom-ask',
        buildingId,
        buildingName,
        persona: 'Visitor',
        icon: 'coffee',
        chip: query,
        user: query,
        pre: [T('find', `query=${q(keywords.join(' ') || label)}`, `${targetSpaceIds.size} match${targetSpaceIds.size === 1 ? '' : 'es'}`)],
        ask: {
          q: 'Where are you right now?',
          why: `Then I'll route you to the closest match (${label}).`,
          options,
          scan: true,
        },
      }
    }
  }

  return null
}


