// Live backend bridge: translates real streamed UIMessage tool calls & prose from the
// `building_guide` backend (`POST /buildings/{id}/chat`) into Agent-First UI state:
//   - Live tool trace (`TraceItem[]`)
//   - Clarifying questions (`ScenarioAsk`) when the agent asks where the user is or which room they mean
//   - Route / Impact / Edit result cards (`ScenarioResult`) built directly from the backend's tool outputs
import { getToolName, isToolUIPart, type UIMessage } from 'ai'
import {
  enrichShowRoute,
  entrancesOf,
  findSpaces,
  floorNameOf,
  fmtM,
  fmtWalk,
  sharedWall,
  type AskOption,
  type MemoryChip,
  type ScenarioAsk,
  type ScenarioResult,
  type StepIcon,
  type TraceItem,
  type WalkStep,
} from '../../lib/buildingGraph'
import { idsIn, spaceLabel, type SceneIndex } from '../../lib/sceneIndex'
import { formatToolArgs, splitAnswer, stripPreferences, toolLabel } from '../chat/text'
import { parseOutput, resolveMessage, toolEffect, type RouteDirection, type ToolEffect } from '../chat/viewSync'

type Rec = Record<string, unknown>
const isRec = (v: unknown): v is Rec => !!v && typeof v === 'object' && !Array.isArray(v)

/** Tools whose result is a walk between places. */
const isRouteTool = (name: string) => name === 'route' || name === 'walk_directions'

const ACTION_ICON: Record<string, StepIcon> = {
  start: 'circle-dot',
  arrive: 'flag',
  left: 'corner-up-left',
  right: 'corner-up-right',
  up: 'arrow-up',
  down: 'arrow-down',
  leave: 'door-open',
}

/** walk_directions steps as the walk view's steps: the backend's own sentences, distances and polylines. */
function walkStepsOf(directions: RouteDirection[], index: SceneIndex, fallbackFloor: string): WalkStep[] {
  return directions.map((d) => ({
    icon: ACTION_ICON[d.action] ?? 'arrow-right',
    text: d.text,
    d: d.distance_m,
    floorId: index.spaceById.get(d.space_id)?.floor_id ?? fallbackFloor,
    spaceId: d.space_id,
    points: d.points.length ? d.points : undefined,
  }))
}

export interface LiveExtraction {
  trace: TraceItem[]
  ask?: ScenarioAsk
  result?: ScenarioResult
  prose?: string
  alreadyApplied?: boolean
}

function formatInputArgs(input: unknown): string {
  if (!isRec(input)) return ''
  return Object.entries(input)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ')
}

function cleanMarkdown(text: string): string {
  return text.replace(/\*\*/g, '').trim()
}

function splitQuestionAndContext(body: string, fallbackQ: string): { q: string; why: string } {
  const cleaned = cleanMarkdown(body)
  if (!cleaned) return { q: fallbackQ, why: '' }
  const lines = cleaned
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean)
  // Find a line that asks the question (contains '?' or starts with "Tell me" / "Where" / "Which")
  const qIdx = lines.findIndex((l) => /\?/.test(l) || /^(tell me|where|which|let me know)/i.test(l))
  if (qIdx >= 0) {
    const q = lines[qIdx]
    const rest = lines.filter((_, i) => i !== qIdx).join('\n')
    return { q, why: rest }
  }
  const sentences = cleaned.split(/(?<=[.!?])\s+/).filter(Boolean)
  const qSentIdx = sentences.findIndex((s) => /\?/.test(s) || /^(tell me|where|which|let me know)/i.test(s))
  if (qSentIdx >= 0) {
    const q = sentences[qSentIdx]
    const rest = sentences.filter((_, i) => i !== qSentIdx).join(' ')
    return { q, why: rest }
  }
  return {
    q: sentences[0] || fallbackQ,
    why: sentences.slice(1).join(' '),
  }
}

