// Pure text helpers for the chat: step-free suffix, the benchmark "Answer:" line, ids -> names, tool chip labels.
import type { SceneIndex } from '../../lib/sceneIndex'
import { spaceLabel } from '../../lib/sceneIndex'

// ---- step-free & session memory preferences -------------------------------------------------------------

/** Machine-readable suffix appended to the outgoing user text when the step-free chip is on. */
export const STEP_FREE_SUFFIX = '\n\n(Preference: step-free routes only.)'
const CONTEXT_PREFIX = '\n\n(Remembered context:'

export function withPreferences(text: string, stepFree: boolean, extraContext?: string[]): string {
  const parts: string[] = []
  if (stepFree) parts.push('step-free routes only')
  if (extraContext?.length) parts.push(...extraContext)
  if (!parts.length) return text
  if (parts.length === 1 && parts[0] === 'step-free routes only') return text + STEP_FREE_SUFFIX
  return `${text}${CONTEXT_PREFIX} ${parts.join('; ')}.)`
}

/** The text a user bubble shows, and whether the step-free preference was attached. */
export function stripPreferences(text: string): { text: string; stepFree: boolean } {
  let clean = text
  let stepFree = false
  const ctxIdx = clean.lastIndexOf(CONTEXT_PREFIX.trim())
  if (ctxIdx !== -1) {
    const suffix = clean.slice(ctxIdx)
    if (/step-free/i.test(suffix)) stepFree = true
    clean = clean.slice(0, ctxIdx).trimEnd()
  }
  const i = clean.lastIndexOf(STEP_FREE_SUFFIX.trim())
  if (i !== -1) {
    stepFree = true
    clean = clean.slice(0, i).trimEnd()
  }
  return { text: clean, stepFree }
}

// ---- "Answer: ..." line ----------------------------------------------------------------------------------

// "Answer: 46.0", "**Answer:** 46 m", "Answer: **Kitchen 0.33**"
const ANSWER_LINE = /^[ \t>*_]*answer[ \t*_]*:[ \t*_]*(.*?)[ \t*_]*$/i

/** Remove the last `Answer: ...` line from prose and return its value separately. */
export function splitAnswer(text: string): { body: string; answer: string | null } {
  const lines = text.split('\n')
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = lines[i].match(ANSWER_LINE)
    if (m) {
      lines.splice(i, 1)
      return { body: lines.join('\n').trimEnd(), answer: m[1].replace(/\*\*/g, '').trim() || null }
    }
  }
  return { body: text, answer: null }
}

// ---- ids -> human names ----------------------------------------------------------------------------------

const ID = String.raw`[A-Za-z0-9_.:-]+-(?:F-?\d+-S\d+|A\d+|F-?\d+-O\d+|V\d+)`
const BRACKETED_ID = new RegExp(String.raw`[ \t]?[\[(](?:id:?\s*)?(${ID})[\])]`, 'g')
const BARE_ID = new RegExp(String.raw`\b${ID}\b`, 'g')

/** Name for any known id (space, asset, connector, opening); undefined when unknown. */
export function labelOf(index: SceneIndex | null, id: string): string | undefined {
  if (!index) return undefined
  const space = index.spaceById.get(id)
  if (space) return spaceLabel(space)
  const asset = index.assetById.get(id)
  if (asset) return asset.name
  const connector = index.connectorById.get(id)
  if (connector) return connector.name
  const opening = index.openingById.get(id)
  if (opening) return opening.kind === 'door' ? 'door' : opening.kind
  return undefined
}

/** "Hall access 0.19 [proc-...-F0-S019]" -> "Hall access 0.19"; bare ids become names when known. */
export function prettifyIds(text: string, index: SceneIndex | null): string {
  return text.replace(BRACKETED_ID, '').replace(BARE_ID, (id) => labelOf(index, id) ?? id)
}

/** A tool argument that may be an id or a name: show the name. */
export function placeName(value: unknown, index: SceneIndex | null): string {
  if (typeof value !== 'string') return '?'
  return labelOf(index, value) ?? prettifyIds(value, index)
}

// ---- numbers ---------------------------------------------------------------------------------------------

export const WALKING_SPEED_MPS = 1.2

export function formatMetres(m: number): string {
  return m >= 100 ? `${Math.round(m)} m` : `${Math.round(m * 10) / 10} m`
}

export function formatWalk(m: number): string {
  const s = m / WALKING_SPEED_MPS
  if (s < 60) return `${Math.max(1, Math.round(s / 5) * 5)} s`
  return `${Math.round(s / 60)} min`
}

// ---- tool chips & trace formatting -----------------------------------------------------------------------

