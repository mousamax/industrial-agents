// Pure scene geometry helpers for rendering outputs from the live `building_guide` backend agent:
// 1. Enriching a backend `ShowRoute` (`space_ids`, `segments`, `length_m`) into step-by-step Walk Mode instructions
// 2. Finding the shared wall midpoint & orientation between two spaces returned by `add_door` / `what_if`
// 3. Static starter prompt definitions (no client-side routing, precomputation, or agent simulation)
import type { SceneOpening, SceneSpace, ShowRoute, XY } from '../api/types'
import type { View3DIntent } from '../store'
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
  /** The step's own polyline on `floorId`, when the backend drew it (walk_directions). */
  points?: XY[]
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
  spaceId?: string
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
  route?: {
    start: string
    goal: string
    length: number
    floors: string[]
    steps: WalkStep[]
    showRoute: ShowRoute
  }
  steps?: WalkStep[]
  say?: string
  followUp?: string
  followLabel?: string
  assetIds?: string[]
  focusIds?: string[] | null
  closed?: string[]
  affected?: string[]
  ghostDoors?: { floorId: string; pos: XY; vertical: boolean; widthM: number; wall?: [XY, XY] }[]
  stats?: { n: string; label: string }[]
  note?: string
  confirm?: string
  confirmPrompt?: string
  applied?: string
  undo?: string
  remember?: MemoryChip[]
  /** The assistant could not be reached; the UI says so in plain words instead of showing the error. */
  offline?: boolean
  /** The answer is about floors on top of each other: show it tilted in 3D (see View3DIntent in the store). */
  view3d?: View3DIntent
}

export interface Scenario {
  id: string
  buildingId: string
  buildingName: string
  persona: string
  icon: 'coffee' | 'accessibility' | 'wrench' | 'forklift' | 'triangle-alert' | 'door-open' | 'calendar'
  chip: string
  user: string
  voice?: boolean
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
  return [...index.spaceById.values()].filter(
    (s) => re.test(s.name) || re.test(spaceLabel(s)) || re.test(s.room_type) || re.test(s.ref),
  )
}