export function extractLiveTurn(
  message: UIMessage,
  index: SceneIndex | null,
  buildingId: string,
  buildingName: string,
  userQuery: string,
  isReady: boolean,
): LiveExtraction {
  const trace: TraceItem[] = []
  const effects: ToolEffect[] = []
  const toolRecords: { name: string; input: Rec; data: Rec; effect: ToolEffect }[] = []

  for (const part of message.parts) {
    if (!isToolUIPart(part)) continue
    const name = getToolName(part)
    const done = part.state === 'output-available' || part.state === 'output-error'
    const inputRec = isRec(part.input) ? part.input : {}
    if (!done) {
      trace.push({
        tool: name,
        args: formatInputArgs(part.input),
        result: 'running…',
        running: true,
      })
      continue
    }
    const effect =
      part.state === 'output-available'
        ? toolEffect(name, part.input, part.output, index)
        : toolEffect(name, part.input, undefined, index, part.errorText)
    effects.push(effect)
    const parsed = parseOutput(part.output)
    const dataRec = isRec(parsed.data) ? parsed.data : {}
    toolRecords.push({ name, input: inputRec, data: dataRec, effect })

    trace.push({
      tool: name,
      args: formatToolArgs(part.input, index) || formatInputArgs(part.input) || toolLabel(name, part.input, index),
      result: effect.summary ?? (effect.error ? 'error' : 'done'),
      running: false,
    })
  }

  const rawText = message.parts
    .map((p) => (p.type === 'text' ? p.text : ''))
    .join('\n')
    .trim()
  const { body, answer } = splitAnswer(stripPreferences(rawText).text)
  const cleanBody = cleanMarkdown(body)

  if (!isReady || !index) {
    return { trace, prose: cleanBody || undefined }
  }

  // 1. Check if `add_door` or `what_if(add_door_between=...)` was called by the backend agent
  const doorTool =
    [...toolRecords].reverse().find((t) => t.name === 'add_door') ??
    [...toolRecords].reverse().find((t) => t.name === 'what_if' && Array.isArray(t.input.add_door_between))
  if (doorTool) {
    const highlightIds = Array.isArray(doorTool.data.highlight)
      ? doorTool.data.highlight.filter((x): x is string => typeof x === 'string' && index.spaceById.has(x))
      : []
    const resolveRoomInput = (raw: unknown) => {
      if (typeof raw !== 'string') return undefined
      const q = raw.trim().toLowerCase()
      return (
        index.spaceById.get(raw) ??
        [...index.spaceById.values()].find(
          (s) => s.name.toLowerCase() === q || spaceLabel(s).toLowerCase() === q || q.includes(s.name.toLowerCase()),
        )
      )
    }
    const pairInput = Array.isArray(doorTool.input.add_door_between)
      ? doorTool.input.add_door_between
      : [doorTool.input.first, doorTool.input.second]
    const spaceA =
      resolveRoomInput(pairInput[0]) ?? (highlightIds[0] ? index.spaceById.get(highlightIds[0]) : undefined)
    const spaceB =
      resolveRoomInput(pairInput[1]) ?? (highlightIds[1] ? index.spaceById.get(highlightIds[1]) : undefined)
    const wall = spaceA && spaceB ? sharedWall(index, spaceA.id, spaceB.id) : null
    const doorBetween = Array.isArray(doorTool.data.door_between)
      ? doorTool.data.door_between.filter((x): x is string => typeof x === 'string')
      : [spaceA ? spaceLabel(spaceA) : 'Room A', spaceB ? spaceLabel(spaceB) : 'Room B']
    const widthM =
      typeof doorTool.data.width_m === 'number'
        ? doorTool.data.width_m
        : typeof doorTool.input.width_m === 'number'
          ? doorTool.input.width_m
          : 0.9
    const effectRec = isRec(doorTool.data.effect_on_walks_out_on_that_floor)
      ? doorTool.data.effect_on_walks_out_on_that_floor
      : doorTool.data
    const shorterList = Array.isArray(effectRec.shorter) ? effectRec.shorter.filter(isRec) : []
    const shorterCount =
      typeof effectRec.shorter_by_more_than_1_m === 'number'
        ? effectRec.shorter_by_more_than_1_m
        : shorterList.length
    const stats: { n: string; label: string }[] = []
    if (shorterList.length > 0 && typeof shorterList[0].saves_m === 'number') {
      stats.push({
        n: `−${shorterList[0].saves_m} m`,
        label: `walk out from ${String(shorterList[0].room ?? doorBetween[0])}`,
      })
    }
    if (shorterCount > 0) {
      stats.push({
        n: String(shorterCount),
        label: shorterCount === 1 ? 'room gets a shorter exit walk' : 'rooms get a shorter exit walk',
      })
    }
    // Also check if the agent ran a `route` tool in the same turn to compare before/after distance
    const routeTool = toolRecords.find((t) => isRouteTool(t.name) && typeof t.data.total_length_m === 'number')
    if (stats.length === 0 && routeTool && typeof routeTool.data.total_length_m === 'number') {
      stats.push({
        n: `${fmtM(routeTool.data.total_length_m)}`,
        label: `${doorBetween[0]} ↔ ${doorBetween[1]}`,
      })
    }

    const focusFloor = spaceA?.floor_id ?? spaceB?.floor_id ?? index.floors[0]?.id ?? ''
    const isDone = doorTool.data.status === 'done'
    const isProposed = typeof doorTool.data.status === 'string' && doorTool.data.status.startsWith('proposed')
    const doorFocusIds = [spaceA?.id, spaceB?.id].filter((x): x is string => Boolean(x))

    return {
      trace,
      alreadyApplied: isDone,
      result: {
        kind: 'edit',
        buildingId,
        buildingName,
        title: `New door · ${focusFloor ? floorNameOf(index, focusFloor) : buildingName}`,
        sub: `${doorBetween.join(' ↔ ')} · ${widthM} m · step-free`,
        understood: cleanBody || `${doorBetween.join(' ↔ ')} (${widthM} m door).`,
        stats,
        note: isProposed ? 'Proposed by the agent — confirm to apply to the building session.' : undefined,
        floors: focusFloor ? [focusFloor] : [],
        focusFloor,
        ghostDoor:
          wall && spaceA ? { floorId: spaceA.floor_id, pos: wall.pos, vertical: wall.vertical, widthM } : undefined,
        focusIds: doorFocusIds.length ? doorFocusIds : highlightIds.length ? highlightIds : null,
        confirm: 'Add the door',
        confirmPrompt: 'Yes, please add the door.',
        applied: typeof doorTool.data.change === 'string' ? doorTool.data.change : 'Door added to the building plan.',
        undo: 'Undo',
        say: cleanBody || undefined,
      },
    }
  }

  // 2. Check if `move_item` was called by the backend agent
  const moveTool = [...toolRecords].reverse().find((t) => t.name === 'move_item')
  if (moveTool) {
    const highlightIds = Array.isArray(moveTool.data.highlight)
      ? moveTool.data.highlight.filter((x): x is string => typeof x === 'string' && index.spaceById.has(x))
      : []
    const itemName = typeof moveTool.data.item === 'string' ? moveTool.data.item : 'Equipment'
    const fromRoom = typeof moveTool.data.from === 'string' ? moveTool.data.from : ''
    const toRoom = typeof moveTool.data.to === 'string' ? moveTool.data.to : ''
    const focusSpace = highlightIds[1]
      ? index.spaceById.get(highlightIds[1])
      : highlightIds[0]
        ? index.spaceById.get(highlightIds[0])
        : undefined
    const focusFloor = focusSpace?.floor_id ?? index.floors[0]?.id ?? ''
    const isDone = moveTool.data.status === 'done'
    const isProposed = typeof moveTool.data.status === 'string' && moveTool.data.status.startsWith('proposed')

    return {
      trace,
      alreadyApplied: isDone,
      result: {
        kind: 'edit',
        buildingId,
        buildingName,
        title: `Move ${itemName}`,
        sub: `${fromRoom} → ${toRoom}`,
        understood: cleanBody || `Move ${itemName} from ${fromRoom} to ${toRoom}.`,
        floors: focusFloor ? [focusFloor] : [],
        focusFloor,
        focusIds: highlightIds.length ? highlightIds : null,
        confirm: isProposed || isDone ? 'Confirm move' : undefined,
        confirmPrompt: 'Yes, please move it.',
        applied: typeof moveTool.data.change === 'string' ? moveTool.data.change : `${itemName} moved to ${toRoom}.`,
        undo: 'Undo',
        say: cleanBody || undefined,
      },
    }
  }

  // 3. Check if `set_closed`, `what_if(close=...)`, or `reachable_rooms` (without a route) was called by the backend agent
  const closeTool = [...toolRecords]
    .reverse()
    .find(
      (t) =>
        t.name === 'set_closed' ||
        (t.name === 'what_if' && Array.isArray(t.input.close)) ||
        (t.name === 'reachable_rooms' && Array.isArray(t.input.closed)),
    )
  if (closeTool && !toolRecords.some((t) => isRouteTool(t.name))) {
    const whatIfTool = toolRecords.find((t) => t.name === 'what_if')
    const reachTool = toolRecords.find((t) => t.name === 'reachable_rooms')
    const setClosedTool = toolRecords.find((t) => t.name === 'set_closed')

    const closedIds: string[] = []
    if (setClosedTool && Array.isArray(setClosedTool.data.all_closed)) {
      for (const id of setClosedTool.data.all_closed) {
        if (typeof id === 'string') closedIds.push(id)
      }
    } else if (setClosedTool && typeof setClosedTool.data.element === 'string') {
      closedIds.push(setClosedTool.data.element)
    }

    const closeName =
      (setClosedTool && typeof setClosedTool.input.element === 'string' ? setClosedTool.input.element : undefined) ??
      (Array.isArray(closeTool.input.close) ? String(closeTool.input.close[0] ?? '') : undefined) ??
      (Array.isArray(closeTool.input.closed) ? String(closeTool.input.closed[0] ?? '') : undefined) ??
      'Closure'

    if (closedIds.length === 0) {
      // Match connector or space by name from the scene index
      const conn = [...index.connectorById.values()].find(
        (c) => c.id === closeName || c.name.toLowerCase() === closeName.toLowerCase(),
      )
      if (conn) closedIds.push(conn.id)
    }

    const affectedIds: string[] = []
    const stats: { n: string; label: string }[] = []

    if (whatIfTool) {
      const cutOff = typeof whatIfTool.data.no_longer_reachable === 'number' ? whatIfTool.data.no_longer_reachable : 0
      const longer =
        typeof whatIfTool.data.longer_by_more_than_1_m === 'number' ? whatIfTool.data.longer_by_more_than_1_m : 0
      stats.push({
        n: String(cutOff),
        label: cutOff === 1 ? 'room loses access' : 'rooms lose access',
      })
      if (longer > 0) {
        stats.push({
          n: String(longer),
          label: longer === 1 ? 'room gets a longer walk' : 'rooms get a longer walk',
        })
      }
      if (Array.isArray(whatIfTool.data.highlight)) {
        for (const id of whatIfTool.data.highlight) {
          if (typeof id === 'string' && index.spaceById.has(id)) affectedIds.push(id)
        }
      }
    } else if (reachTool) {
      const unreachableCount =
        typeof reachTool.data.unreachable_count === 'number' ? reachTool.data.unreachable_count : 0
      const reachableCount = typeof reachTool.data.reachable_count === 'number' ? reachTool.data.reachable_count : 0
      stats.push({
        n: String(unreachableCount),
        label: unreachableCount === 1 ? 'room unreachable' : 'rooms unreachable',
      })
      stats.push({
        n: String(reachableCount),
        label: reachableCount === 1 ? 'room still reachable' : 'rooms still reachable',
      })
    }

    const writtenIds = idsIn(index, body).filter((id) => index.spaceById.has(id))
    for (const id of writtenIds) {
      if (!affectedIds.includes(id)) affectedIds.push(id)
    }

    const connObj = closedIds[0] ? index.connectorById.get(closedIds[0]) : undefined
    const focusFloor =
      (affectedIds[0] ? index.spaceById.get(affectedIds[0])?.floor_id : undefined) ??
      connObj?.stops[1]?.[0] ??
      connObj?.stops[0]?.[0] ??
      index.floors[0]?.id ??
      ''
    const isAlreadyClosed = Boolean(setClosedTool && setClosedTool.data.closed === true)

    return {
      trace,
      alreadyApplied: isAlreadyClosed,
      result: {
        kind: 'impact',
        buildingId,
        buildingName,
        title: `${connObj?.name ?? closeName} out of service`,
        sub: focusFloor ? floorNameOf(index, focusFloor) : buildingName,
        understood: cleanBody || `${connObj?.name ?? closeName} impact analysis.`,
        stats,
        floors: focusFloor ? [focusFloor] : [],
        focusFloor,
        closed: closedIds,
        affected: affectedIds,
        focusIds: affectedIds.length ? affectedIds : null,
        confirm: `Mark ${(connObj?.name ?? closeName).toLowerCase()} closed`,
        confirmPrompt: isAlreadyClosed ? 'Yes, keep it closed.' : `Mark ${connObj?.name ?? closeName} as closed.`,
        applied:
          typeof setClosedTool?.data.change === 'string'
            ? setClosedTool.data.change
            : `${connObj?.name ?? closeName} marked closed. All routes now avoid it.`,
        undo: 'Reopen',
        say: cleanBody || undefined,
      },
    }
  }

  // 4. Check if the turn resolved to a route (`route` tool called by the backend agent)
  const res = resolveMessage(effects, body, answer, index)
  if (res.routeAt !== null) {
    const showRoute = effects[res.routeAt].route!
    const enriched = enrichShowRoute(index, showRoute)
    const directions = effects[res.routeAt].directions
    if (directions) enriched.steps = walkStepsOf(directions, index, enriched.floors[0] ?? '')
    const startId = showRoute.space_ids[0]
    const goalId = showRoute.space_ids[showRoute.space_ids.length - 1]
    const startSpace = startId ? index.spaceById.get(startId) : undefined
    const goalSpace = goalId ? index.spaceById.get(goalId) : undefined
    const assetIds = res.highlight.filter((id) => index.assetById.has(id))
    const firstAsset = assetIds[0] ? index.assetById.get(assetIds[0]) : undefined
    const endTitle = firstAsset ? firstAsset.name : goalSpace ? spaceLabel(goalSpace) : 'Route'
    const endFloor = goalSpace ? floorNameOf(index, goalSpace.floor_id) : ''

    const stepFreeUsed =
      /step[- ]free|wheelchair|no stairs|without stairs/i.test(userQuery) ||
      toolRecords.some((t) => t.input.step_free === true)
    const minWidthUsed = toolRecords.find((t) => typeof t.input.min_width_m === 'number')?.input
      .min_width_m as number | undefined
    const voiceWanted = /short|hands full|spoken|headset/i.test(userQuery)

    const remember: MemoryChip[] = []
    if (stepFreeUsed && !minWidthUsed) {
      remember.push({ icon: 'accessibility', text: 'Step-free routes', stepFree: true })
    }
    if (minWidthUsed && minWidthUsed >= 2) {
      remember.push({
        icon: 'forklift',
        text: `Forklift · ${minWidthUsed} m openings`,
        stepFree: true,
        minWidth: minWidthUsed,
      })
    }
    if (voiceWanted) {
      remember.push({ icon: 'volume-2', text: 'Short spoken answers', voice: true })
    }
    // The destination is not where you are yet: the UI remembers it as your place only once you arrive.

    // A route across floors is easier to follow seen from the side: show it tilted, from its highest floor so
    // the floors below (and the route on them) stay in view.
    const routeFloors = enriched.floors.length ? enriched.floors : goalSpace ? [goalSpace.floor_id] : []
    const topFloor = [...routeFloors].sort(
      (x, y) => (index.floorById.get(y)?.elevation_m ?? 0) - (index.floorById.get(x)?.elevation_m ?? 0),
    )[0]
    const acrossFloors = routeFloors.length > 1

    return {
      trace,
      result: {
        kind: 'route',
        buildingId,
        buildingName,
        title: endTitle,
        sub: `${endFloor ? `${endFloor} · ` : ''}${fmtM(showRoute.length_m)} · ${fmtWalk(showRoute.length_m)}`,
        understood: cleanBody || `${startSpace ? spaceLabel(startSpace) : 'Start'} → ${endTitle}`,
        floors: enriched.floors.length ? enriched.floors : goalSpace ? [goalSpace.floor_id] : [],
        focusFloor: (acrossFloors ? topFloor : enriched.floors[0]) ?? goalSpace?.floor_id ?? index.floors[0]?.id ?? '',
        view3d: acrossFloors ? { link: null } : undefined,
        route: {
          start: startId ?? '',
          goal: goalId ?? '',
          length: showRoute.length_m,
          floors: enriched.floors,
          steps: enriched.steps,
          showRoute,
        },
        steps: enriched.steps,
        say: cleanBody || `${endTitle}, ${fmtWalk(showRoute.length_m)}.`,
        assetIds,
        remember,
      },
    }
  }

  // 4b. Rooms measured through the floors (`distance_3d`) or stacked on top of each other (`room_details` asked
  // about above/below): the answer is shown tilted in 3D, both rooms lit and joined by a line.
  const vertical = verticalPair(toolRecords, index, userQuery)
  if (vertical) {
    const [a, b] = [vertical.from, vertical.to].map((id) => index.spaceById.get(id)!)
    const elev = (fid: string) => index.floorById.get(fid)?.elevation_m ?? 0
    const top = elev(a.floor_id) >= elev(b.floor_id) ? a.floor_id : b.floor_id
    const title = vertical.distance !== null ? `${spaceLabel(a)} to ${spaceLabel(b)}` : spaceLabel(b)
    return {
      trace,
      result: {
        kind: 'impact',
        buildingId,
        buildingName,
        title,
        sub: [...new Set([a.floor_id, b.floor_id])].map((f) => floorNameOf(index, f)).join(' · '),
        understood: cleanBody || answer || title,
        floors: [...new Set([top, a.floor_id, b.floor_id])],
        focusFloor: top,
        focusIds: [a.id, b.id],
        say: cleanBody || undefined,
        remember: [],
        view3d: {
          link:
            vertical.distance !== null
              ? { from: a.id, to: b.id, label: `${Math.round(vertical.distance * 10) / 10} m`, labelAt: 'mid' }
              : { from: a.id, to: b.id, label: spaceLabel(b), labelAt: 'to' },
        },
      },
    }
  }

  // 5. Check if the backend agent is asking a clarifying question (e.g. "Where are you?" or "Which warehouse zone?")
  const asksWhereYouAre =
    /where are you|which room|which floor|which entrance|which lobby|starting from|where.*start|tell me which room/i.test(
      cleanBody,
    )
  const asksWhichOne = /which one|which of|which warehouse|which zone|which meeting|which office|\?/i.test(cleanBody)

  if (asksWhereYouAre && !toolRecords.some((t) => isRouteTool(t.name))) {
    const seen = new Set<string>()
    const starts = [...findSpaces(index, /^lobby$/i), ...entrancesOf(index).map((e) => e.space)].filter((sp) => {
      if (seen.has(sp.id)) return false
      seen.add(sp.id)
      return true
    })
    const options: AskOption[] = starts.slice(0, 4).map((s) => {
      const sName = spaceLabel(s)
      const fName = floorNameOf(index, s.floor_id)
      return {
        label: sName,
        sub: `${fName} · ${s.room_type === 'lobby' ? 'lobby' : 'entrance'}`,
        replyText: `I am in ${s.name} (${fName}).`,
        spaceId: s.id,
      }
    })
    if (options.length > 0) {
      const { q, why } = splitQuestionAndContext(cleanBody, 'Where are you right now?')
      return {
        trace,
        ask: { q, why, options, scan: true },
      }
    }
  }

  if (asksWhichOne && !asksWhereYouAre && !toolRecords.some((t) => isRouteTool(t.name))) {
    // Extract candidate rooms returned by the backend's `find`, `list_rooms`, or `nearest` tools, or mentioned in the text
    const findTools = toolRecords.filter((t) => t.name === 'find' || t.name === 'list_rooms' || t.name === 'nearest')
    const lastFind = findTools[findTools.length - 1]
    const highlightIds =
      lastFind && Array.isArray(lastFind.data.highlight)
        ? lastFind.data.highlight.filter((id): id is string => typeof id === 'string' && index.spaceById.has(id))
        : idsIn(index, body).filter((id) => index.spaceById.has(id))

    const candidateSpaces = highlightIds
      .map((id) => index.spaceById.get(id))
      .filter((s): s is NonNullable<typeof s> => !!s)

    if (candidateSpaces.length >= 2) {
      const options: AskOption[] = candidateSpaces.slice(0, 5).map((sp) => {
        const label = spaceLabel(sp)
        const fName = floorNameOf(index, sp.floor_id)
        return {
          label,
          sub: `${fName} · ${sp.room_type.replace(/_/g, ' ')}`,
          replyText: `${sp.name} (${fName})`,
        }
      })
      const { q, why } = splitQuestionAndContext(cleanBody, 'Which one do you mean?')
      return {
        trace,
        ask: { q, why, options },
      }
    }
  }

  // 6. Highlighted spaces/assets or general answer card from the backend agent
  const allHighlights =
    res.highlight.length > 0
      ? res.highlight
      : [...new Set(effects.flatMap((e) => e.highlight))].slice(0, 12)
  const writtenIds = idsIn(index, body)
  const focusIds = allHighlights.length > 0 ? allHighlights : writtenIds

  const focusSpaces = focusIds
    .map(
      (id) =>
        index.spaceById.get(id) ??
        (index.assetById.get(id) ? index.spaceById.get(index.assetById.get(id)!.space_id) : undefined),
    )
    .filter((s): s is NonNullable<typeof s> => !!s)

  const focusFloor = focusSpaces[0]?.floor_id ?? index.floors[0]?.id ?? ''
  const floors = [...new Set(focusSpaces.map((s) => s.floor_id))]
  const title =
    focusSpaces.length === 1
      ? spaceLabel(focusSpaces[0])
      : focusSpaces.length > 1
        ? `${focusSpaces.length} places found`
        : answer
          ? `Answer: ${answer}`
          : 'Building guide'

  return {
    trace,
    result: {
      kind: 'impact',
      buildingId,
      buildingName,
      title,
      sub: focusFloor ? floorNameOf(index, focusFloor) : buildingName,
      understood: cleanBody || answer || 'Here is what I found in the building.',
      floors: floors.length ? floors : focusFloor ? [focusFloor] : [],
      focusFloor,
      focusIds: focusIds.length ? focusIds : null,
      assetIds: focusIds.filter((id) => index.assetById.has(id)),
      say: cleanBody || undefined,
      remember: [],
    },
  }
}