function humanizeName(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

/** Format raw tool input as readable `key="val", ...` for the live tool-call trace box. */
export function formatToolArgs(rawInput: unknown, index: SceneIndex | null): string {
  const input = asRecord(rawInput)
  const parts: string[] = []
  for (const [k, v] of Object.entries(input)) {
    if (v === undefined || v === null || v === false || (Array.isArray(v) && v.length === 0)) continue
    if (typeof v === 'string') {
      parts.push(`${k}="${placeName(v, index)}"`)
    } else if (Array.isArray(v)) {
      parts.push(`${k}=[${v.map((x) => (typeof x === 'string' ? `"${placeName(x, index)}"` : String(x))).join(', ')}]`)
    } else {
      parts.push(`${k}=${String(v)}`)
    }
  }
  return parts.join(', ')
}

/** "Searched: 3d printer", "Route: Lobby → Kitchen 0.33 · step-free"... what the agent understood. */
const lower = (v: unknown) => humanizeName(String(v ?? '')).toLowerCase()
const list = (v: unknown, index: SceneIndex | null) =>
  Array.isArray(v) ? v.map((x) => placeName(x, index)).join(', ') : ''

export function toolLabel(toolName: string, rawInput: unknown, index: SceneIndex | null): string {
  const input = asRecord(rawInput)
  const sf = input.step_free ? ' · step-free' : ''
  const width = typeof input.min_width_m === 'number' ? ` · ≥ ${input.min_width_m} m wide` : ''
  const floor = input.floor !== undefined && input.floor !== null ? ` · floor ${input.floor}` : ''
  switch (toolName) {
    // ---- building_guide tools (src/app/agents/building_guide/tools.py on main)
    case 'find':
      return `Searched: ${input.query ?? '…'}${floor}`
    case 'route': {
      if (input.start === undefined && input.destination === undefined) return 'Route'
      const via = Array.isArray(input.via) && input.via.length ? ` via ${list(input.via, index)}` : ''
      return `Route: ${placeName(input.start, index)} → ${placeName(input.destination, index)}${via}${sf}${width}`
    }
    case 'nearest':
      return `Nearest ${lower(input.item_type ?? input.room_type ?? 'place')} from ${placeName(input.start, index)}${sf}`
    case 'room_details':
      return `Room details: ${placeName(input.room, index)}`
    case 'list_rooms':
      return `Rooms${input.room_type ? `: ${lower(input.room_type)}` : ''}${floor}`
    case 'count_items':
      return `Counted: ${lower(input.item_type)}${floor}`
    case 'distance_3d':
      return `Straight line: ${placeName(input.first, index)} ↔ ${placeName(input.second, index)}`
    case 'reachable_rooms': {
      const closed = Array.isArray(input.closed) && input.closed.length ? ` · without ${list(input.closed, index)}` : ''
      return `Reachable${floor} from ${placeName(input.start, index)}${sf}${width}${closed}`
    }
    case 'what_if': {
      const door = Array.isArray(input.add_door_between) && input.add_door_between.length === 2
        ? `new door ${placeName(input.add_door_between[0], index)} ↔ ${placeName(input.add_door_between[1], index)}`
        : ''
      const closed = Array.isArray(input.close) && input.close.length ? `closed ${list(input.close, index)}` : ''
      return `What if: ${[door, closed].filter(Boolean).join(', ') || 'change'}${floor}${sf}`
    }
    // ---- starter-kit tools (kept so old chats still render)
    case 'search_building': {
      const floor = typeof input.floor_level === 'number' ? ` · floor ${input.floor_level}` : ''
      return `Searched: ${input.query ?? '…'}${floor}`
    }
    case 'find_route': {
      const via = Array.isArray(input.via) && input.via.length
        ? ` via ${input.via.map((v) => placeName(v, index)).join(', ')}`
        : ''
      const sf = input.step_free ? ' · step-free' : ''
      if (input.start === undefined && input.destination === undefined) return 'Route'
      return `Route: ${placeName(input.start, index)} → ${placeName(input.destination, index)}${via}${sf}`
    }
    case 'set_closed':
      return `${input.closed === false ? 'Reopened' : 'Closed'}: ${placeName(input.element, index)}`
    case 'add_door':
      return `New door: ${placeName(input.first ?? input.first_space, index)} ↔ ${placeName(input.second ?? input.second_space, index)}`
    case 'building_overview':
      return 'Checked building overview'
    case 'show_route':
      return 'Drew the route'
  }
  const firstString = Object.values(input).find((v) => typeof v === 'string')
  return firstString ? `${humanizeName(toolName)}: ${placeName(firstString, index)}` : humanizeName(toolName)
}
