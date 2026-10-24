// Bridge between live @ai-sdk/react ChatMessage turns (POST /buildings/{id}/chat)
// and the Agent-First Flow state (TraceItem[], ScenarioAsk, ScenarioResult).
import { getToolName, isToolUIPart } from 'ai'
import type { ChatMessage } from '../chat/chats'
import { formatToolArgs, splitAnswer, stripPreferences } from '../chat/text'
import { parseOutput, resolveMessage, toolEffect, type ToolEffect } from '../chat/viewSync'
import {
  buildAddDoorScenario,
  buildLiftClosedScenario,
  computeRoute,
  enrichShowRoute,
  entrancesOf,
  findSpaces,
  floorNameOf,
  fmtM,
  fmtWalk,
  nearestWith,
  solveClientQuery,
  type AskOption,
  type MemoryChip,
  type ScenarioAsk,
  type ScenarioResult,
  type TraceItem,
} from '../../lib/buildingGraph'
import { idsIn, spaceLabel, type SceneIndex } from '../../lib/sceneIndex'

export interface LiveTurnState {
  trace: TraceItem[]
  ask?: ScenarioAsk
  result?: ScenarioResult
  alreadyApplied?: boolean
  prose?: string
}

type Rec = Record<string, unknown>
const isRec = (v: unknown): v is Rec => !!v && typeof v === 'object' && !Array.isArray(v)

function splitQuestionAndWhy(text: string, fallbackQ: string): { q: string; why: string } {
  const clean = text.replace(/\*\*/g, '').trim()
  const sentences = clean.split(/(?<=[.!?])\s+|\n+/).map((s) => s.trim()).filter(Boolean)
  const qIdx = sentences.findIndex((s) => s.endsWith('?'))
  if (qIdx >= 0) {
    const q = sentences[qIdx]
    const rest = sentences.filter((_, i) => i !== qIdx).join(' ')
    return { q, why: rest || 'Pick an option below or type your answer.' }
  }
  return { q: fallbackQ, why: clean || 'Pick an option below.' }
}