export function entrancesOf(index: SceneIndex): { opening: SceneOpening; space: SceneSpace }[] {
  const out: { opening: SceneOpening; space: SceneSpace }[] = []
  for (const o of index.openingById.values()) {
    if (!o.space_ids.includes('outside')) continue
    const sid = o.space_ids.find((x) => x !== 'outside')
    const space = sid ? index.spaceById.get(sid) : undefined
    if (space) out.push({ opening: o, space })
  }
  return out
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

const theName = (n: string) => (/^[A-Z][a-z]+ \d/.test(n) ? n : 'the ' + n)

function buildRouteSteps(
  index: SceneIndex,
  nodes: string[],
  edges: { node: string; from: string; via: string; w: number }[],
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
      const opIn = index.openingById.get(nodes[i - 1].slice(5))
      const opOut = index.openingById.get(nodes[i + 1].slice(5))
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

/**
 * Given a `ShowRoute` from the backend agent (`space_ids`, `length_m`), reconstruct `WalkStep[]`
 * along the backend's exact `space_ids` so each step highlights the corresponding space on the map.
 */
export function enrichShowRoute(
  index: SceneIndex,
  show: ShowRoute,
): { steps: WalkStep[]; floors: string[]; connectors: string[] } {
  const spaceIds = show.space_ids.filter((id) => index.spaceById.has(id))
  if (spaceIds.length >= 2) {
    const nodes: string[] = [spaceIds[0]]
    const edges: { node: string; from: string; via: string; w: number }[] = []
    for (let i = 0; i + 1 < spaceIds.length; i++) {
      const a = index.spaceById.get(spaceIds[i])!
      const b = index.spaceById.get(spaceIds[i + 1])!
      if (a.floor_id === b.floor_id) {
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
    return {
      steps,
      floors: floors.length ? floors : [spaceIds[0] ? index.spaceById.get(spaceIds[0])!.floor_id : ''],
      connectors,
    }
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

/** Prepared starter prompts for the two demo buildings. They name real rooms there, so they belong to those
 *  buildings only; every other building gets suggestions from its own plan (buildingSuggestions). */
export function buildStarterPrompts(): Scenario[] {
  const campusId = 'proc-campus-dev-000'
  const campusName = 'Office campus'
  const factoryId = 'proc-factory-dev-003'
  const factoryName = 'Factory & office'
  const colabId = 'munich-urban-colab'
  const colabName = 'Munich Urban Colab'

  return [
    {
      id: 'hackathon',
      buildingId: colabId,
      buildingName: colabName,
      persona: 'Guest',
      icon: 'calendar',
      chip: 'Where is the hackathon?',
      user: "Hi, I'm a guest. Where is the hackathon?",
    },
    {
      id: 'coffee',
      buildingId: campusId,
      buildingName: campusName,
      persona: 'New employee',
      icon: 'coffee',
      chip: 'Where can I get a coffee?',
      user: "Hi, it's my first day here. Where can I get a coffee?",
    },
    {
      id: 'wheelchair',
      buildingId: campusId,
      buildingName: campusName,
      persona: 'Wheelchair user',
      icon: 'accessibility',
      chip: 'Meeting room 1.12 without stairs',
      user: 'I use a wheelchair. How do I get from Main lobby 0.18 (Ground floor) to Meeting room 1.12 (Floor 1)?',
    },
    {
      id: 'technician',
      buildingId: factoryId,
      buildingName: factoryName,
      persona: 'Service technician',
      icon: 'wrench',
      voice: true,
      chip: 'Take me to CNC mill H-A1-B',
      user: 'Got a call about CNC mill H-A1-B. How do I get there from Main aisle H-B5? Keep it short.',
    },
    {
      id: 'forklift',
      buildingId: factoryId,
      buildingName: factoryName,
      persona: 'Forklift driver',
      icon: 'forklift',
      chip: 'Forklift route to the warehouse',
      user: 'Forklift run (3 m wide, no steps) from the logistics zone to the warehouse. Which way?',
    },
    {
      id: 'lift',
      buildingId: campusId,
      buildingName: campusName,
      persona: 'Facility manager',
      icon: 'triangle-alert',
      chip: 'What if Lift 1 is out today?',
      user: 'What if Lift 1 is out of service today? Which rooms on Floor 1 lose step-free access or get a longer walk?',
    },
    {
      id: 'door',
      buildingId: campusId,
      buildingName: campusName,
      persona: 'Facility manager',
      icon: 'door-open',
      chip: 'Add a door from 2.06 to 2.07',
      user: 'Add a door between Seminar room 2.06 and Kitchen 2.07.',
    },
  ]
}

export interface Suggestion {
  label: string
  text: string
}

/** Questions this building can answer, from what its plan holds: no room named that isn't there. A building
 *  drafted from plans leads with what the guide still needs to know about it. */
export function buildingSuggestions(index: SceneIndex | null, drafted: boolean): Suggestion[] {
  if (!index) return []
  const spaces = [...index.spaceById.values()]
  const assets = [...index.assetById.values()]
  const has = (pattern: RegExp) => spaces.some((s) => pattern.test(s.room_type)) || assets.some((a) => pattern.test(a.type))
  const out: Suggestion[] = []
  if (drafted) {
    out.push({ label: 'What do you still need from me?', text: 'You drafted this building from my floor plans. What do you still need from me?' })
  }
  if (has(/coffee|kitchen|canteen|cafeteria/)) {
    out.push({ label: 'Where can I get a coffee?', text: 'Where can I get a coffee?' })
  }
  if (has(/toilet|\bwc\b|restroom|bathroom/)) {
    out.push({ label: 'Where are the toilets?', text: 'Where is the nearest toilet?' })
  }
  const lift = [...index.connectorById.values()].find((c) => c.kind === 'lift' && c.name)
  if (lift && index.floors.length > 1) {
    out.push({
      label: `What if ${lift.name} is out?`,
      text: `What if ${lift.name} is out of service today? Which rooms lose step-free access?`,
    })
  }
  if (has(/warehouse|logistics/)) {
    out.push({ label: 'Forklift route to the warehouse', text: 'Forklift run (3 m wide, no steps) from the nearest entrance to the warehouse. Which way?' })
  }
  out.push({ label: 'Which room is the biggest?', text: 'Which room in this building is the biggest?' })
  return out
}