const ABOVE = /\b(above|upstairs|on top of|over(?=\s+(it|the|this|that|room|\w+\s\d)))\b/i
const BELOW = /\b(below|beneath|under(neath)?|downstairs)\b/i

/** The two rooms of a vertical answer, from the tools the agent used (never from the wording of its reply). */
function verticalPair(
  records: { name: string; input: Rec; data: Rec }[],
  index: SceneIndex,
  userQuery: string,
): { from: string; to: string; distance: number | null } | null {
  const isSpace = (v: unknown): v is string => typeof v === 'string' && index.spaceById.has(v)
  const last = [...records].reverse()
  const dist = last.find((t) => t.name === 'distance_3d' && typeof t.data.distance_m === 'number')
  if (dist) {
    const ids = Array.isArray(dist.data.highlight) ? dist.data.highlight.filter(isSpace) : []
    if (ids.length === 2 && ids[0] !== ids[1]) return { from: ids[0], to: ids[1], distance: dist.data.distance_m as number }
  }
  const above = ABOVE.test(userQuery)
  const below = BELOW.test(userQuery)
  if (above === below) return null
  const room = last.find((t) => t.name === 'room_details' && isSpace(t.data.id))
  const other = room?.data[above ? 'directly_above_id' : 'directly_below_id']
  return room && isSpace(other) ? { from: room.data.id as string, to: other, distance: null } : null
}