export function extractLiveTurn(
  message: ChatMessage | undefined,
  index: SceneIndex | null,
  buildingId: string,
  buildingName: string,
  userQuery: string,
  memory: MemoryChip[],
  closedIds: string[],
  isReady: boolean,
): LiveTurnState {
  if (!message || message.role !== 'assistant') {
    return { trace: [] }
  }

  const trace: TraceItem[] = []
  const effects: ToolEffect[] = []
  const toolRecords: { name: string; input: Rec; data: Rec }[] = []

  for (const part of message.parts) {
    if (!isToolUIPart(part)) continue
    const name = getToolName(part)
    const args = formatToolArgs(part.input, index)
    const done = part.state === 'output-available' || part.state === 'output-error'
    if (!done) {
      trace.push({ tool: name, args, result: '…', running: true })
      continue
    }
    const effect =
      part.state === 'output-available'
        ? toolEffect(name, part.input, part.output, index)
        : toolEffect(name, part.input, undefined, index, part.errorText)
    effects.push(effect)
    const parsed = part.state === 'output-available' ? parseOutput(part.output).data : null
    if (isRec(parsed)) {
      toolRecords.push({ name, input: isRec(part.input) ? part.input : {}, data: parsed })
    }
    trace.push({
      tool: name,
      args,
      result: effect.summary ?? (part.state === 'output-error' ? 'error' : 'done'),
      running: false,
    })
  }

  const rawText = message.parts
    .map((p) => (p.type === 'text' ? p.text : ''))
    .join('\n')
    .trim()
  const { body, answer } = splitAnswer(stripPreferences(rawText).text)

  if (!isReady || !index) {
    return { trace, prose: body || undefined }
  }

  // 1. Check if `add_door` or `what_if(add_door_between=...)` was called
  const doorTool = [...toolRecords]
    .reverse()
    .find((t) => t.name === 'add_door' || (t.name === 'what_if' && Array.isArray(t.input.add_door_between)))
  if (doorTool) {
    const highlightIds = Array.isArray(doorTool.data.highlight)
      ? doorTool.data.highlight.filter((x): x is string => typeof x === 'string')
      : []
    const spaceA = highlightIds[0] ? index.spaceById.get(highlightIds[0]) : undefined
    const spaceB = highlightIds[1] ? index.spaceById.get(highlightIds[1]) : undefined
    const between = Array.isArray(doorTool.input.add_door_between)
      ? doorTool.input.add_door_between
      : Array.isArray(doorTool.input.between)
        ? doorTool.input.between
        : [doorTool.input.first, doorTool.input.second]
    const aStr = spaceA?.name ?? (typeof between[0] === 'string' ? between[0] : 'Seminar room 2.06')
    const bStr = spaceB?.name ?? (typeof between[1] === 'string' ? between[1] : 'Kitchen 2.07')
    const built = buildAddDoorScenario(index, buildingId, buildingName, aStr, bStr)
    if (built?.result) {
      const isDone = doorTool.data.status === 'done'
      return {
        trace,
        alreadyApplied: isDone,
        result: {
          ...built.result,
          understood: body || built.result.understood,
        },
      }
    }
  }

  // 2. Check if `set_closed` or `what_if(close=...)` was called
  const closeTool = [...toolRecords]
    .reverse()
    .find((t) => t.name === 'set_closed' || (t.name === 'what_if' && Array.isArray(t.input.close)))
  if (closeTool && !toolRecords.some((t) => t.name === 'route')) {
    const elemId = typeof closeTool.data.element === 'string' ? closeTool.data.element : undefined
    const conn = elemId ? index.connectorById.get(elemId) : undefined
    const closeTarget =
      conn?.name ??
      (Array.isArray(closeTool.input.close)
        ? String(closeTool.input.close[0] ?? '')
        : typeof closeTool.input.element === 'string'
          ? closeTool.input.element
          : undefined)
    const built = buildLiftClosedScenario(index, buildingId, buildingName, closeTarget)
    if (built?.result) {
      const isDone = closeTool.name === 'set_closed' && closeTool.data.closed === true
      return {
        trace,
        alreadyApplied: isDone,
        result: {
          ...built.result,
          understood: body || built.result.understood,
        },
      }
    }
  }

  // 3. Check if the turn resolved to a route (`route` tool)
  const res = resolveMessage(effects, body, answer, index)
  if (res.routeAt !== null) {
    const showRoute = effects[res.routeAt].route!
    const enriched = enrichShowRoute(index, showRoute)
    const startId = showRoute.space_ids[0]
    const goalId = showRoute.space_ids[showRoute.space_ids.length - 1]
    const startSpace = startId ? index.spaceById.get(startId) : undefined
    const goalSpace = goalId ? index.spaceById.get(goalId) : undefined
    const assetIds = res.highlight.filter((id) => index.assetById.has(id))
    const firstAsset = assetIds[0] ? index.assetById.get(assetIds[0]) : undefined
    const endTitle = firstAsset ? firstAsset.name : goalSpace ? spaceLabel(goalSpace) : 'Route'
    const endFloor = goalSpace ? floorNameOf(index, goalSpace.floor_id) : ''
    const cleanBody = body.replace(/\*\*/g, '').trim()
    const firstSentence =
      cleanBody.split(/(?<=[.!?])\s+|\n+/)[0] ||
      `${startSpace ? spaceLabel(startSpace) : 'Start'} → ${endTitle}`

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
    if (goalSpace) {
      remember.push({ icon: 'map-pin', text: `At ${spaceLabel(goalSpace)}`, spaceId: goalSpace.id })
    }

    const computed =
      startSpace && goalSpace
        ? computeRoute(index, startSpace.id, goalSpace.id, {
            stepFree: stepFreeUsed,
            minWidth: minWidthUsed,
            closed: new Set(closedIds),
          })
        : null

    // Attach multi-turn wheelchair follow-up chip when arriving at Meeting room 1.13
    const isWheelchairTurn1 =
      goalSpace && /1\.13/.test(goalSpace.name) && index.spaceById.size > 0
    const kitch107 = [...index.spaceById.values()].find((s) => /1\.07/.test(s.name))

    return {
      trace,
      result: {
        kind: 'route',
        buildingId,
        buildingName,
        title: endTitle,
        sub: `${endFloor ? `${endFloor} · ` : ''}${fmtM(showRoute.length_m)} · ${fmtWalk(showRoute.length_m)}`,
        understood: firstSentence,
        floors: enriched.floors.length ? enriched.floors : goalSpace ? [goalSpace.floor_id] : [],
        focusFloor: enriched.floors[0] ?? goalSpace?.floor_id ?? index.floors[0]?.id ?? '',
        route: computed ?? {
          start: startId ?? '',
          goal: goalId ?? '',
          nodes: showRoute.space_ids,
          length: showRoute.length_m,
          spaces: showRoute.space_ids,
          connectors: [],
          doors: [],
          segments: showRoute.segments,
          floors: enriched.floors,
          steps: enriched.steps,
          showRoute,
        },
        steps: computed?.steps.length ? computed.steps : enriched.steps,
        say: cleanBody || `${endTitle}, ${fmtWalk(showRoute.length_m)}.`,
        assetIds,
        remember,
        followUp: isWheelchairTurn1 && kitch107 ? `Great, and from there to ${spaceLabel(kitch107)}?` : undefined,
        followLabel: isWheelchairTurn1 && kitch107 ? `Then to ${spaceLabel(kitch107)}` : undefined,
      },
    }
  }

  // 4. If the assistant is asking a clarifying question (e.g. "Where are you right now?" or "Which warehouse zone?")
  const findTools = toolRecords.filter((t) => t.name === 'find' || t.name === 'list_rooms')
  const asksWhereYouAre = /where are you|which entrance|which lobby|starting from|where.*start/i.test(body)
  const hasQuestion = /\?/.test(body)

  if (hasQuestion || (findTools.length > 0 && !toolRecords.some((t) => t.name === 'route'))) {
    const lastFind = findTools[findTools.length - 1]
    const foundHighlightIds = lastFind && Array.isArray(lastFind.data.highlight)
      ? lastFind.data.highlight.filter((id): id is string => typeof id === 'string' && index.spaceById.has(id))
      : []

    if (asksWhereYouAre && foundHighlightIds.length > 0) {
      const targetSet = new Set(foundHighlightIds)
      const seen = new Set<string>()
      const starts = [...findSpaces(index, /^lobby$/i), ...entrancesOf(index).map((e) => e.space)].filter((sp) => {
        if (seen.has(sp.id)) return false
        seen.add(sp.id)
        return true
      })
      const options: AskOption[] = starts.slice(0, 3).flatMap((s) => {
        const near = nearestWith(index, s.id, (sp) => targetSet.has(sp.id))
        if (!near) return []
        const sName = spaceLabel(s)
        return [
          {
            label: sName,
            sub: `${floorNameOf(index, s.floor_id)} · ${s.room_type === 'lobby' ? 'lobby' : 'entrance'}`,
            replyText: `I'm at ${sName}.`,
          },
        ]
      })
      if (options.length > 0) {
        const { q, why } = splitQuestionAndWhy(body, 'Where are you right now?')
        return {
          trace,
          ask: { q, why, options, scan: true },
        }
      }
    }

    // Multiple candidate destinations (e.g., warehouse zones, presses, meeting rooms)
    if (!asksWhereYouAre && foundHighlightIds.length >= 2) {
      const firstFind = findTools.length >= 2 ? findTools[0] : null
      const startIdFromFirstFind =
        firstFind && Array.isArray(firstFind.data.highlight) && typeof firstFind.data.highlight[0] === 'string'
          ? firstFind.data.highlight[0]
          : memory.find((m) => m.spaceId)?.spaceId
      const isForklift = /forklift|warehouse|pallet/i.test(userQuery)
      const stepFree = isForklift || memory.some((m) => m.stepFree) || /wheelchair|step[- ]free/i.test(userQuery)
      const minWidth = isForklift ? 3 : memory.find((m) => m.minWidth)?.minWidth

      const candidateSpaces = foundHighlightIds
        .map((id) => index.spaceById.get(id))
        .filter((s): s is NonNullable<typeof s> => !!s)

      const options: AskOption[] = candidateSpaces.slice(0, 5).map((sp) => {
        const label = spaceLabel(sp)
        const r =
          startIdFromFirstFind && index.spaceById.has(startIdFromFirstFind)
            ? computeRoute(index, startIdFromFirstFind, sp.id, { stepFree, minWidth })
            : null
        const sub = startIdFromFirstFind
          ? r
            ? `${fmtM(r.length)}${isForklift ? ' for the forklift' : ''}`
            : minWidth
              ? 'No opening wide enough'
              : floorNameOf(index, sp.floor_id)
          : `${floorNameOf(index, sp.floor_id)} · ${sp.room_type.replace(/_/g, ' ')}`
        return {
          label,
          sub,
          disabled: Boolean(startIdFromFirstFind && minWidth && !r),
          replyText: label,
        }
      })

      if (options.length >= 2) {
        const { q, why } = splitQuestionAndWhy(body, 'Which one do you mean?')
        return {
          trace,
          ask: { q, why, options },
        }
      }
    }

    // Fallback to client solver for ask options if applicable
    const clientSolved = solveClientQuery(index, buildingId, buildingName, userQuery, memory, closedIds)
    if (clientSolved?.ask) {
      const { q, why } = splitQuestionAndWhy(body, clientSolved.ask.q)
      return {
        trace,
        ask: {
          ...clientSolved.ask,
          q,
          why,
        },
      }
    }
  }

  // 5. Highlighted spaces/assets or general answer card on the map
  const allHighlights =
    res.highlight.length > 0
      ? res.highlight
      : [...new Set(effects.flatMap((e) => e.highlight))].slice(0, 12)
  const writtenIds = idsIn(index, body)
  const focusIds = allHighlights.length > 0 ? allHighlights : writtenIds

  const focusSpaces = focusIds
    .map((id) => index.spaceById.get(id) ?? (index.assetById.get(id) ? index.spaceById.get(index.assetById.get(id)!.space_id) : undefined))
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
      understood: body.replace(/\*\*/g, '') || answer || 'Here is what I found in the building.',
      floors: floors.length ? floors : focusFloor ? [focusFloor] : [],
      focusFloor,
      focusIds: focusIds.length ? focusIds : null,
      assetIds: focusIds.filter((id) => index.assetById.has(id)),
      say: body.replace(/\*\*/g, '') || undefined,
      remember: [],
    },
  }
}
