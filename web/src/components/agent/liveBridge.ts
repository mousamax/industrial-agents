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
  floorNameOf,
  fmtM,
  fmtWalk,
  solveClientQuery,
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
  prose?: string
}

type Rec = Record<string, unknown>
const isRec = (v: unknown): v is Rec => !!v && typeof v === 'object' && !Array.isArray(v)

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

  // 1. Check if a what_if / add_door / set_closed tool was used
  const doorTool = toolRecords.find(
    (t) => t.name === 'add_door' || (t.name === 'what_if' && Array.isArray(t.input.add_door_between)),
  )
  if (doorTool) {
    const between = (doorTool.input.between ?? doorTool.input.add_door_between) as unknown
    if (Array.isArray(between) && between.length >= 2) {
      const aStr = String(between[0])
      const bStr = String(between[1])
      const built = buildAddDoorScenario(index, buildingId, buildingName, aStr, bStr)
      if (built?.result) {
        return {
          trace,
          result: {
            ...built.result,
            understood: body || built.result.understood,
          },
        }
      }
    }
  }

  const closeTool = toolRecords.find(
    (t) => t.name === 'set_closed' || (t.name === 'what_if' && Array.isArray(t.input.close)),
  )
  if (closeTool) {
    const closeTarget = Array.isArray(closeTool.input.close)
      ? String(closeTool.input.close[0] ?? '')
      : typeof closeTool.input.element === 'string'
        ? closeTool.input.element
        : undefined
    const built = buildLiftClosedScenario(index, buildingId, buildingName, closeTarget)
    if (built?.result) {
      return {
        trace,
        result: {
          ...built.result,
          understood: body || built.result.understood,
        },
      }
    }
  }

  // 2. Check if the turn resolved to a route
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
    const firstSentence = body.split(/(?<=[.!?])\s+/)[0] || `${startSpace ? spaceLabel(startSpace) : 'Start'} → ${endTitle}`
    const stepFreeUsed =
      /step[- ]free|wheelchair|lift|elevator/i.test(userQuery) ||
      toolRecords.some((t) => t.input.step_free === true)
    const remember: MemoryChip[] = []
    if (stepFreeUsed) remember.push({ icon: 'accessibility', text: 'Step-free routes', stepFree: true })
    if (goalSpace) remember.push({ icon: 'map-pin', text: `At ${spaceLabel(goalSpace)}`, spaceId: goalSpace.id })

    // Also compute a full ComputedRoute if start & goal exist so route.connectors/doors are populated
    const computed =
      startSpace && goalSpace
        ? computeRoute(index, startSpace.id, goalSpace.id, {
            stepFree: stepFreeUsed,
            closed: new Set(closedIds),
          })
        : null

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
        say: body || `${endTitle}, ${fmtWalk(showRoute.length_m)}.`,
        assetIds,
        remember,
      },
    }
  }

  // 3. If the assistant is asking a question (e.g. "Where are you?" or "Which one?") or we can offer options
  if (/\?/.test(body)) {
    const clientSolved = solveClientQuery(index, buildingId, buildingName, userQuery, memory, closedIds)
    if (clientSolved?.ask) {
      return {
        trace,
        ask: {
          ...clientSolved.ask,
          why: body,
        },
      }
    }
  }

  // 4. Highlighted spaces/assets or general answer card on the map
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
      understood: body || answer || 'Here is what I found in the building.',
      floors: floors.length ? floors : focusFloor ? [focusFloor] : [],
      focusFloor,
      focusIds: focusIds.length ? focusIds : null,
      assetIds: focusIds.filter((id) => index.assetById.has(id)),
      say: body || undefined,
      remember: [],
    },
  }
}
