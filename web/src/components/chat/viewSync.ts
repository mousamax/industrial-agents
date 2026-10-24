// Map sync, pure part: tool output -> what the map should show. No store access here; chats.ts applies it.
//
// Preferred path: the tool result carries `view: ViewCommand[]` (contract in src/app/building/view.py).
// Fallback (tools that don't send `view` yet): read ids, routes and "no route" errors out of the raw output.
import type { RouteSegment, ShowRoute, ViewCommand, XY } from '../../api/types'
import { idsIn, type SceneIndex } from '../../lib/sceneIndex'

export interface ToolEffect {
  /** Commands the tool sent explicitly; when non-empty, the fallback fields below are ignored. */
  views: ViewCommand[]
  /** Route found in the output (from `view`, or built from a space path). */
  route: ShowRoute | null
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

/** The door between two neighbouring spaces on one floor, closest to the straight line between anchors. */
function doorBetween(index: SceneIndex, a: string, b: string): XY | null {
  const sa = index.spaceById.get(a)
  const sb = index.spaceById.get(b)
  if (!sa || !sb || sa.floor_id !== sb.floor_id) return null
  let best: XY | null = null
  let bestCost = Infinity
  for (const o of index.openingsByFloor.get(sa.floor_id) ?? []) {
    if (!o.space_ids.includes(a) || !o.space_ids.includes(b)) continue
    const p = o.position as XY
    const cost = dist(sa.anchor as XY, p) + dist(p, sb.anchor as XY)
    if (cost < bestCost) {
      bestCost = cost
      best = p
    }
  }
  return best
}

/**
 * Per-floor polylines through the nodes in order: space anchors, plus door positions (`door:<id>` nodes,
 * or the shared door looked up between consecutive spaces). A new segment starts when the floor changes.
 */
export function segmentsFor(nodes: string[], index: SceneIndex): RouteSegment[] {
  const segments: RouteSegment[] = []
  const push = (floor: string, p: XY) => {
    const last = segments[segments.length - 1]
    if (!last || last.floor_id !== floor) segments.push({ floor_id: floor, points: [p] })
    else last.points.push(p)
  }
  let prevSpace: string | null = null
  let prevWasDoor = false
  for (const node of nodes) {
    if (node.startsWith('door:')) {
      const o = index.openingById.get(node.slice(5))
      if (o) push(o.floor_id, o.position as XY)
      prevWasDoor = true
      continue
    }
    const space = index.spaceById.get(node)
    if (!space) continue // "outside" and unknown nodes
    if (prevSpace && !prevWasDoor) {
      const door = doorBetween(index, prevSpace, node)
      if (door) push(space.floor_id, door)
    }
    push(space.floor_id, space.anchor as XY)
    prevSpace = node
    prevWasDoor = false
  }
  return segments
}

/** A ShowRoute built from an output that looks like a route (ordered space path, length in metres). */
export function routeFromData(data: unknown, index: SceneIndex | null, depth = 0): ShowRoute | null {
  if (!index || !isRec(data) || depth > 2) return null
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
  return {
    kind: 'route',
    segments: segmentsFor(nodes, index),
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

  effect.views = viewCommands(data)
  if (effect.views.length) {
    effect.route = (effect.views.find((v) => v.kind === 'route') as ShowRoute | undefined) ?? null
  } else if (!effect.error && index) {
    effect.route = routeFromData(data, index)
    if (!effect.route && !NO_HIGHLIGHT_TOOLS.test(toolName)) {
      const ids = (isRec(data) && resultIds(data, index)) || idsIn(index, text)
      effect.highlight = ids.slice(0, MAX_HIGHLIGHT)
    }
  }

  if (effect.route) effect.summary = `${Math.round(effect.route.length_m * 10) / 10} m`
  else if (effect.unreachable) effect.summary = 'no route'
  else if (effect.error) effect.summary = 'no match'
  else if (isRec(data) && typeof data.matches === 'number') effect.summary = `${data.matches} found`
  return effect
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
        if (gap < bestGap) {
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
