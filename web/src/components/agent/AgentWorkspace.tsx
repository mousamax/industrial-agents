// Agent-First workspace implementing Claude Design's AgentPhone.dc.html & AgentDesktop.dc.html.
// Replaces the 12 friction points:
// - Opens on one question ("What do you need?"); the map loads with the answer
// - Remembers location & constraints ("I remember" chips) across turns
// - Tappable option cards with constrained distances + QR door sign scan
// - Full-screen step-by-step Walk Mode with left/right corridor turns & floor switching
// - Voice input (Listening…) & spoken answers (Read aloud)
// - Safe preview -> confirm -> undo for map changes (lift closure, new door)
// - Works both with the 7 cross-building pitch scenarios and live POST /buildings/{id}/chat queries
import { getToolName, isToolUIPart } from 'ai'
import clsx from 'clsx'
import {
  Accessibility,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Building2,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Coffee,
  Compass,
  CornerUpLeft,
  CornerUpRight,
  DoorOpen,
  Flag,
  Forklift,
  Map as MapIcon,
  MapPin,
  Mic,
  Navigation,
  QrCode,
  RotateCcw,
  Sparkles,
  TriangleAlert,
  Volume2,
  Wrench,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  buildStarterPrompts,
  floorNameOf,
  fmtM,
  fmtWalk,
  type AskOption,
  type MemoryChip,
  type Scenario,
  type ScenarioAsk,
  type ScenarioResult,
  type StepIcon,
  type TraceItem,
  type WalkStep,
} from '../../lib/buildingGraph'
import type { SceneIndex } from '../../lib/sceneIndex'
import { useApp } from '../../store'
import { BuildingPicker } from '../browse/BuildingPicker'
import { useBrowseUi } from '../browse/browseStore'
import { getBuildingChat, resetChat, useBuildingChat, type ChatMessage } from '../chat/chats'
import { splitAnswer, stripPreferences, withPreferences } from '../chat/text'
import { MapView } from '../map/MapView'
import { mapLabel, shapeAnswer } from './assistantCopy'
import { AssistantView } from './AssistantView'
import { extractLiveTurn } from './liveBridge'

/** One finished exchange of the conversation, shown above the current answer. */
export interface PastTurn {
  user: string
  reply: string
}

/** The finished exchanges in a chat, oldest first, without the one still on screen (the last question). */
function pastTurns(messages: ChatMessage[]): PastTurn[] {
  const turns: PastTurn[] = []
  for (const m of messages) {
    const text = m.parts
      .map((p) => (p.type === 'text' ? p.text : ''))
      .join('')
      .trim()
    if (m.role === 'user') turns.push({ user: stripPreferences(text).text, reply: '' })
    else if (m.role === 'assistant' && turns.length) {
      const last = turns[turns.length - 1]
      last.reply = [last.reply, splitAnswer(text).body].filter(Boolean).join('\n\n')
    }
  }
  return turns.slice(0, -1).filter((t) => t.user && t.reply)
}

/** Tools that change the drafted plan itself (Level 4): the map must reload the corrected building. */
const PLAN_EDITS = new Set(['fix_draft', 'draft_building'])

/** `?ui=classic` keeps the first agent-first layout (sidebar + Google palette) reachable for comparison. */
const CLASSIC = new URLSearchParams(window.location.search).get('ui') === 'classic'

export type FlowPhase = 'home' | 'listening' | 'working' | 'ask' | 'result' | 'walk'

function StepIconGlyph({ icon, className }: { icon: StepIcon; className?: string }) {
  switch (icon) {
    case 'circle-dot':
      return <CircleDot className={className} aria-hidden />
    case 'arrow-up':
      return <ArrowUp className={className} aria-hidden />
    case 'arrow-down':
      return <ArrowDown className={className} aria-hidden />
    case 'corner-up-left':
      return <CornerUpLeft className={className} aria-hidden />
    case 'corner-up-right':
      return <CornerUpRight className={className} aria-hidden />
    case 'door-open':
      return <DoorOpen className={className} aria-hidden />
    case 'flag':
      return <Flag className={className} aria-hidden />
    default:
      return <ArrowRight className={className} aria-hidden />
  }
}

function ScenarioIconGlyph({ icon, className }: { icon: Scenario['icon']; className?: string }) {
  switch (icon) {
    case 'coffee':
      return <Coffee className={className} aria-hidden />
    case 'accessibility':
      return <Accessibility className={className} aria-hidden />
    case 'wrench':
      return <Wrench className={className} aria-hidden />
    case 'forklift':
      return <Forklift className={className} aria-hidden />
    case 'triangle-alert':
      return <TriangleAlert className={className} aria-hidden />
    case 'door-open':
      return <DoorOpen className={className} aria-hidden />
  }
}

function MemoryIconGlyph({ icon, className }: { icon: MemoryChip['icon']; className?: string }) {
  switch (icon) {
    case 'accessibility':
      return <Accessibility className={className} aria-hidden />
    case 'forklift':
      return <Forklift className={className} aria-hidden />
    case 'volume-2':
      return <Volume2 className={className} aria-hidden />
    default:
      return <MapPin className={className} aria-hidden />
  }
}

export function AgentWorkspace({ phone }: { phone: boolean }) {
  const buildingId = useApp((s) => s.buildingId)
  const scene = useApp((s) => s.scene)
  const index = useApp((s) => s.index)
  const buildings = useApp((s) => s.buildings)
  const loadBuilding = useApp((s) => s.loadBuilding)
  const setFloor = useApp((s) => s.setFloor)
  const setRoute = useApp((s) => s.setRoute)
  const setHighlight = useApp((s) => s.setHighlight)
  const setView3d = useApp((s) => s.setView3d)
  const setMapOverlays = useApp((s) => s.setMapOverlays)
  const setMapInsets = useApp((s) => s.setMapInsets)
  const setMode = useApp((s) => s.setMode)
  const memory = useApp((s) => s.memory)
  const remember = useApp((s) => s.remember)
  const forgetOne = useApp((s) => s.forgetOne)
  const forgetAll = useApp((s) => s.forgetAll)
  const pendingPrompt = useApp((s) => s.pendingPrompt)
  const consumePrompt = useApp((s) => s.consumePrompt)
  const setPickerOpen = useBrowseUi((s) => s.setPickerOpen)

  const activeBuildingName = useMemo(() => {
    if (buildingId === 'proc-campus-dev-000') return 'Office campus'
    if (buildingId === 'proc-factory-dev-003') return 'Factory & office'
    return scene?.name ?? buildings.find((b) => b.id === buildingId)?.name ?? 'Building'
  }, [buildingId, scene?.name, buildings])

  const starterList = useMemo(
    () => buildStarterPrompts(),
    [],
  )

  // Flow state
  const [phase, setPhase] = useState<FlowPhase>('home')
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [userText, setUserText] = useState('')
  const [replyText, setReplyText] = useState<string | null>(null)
  const [trace, setTrace] = useState<TraceItem[]>([])
  const [traceOpen, setTraceOpen] = useState(false)
  const [ask, setAsk] = useState<ScenarioAsk | null>(null)
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [floor, setFloorState] = useState<string | null>(null)
  const [stepIdx, setStepIdx] = useState(0)
  const [speaking, setSpeaking] = useState(false)
  const [applied, setApplied] = useState(false)
  const [input, setInput] = useState('')
  const [listenText, setListenText] = useState('')
  const [liveMode, setLiveMode] = useState(false)

  // Subscribe directly to the active building's Chat instance so switching buildings never drops listeners
  const chat = useBuildingChat(buildingId)
  const [liveMessages, setLiveMessages] = useState<ChatMessage[]>([])
  const [liveStatus, setLiveStatus] = useState<string>('ready')
  const [liveError, setLiveError] = useState<Error | undefined>(undefined)

  useEffect(() => {
    if (!chat) {
      setLiveMessages([])
      setLiveStatus('ready')
      setLiveError(undefined)
      return
    }
    const sync = () => {
      setLiveMessages([...chat.messages])
      setLiveStatus(chat.status)
      setLiveError(chat.error)
    }
    sync()
    const unsub = [
      chat['~registerMessagesCallback'](sync),
      chat['~registerStatusCallback'](sync),
      chat['~registerErrorCallback'](sync),
    ]
    return () => unsub.forEach((u) => u())
  }, [chat])

  const history = useMemo(() => pastTurns(liveMessages), [liveMessages])

  const clearLiveError = useCallback(() => {
    chat?.clearError()
    setLiveError(undefined)
  }, [chat])

  // Speech cleanup
  const stopSpeech = useCallback(() => {
    setSpeaking(false)
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel()
      } catch {
        // ignore
      }
    }
  }, [])

  const speakText = useCallback((text: string | undefined) => {
    if (!text) return
    setSpeaking(true)
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel()
        const u = new SpeechSynthesisUtterance(text)
        u.rate = 1.02
        u.onend = () => setSpeaking(false)
        u.onerror = () => setSpeaking(false)
        window.speechSynthesis.speak(u)
      } catch {
        // Fallback visual waveform remains active
      }
    }
  }, [])

  useEffect(() => {
    return () => {
      stopSpeech()
    }
  }, [stopSpeech])

  // Apply result helper
  const showResult = useCallback(
    (res: ScenarioResult, autoSpeak = false, initialApplied = false) => {
      if (res.buildingId && res.buildingId !== useApp.getState().buildingId) {
        void loadBuilding(res.buildingId)
      }
      if (res.remember?.length) {
        remember(res.remember)
      }
      const wantSpeak = autoSpeak || memory.some((m) => m.voice) || res.remember?.some((m) => m.voice)
      setPhase('result')
      setResult(res)
      setFloorState(res.focusFloor)
      setStepIdx(0)
      setApplied(initialApplied)
      if (wantSpeak && res.say) {
        speakText(res.say)
      } else {
        stopSpeech()
      }
    },
    [loadBuilding, remember, memory, speakText, stopSpeech],
  )

  // Send a prompt to the live building_guide backend (POST /buildings/{id}/chat)
  const prevAssistantCountRef = useRef<number>(0)
  const sendToBackend = useCallback(
    async (
      text: string,
      targetBuildingId?: string,
      matchedScenario?: Scenario | null,
      isReply = false,
    ) => {
      stopSpeech()
      const bId = targetBuildingId ?? useApp.getState().buildingId ?? 'proc-campus-dev-000'
      if (bId !== useApp.getState().buildingId) {
        await loadBuilding(bId)
      }
      const targetChat = getBuildingChat(bId)
      if (targetChat.status === 'submitted' || targetChat.status === 'streaming') {
        await targetChat.stop()
      }
      clearLiveError()
      prevAssistantCountRef.current = targetChat.messages.filter((m) => m.role === 'assistant').length
      if (!isReply) {
        setScenario(matchedScenario ?? null)
        setPhase('working')
        setUserText(text)
        setReplyText(null)
        setTrace([])
        setTraceOpen(false)
        setAsk(null)
        setResult(null)
        setApplied(false)
        setInput('')
      } else {
        setPhase('working')
        setReplyText(text)
      }
      setInput('')
      setLiveMode(true)
      const currentMem = useApp.getState().memory
      void targetChat.sendMessage({
        text: withPreferences(
          text,
          currentMem.some((m) => m.stepFree),
          currentMem.map((m) => m.text),
        ),
      })
    },
    [stopSpeech, loadBuilding, clearLiveError],
  )

  // Run a starter prompt card by sending its prompt directly to the live backend agent
  const runScenario = useCallback(
    (sc: Scenario) => {
      void sendToBackend(sc.user, sc.buildingId, sc, false)
    },
    [sendToBackend],
  )

  // Watch live chat messages when in liveMode
  useEffect(() => {
    if (!liveMode) return
    const isReady = liveStatus === 'ready'
    if (liveError) {
      setLiveMode(false)
      setPhase('result')
      setResult({
        kind: 'impact',
        buildingId: buildingId ?? '',
        buildingName: activeBuildingName,
        title: 'Backend connection error',
        sub: activeBuildingName,
        understood: liveError.message || 'Could not reach the building_guide agent.',
        floors: [],
        focusFloor: '',
        offline: true,
      })
      return
    }
    const assistants = liveMessages.filter((m) => m.role === 'assistant')
    if (assistants.length <= prevAssistantCountRef.current) return
    const lastAssistant = assistants[assistants.length - 1]
    if (!lastAssistant || lastAssistant.parts.length === 0) return
    const chatReady = isReady && chat?.status === 'ready'
    const extracted = extractLiveTurn(
      lastAssistant,
      index,
      buildingId ?? '',
      activeBuildingName,
      userText,
      chatReady,
    )
    setTrace(extracted.trace)
    if (chatReady) {
      prevAssistantCountRef.current = assistants.length
      setLiveMode(false)
      if (lastAssistant.parts.some((p) => isToolUIPart(p) && PLAN_EDITS.has(getToolName(p)))) {
        void useApp.getState().reloadScene()
      }
      if (extracted.ask) {
        setPhase('ask')
        setAsk(extracted.ask)
      } else if (extracted.result) {
        showResult(extracted.result, Boolean(scenario?.voice), Boolean(extracted.alreadyApplied))
      }
    }
  }, [
    chat,
    liveMode,
    liveMessages,
    liveStatus,
    liveError,
    scenario,
    index,
    buildingId,
    activeBuildingName,
    userText,
    showResult,
  ])

  // Submit handler (free-form user input or starter prompt -> live backend agent)
  const submit = useCallback(
    (rawText?: string) => {
      const q = (rawText ?? input).trim()
      if (!q) return
      const matched = starterList.find(
        (s) => s.id === q || s.user.toLowerCase() === q.toLowerCase() || s.chip.toLowerCase() === q.toLowerCase(),
      )
      if (matched) {
        void sendToBackend(matched.user, matched.buildingId, matched, false)
        return
      }
      const isFollowUp = phase === 'ask' || phase === 'result' || phase === 'walk'
      void sendToBackend(q, buildingId ?? undefined, scenario, isFollowUp)
    },
    [input, starterList, sendToBackend, buildingId, phase, scenario],
  )

  // Consume any prompt sent from BrowsePanel ("Directions")
  useEffect(() => {
    if (!pendingPrompt) return
    const text = consumePrompt()
    if (text) submit(text)
  }, [pendingPrompt, consumePrompt, submit])

  // Pick an option in `ask` -> remembers location if applicable and sends replyText to the live backend agent
  const pickOption = useCallback(
    (opt: AskOption) => {
      if (opt.disabled) return
      if (opt.spaceId) {
        remember([{ icon: 'map-pin', text: `At ${opt.label}`, spaceId: opt.spaceId }])
      }
      void sendToBackend(opt.replyText, buildingId ?? undefined, scenario, true)
    },
    [remember, sendToBackend, buildingId, scenario],
  )

  const scanDoor = useCallback(() => {
    const first = ask?.options.find((o) => !o.disabled)
    if (first) pickOption(first)
  }, [ask, pickOption])

  // Voice input (Web Speech API)
  const recognitionRef = useRef<{ stop: () => void } | null>(null)
  const startVoice = useCallback(() => {
    stopSpeech()
    setPhase('listening')
    setListenText('')

    const SpeechRec =
      typeof window !== 'undefined'
        ? ((window as unknown as Record<string, unknown>).SpeechRecognition ??
            (window as unknown as Record<string, unknown>).webkitSpeechRecognition)
        : null

    if (typeof SpeechRec === 'function') {
      try {
        const rec = new (SpeechRec as new () => {
          continuous: boolean
          interimResults: boolean
          lang: string
          onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void
          onerror: () => void
          start: () => void
          stop: () => void
        })()
        rec.continuous = false
        rec.interimResults = true
        rec.lang = 'en-US'
        rec.onresult = (event) => {
          let transcript = ''
          for (let i = 0; i < event.results.length; i++) {
            transcript += event.results[i][0]?.transcript ?? ''
          }
          if (transcript.trim()) setListenText(transcript.trim())
        }
        rec.onerror = () => {
          // User can click Done speaking or Cancel
        }
        rec.start()
        recognitionRef.current = rec
      } catch {
        // Ignore if unsupported
      }
    }
  }, [stopSpeech])

  const finishVoiceEarly = useCallback(() => {
    recognitionRef.current?.stop()
    recognitionRef.current = null
    const finalTranscript = listenText.trim()
    if (finalTranscript) {
      submit(finalTranscript)
    } else {
      setPhase('home')
    }
  }, [listenText, submit])

  // Walk Mode controls
  const startWalk = useCallback(() => {
    if (!result?.steps?.length) return
    const firstFloor = result.steps[0]?.floorId ?? result.focusFloor
    setPhase('walk')
    setStepIdx(0)
    setFloorState(firstFloor)
    speakText(result.steps[0]?.text)
  }, [result, speakText])

  const moveStep = useCallback(
    (delta: number) => {
      const steps = result?.steps ?? []
      if (!steps.length) return
      const next = Math.max(0, Math.min(steps.length - 1, stepIdx + delta))
      const nextFloor = steps[next]?.floorId ?? floor
      setStepIdx(next)
      if (nextFloor) setFloorState(nextFloor)
      if (speaking && steps[next]?.text) {
        speakText(steps[next].text)
      }
    },
    [result?.steps, stepIdx, floor, speaking, speakText],
  )

  const selectStep = useCallback(
    (idx: number) => {
      const steps = result?.steps ?? []
      if (!steps[idx]) return
      setStepIdx(idx)
      setFloorState(steps[idx].floorId)
    },
    [result?.steps],
  )

  const exitWalk = useCallback(() => {
    stopSpeech()
    setPhase('result')
  }, [stopSpeech])

  // Confirm / Undo map changes -> sends confirmation or undo directly to the live backend agent session
  const confirmChange = useCallback(() => {
    if (!result) return
    setApplied(true)
    const promptText = result.confirmPrompt ?? 'Yes, please apply that change.'
    void sendToBackend(promptText, buildingId ?? undefined, scenario, true)
  }, [result, sendToBackend, buildingId, scenario])

  const undoChange = useCallback(() => {
    if (!result) return
    setApplied(false)
    void sendToBackend('Undo the last change.', buildingId ?? undefined, scenario, true)
  }, [result, sendToBackend, buildingId, scenario])

  // Back to the welcome screen: no answer, route or highlight left on the map
  const clearView = useCallback(() => {
    stopSpeech()
    recognitionRef.current?.stop()
    recognitionRef.current = null
    setLiveMode(false)
    setPhase('home')
    setScenario(null)
    setUserText('')
    setReplyText(null)
    setTrace([])
    setTraceOpen(false)
    setAsk(null)
    setResult(null)
    setFloorState(null)
    setStepIdx(0)
    setApplied(false)
    setInput('')
    setListenText('')
    setRoute(null)
    setHighlight([])
    setMapOverlays({ closedIds: [], affectedIds: [], ghostDoor: null, walkMarker: null })
    setMapInsets({ top: 0, right: 0, bottom: 0, left: 0 })
    // Where you were belongs to the last question; preferences (step-free, short answers) carry over.
    useApp.setState((s) => ({ memory: s.memory.filter((m) => !m.spaceId) }))
  }, [stopSpeech, setRoute, setHighlight, setMapOverlays, setMapInsets])

  // Start over: the welcome screen, and the guide forgets this conversation
  const reset = useCallback(() => {
    clearView()
    if (buildingId) resetChat(buildingId)
  }, [clearView, buildingId])

  // Another building: the last one's answer, route and highlights mean nothing here. A question already on
  // its way to the new building (a starter, an upload) sets its own state after the switch.
  const shownBuilding = useRef(buildingId)
  useEffect(() => {
    if (shownBuilding.current === buildingId) return
    shownBuilding.current = buildingId
    clearView()
  }, [buildingId, clearView])

  // A place remembered in one building means nothing in another.
  useEffect(() => {
    useApp.setState((s) => ({
      memory: s.memory.filter((m) => !m.spaceId || !buildingId || index?.spaceById.has(m.spaceId)),
    }))
  }, [buildingId, index])

  // Arriving is the only time we know where you are without asking.
  const arrive = useCallback(() => {
    const goal = result?.route?.goal
    if (goal && index?.spaceById.has(goal)) {
      remember([{ icon: 'map-pin', text: `At ${result?.title ?? 'destination'}`, spaceId: goal }])
    }
  }, [result, index, remember])

  // Synchronize MapView with Agent-First state
  useEffect(() => {
    const isMap = (phase === 'result' || phase === 'walk') && !!result
    if (!isMap || !result) {
      setView3d(null)
      setRoute(null)
      setHighlight([])
      setMapOverlays({ closedIds: [], affectedIds: [], ghostDoor: null, walkMarker: null })
      // Home: the whole floor sits between the building pill and the greeting above the bar.
      setMapInsets(
        CLASSIC
          ? { top: 0, right: 0, bottom: 0, left: 0 }
          : phone
            ? { top: 76, right: 16, bottom: 300, left: 16 }
            : { top: 84, right: 80, bottom: 250, left: 80 },
      )
      return
    }

    const steps = result.steps ?? []
    const curStep: WalkStep | undefined = steps[stepIdx] ?? steps[0]
    const activeFloor = (phase === 'walk' && curStep?.floorId ? curStep.floorId : floor) ?? result.focusFloor
    if (activeFloor && activeFloor !== useApp.getState().floorId) {
      setFloor(activeFloor)
    }
    // Walking is step by step on one floor at a time, flat; an overview the answer asked for in 3D is tilted.
    setView3d(phase === 'walk' ? null : (result.view3d ?? null))

    if (phase === 'walk') {
      const curSpace = curStep && index ? index.spaceById.get(curStep.spaceId) : undefined
      setMapInsets(
        phone
          ? { top: 155, right: 20, bottom: 165, left: 20 }
          : { top: 88, right: 32, bottom: 120, left: 32 },
      )
      setRoute(result.route?.showRoute ?? null)
      setHighlight(result.assetIds ?? [])
      setMapOverlays({
        closedIds: [],
        affectedIds: [],
        ghostDoor: null,
        walkMarker:
          curSpace && activeFloor
            ? { floorId: activeFloor, pos: curSpace.anchor as [number, number] }
            : null,
      })
    } else {
      setMapInsets(
        CLASSIC
          ? phone
            ? { top: 64, right: 20, bottom: 360, left: 20 }
            : { top: 84, right: 32, bottom: 52, left: 32 }
          : phone
            ? { top: 72, right: 16, bottom: 420, left: 16 }
            : { top: 88, right: 40, bottom: 40, left: 460 },
      )
      setRoute(result.route?.showRoute ?? null)
      setHighlight(
        result.focusIds ?? result.assetIds ?? [],
        CLASSIC || result.offline ? null : mapLabel(result, shapeAnswer(result).headline),
      )
      setMapOverlays({
        closedIds: result.closed ?? [],
        affectedIds: result.affected ?? [],
        ghostDoor: result.ghostDoor ? { ...result.ghostDoor, applied } : null,
        walkMarker: null,
      })
    }
    // setRoute moves the map to the route's first floor; the floor picked above (a step, the switcher) wins.
    if (activeFloor && activeFloor !== useApp.getState().floorId) setFloor(activeFloor)
  }, [
    phase,
    result,
    floor,
    stepIdx,
    applied,
    phone,
    index,
    setFloor,
    setRoute,
    setHighlight,
    setMapOverlays,
    setMapInsets,
    setView3d,
  ])

  const activeIndex = index

  if (!CLASSIC) {
    return (
      <>
        <BuildingPicker />
        <AssistantView
          phone={phone}
          buildingId={buildingId}
          history={history}
          drafted={buildings.find((b) => b.id === buildingId)?.split === 'draft'}
          phase={phase}
          buildingName={result?.buildingName ?? activeBuildingName}
          memory={memory}
          starterList={starterList}
          userText={userText}
          replyText={replyText}
          trace={trace}
          traceOpen={traceOpen}
          setTraceOpen={setTraceOpen}
          ask={ask}
          result={result}
          activeIndex={activeIndex}
          floor={floor}
          setFloorState={setFloorState}
          stepIdx={stepIdx}
          speaking={speaking}
          applied={applied}
          input={input}
          setInput={setInput}
          listenText={listenText}
          onOpenBuildingPicker={() => setPickerOpen(true)}
          onOpenBrowseMap={() => setMode('browse')}
          onReset={reset}
          onSubmit={submit}
          onRunScenario={runScenario}
          onPickOption={pickOption}
          onScanDoor={scanDoor}
          onStartVoice={startVoice}
          onFinishVoiceEarly={finishVoiceEarly}
          onStartWalk={startWalk}
          onMoveStep={moveStep}
          onSelectStep={selectStep}
          onExitWalk={exitWalk}
          onSpeakText={speakText}
          onStopSpeech={stopSpeech}
          onConfirmChange={confirmChange}
          onUndoChange={undoChange}
          onForgetOne={forgetOne}
          onForgetAll={forgetAll}
          onArrive={arrive}
        />
      </>
    )
  }

  // ---- Render Phone vs Desktop -----------------------------------------------------------------------------

  if (phone) {
    return (
      <div className="relative flex h-dvh w-full flex-col overflow-hidden bg-white select-none">
        <BuildingPicker />
        <PhoneView
          phase={phase}
          buildingName={result?.buildingName ?? activeBuildingName}
          memory={memory}
          starterList={starterList}
          userText={userText}
          replyText={replyText}
          trace={trace}
          traceOpen={traceOpen}
          setTraceOpen={setTraceOpen}
          ask={ask}
          result={result}
          activeIndex={activeIndex}
          floor={floor}
          setFloorState={setFloorState}
          stepIdx={stepIdx}
          speaking={speaking}
          applied={applied}
          input={input}
          setInput={setInput}
          listenText={listenText}
          onOpenBuildingPicker={() => setPickerOpen(true)}
          onOpenBrowseMap={() => setMode('browse')}
          onReset={reset}
          onSubmit={submit}
          onRunScenario={runScenario}
          onPickOption={pickOption}
          onScanDoor={scanDoor}
          onStartVoice={startVoice}
          onFinishVoiceEarly={finishVoiceEarly}
          onStartWalk={startWalk}
          onMoveStep={moveStep}
          onSelectStep={selectStep}
          onExitWalk={exitWalk}
          onSpeakText={speakText}
          onStopSpeech={stopSpeech}
          onConfirmChange={confirmChange}
          onUndoChange={undoChange}
          onForgetOne={forgetOne}
          onForgetAll={forgetAll}
        />
      </div>
    )
  }

  return (
    <div className="relative flex h-dvh w-full overflow-hidden bg-[#f4f5f7] select-none">
      <BuildingPicker />
      <DesktopView
        phase={phase}
        buildingName={result?.buildingName ?? activeBuildingName}
        memory={memory}
        starterList={starterList}
        userText={userText}
        replyText={replyText}
        trace={trace}
        traceOpen={traceOpen}
        setTraceOpen={setTraceOpen}
        ask={ask}
        result={result}
        activeIndex={activeIndex}
        floor={floor}
        setFloorState={setFloorState}
        stepIdx={stepIdx}
        speaking={speaking}
        applied={applied}
        input={input}
        setInput={setInput}
        listenText={listenText}
        onOpenBuildingPicker={() => setPickerOpen(true)}
        onOpenBrowseMap={() => setMode('browse')}
        onReset={reset}
        onSubmit={submit}
        onRunScenario={runScenario}
        onPickOption={pickOption}
        onScanDoor={scanDoor}
        onStartVoice={startVoice}
        onFinishVoiceEarly={finishVoiceEarly}
        onStartWalk={startWalk}
        onMoveStep={moveStep}
        onSelectStep={selectStep}
        onExitWalk={exitWalk}
        onSpeakText={speakText}
        onStopSpeech={stopSpeech}
        onConfirmChange={confirmChange}
        onUndoChange={undoChange}
        onForgetOne={forgetOne}
        onForgetAll={forgetAll}
      />
    </div>
  )
}

// ---- Shared Props Interface ------------------------------------------------------------------------------

export interface ViewProps {
  buildingId?: string | null
  phase: FlowPhase
  buildingName: string
  memory: MemoryChip[]
  starterList: Scenario[]
  userText: string
  replyText: string | null
  trace: TraceItem[]
  traceOpen: boolean
  setTraceOpen: (v: boolean | ((p: boolean) => boolean)) => void
  ask: ScenarioAsk | null
  result: ScenarioResult | null
  activeIndex: SceneIndex | null
  floor: string | null
  setFloorState: (f: string) => void
  stepIdx: number
  speaking: boolean
  applied: boolean
  input: string
  setInput: (v: string) => void
  listenText: string
  onOpenBuildingPicker: () => void
  onOpenBrowseMap: () => void
  onReset: () => void
  onSubmit: (text?: string) => void
  onRunScenario: (sc: Scenario) => void
  onPickOption: (opt: AskOption) => void
  onScanDoor: () => void
  onStartVoice: () => void
  onFinishVoiceEarly: () => void
  onStartWalk: () => void
  onMoveStep: (delta: number) => void
  onSelectStep: (idx: number) => void
  onExitWalk: () => void
  onSpeakText: (text: string | undefined) => void
  onStopSpeech: () => void
  onConfirmChange: () => void
  onUndoChange: () => void
  onForgetOne: (idx: number) => void
  onForgetAll: () => void
  onArrive?: () => void
}

// ---- Shared Subcomponents --------------------------------------------------------------------------------

function TraceDropdown({
  trace,
  open,
  onToggle,
}: {
  trace: TraceItem[]
  open: boolean
  onToggle: () => void
}) {
  if (!trace.length) return null
  const running = trace.some((t) => t.running)
  const last = trace[trace.length - 1]
  return (
    <div className="my-1.5">
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f3f4] px-2.5 py-1 text-[12px] text-[#5f6368] hover:bg-[#e8eaed]"
      >
        {running ? (
          <span
            className="size-2.5 rounded-full border-2 border-[#1a73e8] border-t-transparent"
            style={{ animation: 'ttyb-spin 0.7s linear infinite' }}
          />
        ) : (
          <Sparkles className="size-3 text-[#1a73e8]" aria-hidden />
        )}
        <span>{running ? `${last.tool}…` : `${trace.length} tool call${trace.length > 1 ? 's' : ''}`}</span>
        <ChevronDown className={clsx('size-3 transition-transform', open && 'rotate-180')} aria-hidden />
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 rounded-xl border border-[#e2e5ea] bg-[#f8f9fa] px-3 py-2 font-mono text-[11px] text-[#3c4043]">
          {trace.map((t, idx) => (
            <div key={idx} className="flex items-baseline justify-between gap-2">
              <span className="truncate">
                <b className="text-[#1a73e8]">{t.tool}</b>({t.args})
              </span>
              <span className="shrink-0 text-[#5f6368]">{t.running ? '…' : t.result}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SpeakingBanner({ onStop }: { onStop: () => void }) {
  return (
    <div className="mt-2.5 flex items-center gap-2 rounded-xl bg-[#e8f0fe] px-3 py-2 text-[12px] font-medium text-[#1a73e8]">
      <Volume2 className="size-3.5 shrink-0" aria-hidden />
      <div className="flex h-3 items-center gap-[3px]">
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className="h-3 w-[2.5px] rounded-sm bg-[#1a73e8]"
            style={{ animation: `ttyb-wave 0.7s ease-in-out ${i * 0.1}s infinite` }}
          />
        ))}
      </div>
      <span className="flex-1">Reading aloud…</span>
      <button type="button" onClick={onStop} className="hover:underline">
        Stop
      </button>
    </div>
  )
}

// ---- Phone View (matches AgentPhone.dc.html) -------------------------------------------------------------

function PhoneView(props: ViewProps) {
  const {
    phase,
    buildingName,
    memory,
    starterList,
    userText,
    replyText,
    trace,
    traceOpen,
    setTraceOpen,
    ask,
    result,
    activeIndex,
    floor,
    setFloorState,
    stepIdx,
    speaking,
    applied,
    input,
    setInput,
    listenText,
    onOpenBuildingPicker,
    onOpenBrowseMap,
    onReset,
    onSubmit,
    onRunScenario,
    onPickOption,
    onScanDoor,
    onStartVoice,
    onFinishVoiceEarly,
    onStartWalk,
    onMoveStep,
    onSelectStep,
    onExitWalk,
    onSpeakText,
    onStopSpeech,
    onConfirmChange,
    onUndoChange,
    onForgetOne,
    onForgetAll,
  } = props

  const isResult = phase === 'result' && !!result
  const isWalk = phase === 'walk' && !!result
  const isMap = isResult || isWalk

  const steps = result?.steps ?? []
  const curStep = steps[stepIdx] ?? steps[0]
  const nextStep = steps[stepIdx + 1]
  const activeFloor = (isWalk && curStep?.floorId ? curStep.floorId : floor) ?? result?.focusFloor ?? ''
  const remainM = isWalk ? steps.slice(stepIdx).reduce((a, x) => a + (x.d || 0), 0) : 0

  return (
    <>
      {/* Map canvas (mounted when in result or walk mode) */}
      {isMap && (
        <div
          className={clsx(
            ' overflow-hidden bg-[#f4f5f7] transition-all duration-300',
            isWalk ? 'absolute inset-0' : 'absolute inset-x-0 top-0 h-[54%]',
          )}
        >
          <MapView hideControls />

          {isResult && result && (
            <div className="pointer-events-none absolute inset-x-3 top-[calc(12px+env(safe-area-inset-top))] z-20 flex items-center justify-between gap-2 [&>*]:pointer-events-auto">
              <button
                type="button"
                onClick={onReset}
                className="inline-flex items-center gap-1.5 rounded-full bg-white/95 px-3 py-2 text-[13px] font-medium text-[#202124] shadow-[0_2px_10px_rgba(32,33,36,0.14)] backdrop-blur"
              >
                <ArrowLeft className="size-3.5" aria-hidden /> Ask again
              </button>

              {result.floors.length > 1 && (
                <div className="flex gap-1 rounded-full bg-white/95 p-1 shadow-[0_2px_10px_rgba(32,33,36,0.14)] backdrop-blur">
                  {result.floors.map((fid) => (
                    <button
                      key={fid}
                      type="button"
                      onClick={() => setFloorState(fid)}
                      className={clsx(
                        'rounded-full px-3 py-1 text-[12px] font-medium transition-colors',
                        fid === activeFloor ? 'bg-[#1a73e8] text-white' : 'text-[#5f6368]',
                      )}
                    >
                      {activeIndex ? floorNameOf(activeIndex, fid) : fid}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Walk Mode overlays (Phone) */}
      {isWalk && curStep && (
        <>
          <div className="absolute inset-x-3 top-[calc(12px+env(safe-area-inset-top))] z-20 rounded-[20px] bg-[#1a73e8] p-4 text-white shadow-[0_8px_24px_rgba(26,115,232,0.38)]">
            <div className="flex items-center gap-3">
              <div className="grid size-[46px] shrink-0 place-items-center rounded-[14px] bg-white/20">
                <StepIconGlyph icon={curStep.icon} className="size-6 text-white" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-medium tracking-[0.04em] uppercase opacity-85">
                  Step {stepIdx + 1} of {steps.length} ·{' '}
                  {activeIndex ? floorNameOf(activeIndex, curStep.floorId) : curStep.floorId}
                </div>
                <div className="mt-0.5 text-[18px] leading-[1.25] font-semibold">{curStep.text}</div>
              </div>
              <button
                type="button"
                onClick={onExitWalk}
                aria-label="Exit walk mode"
                className="grid size-8 shrink-0 place-items-center rounded-full bg-white/15"
              >
                <X className="size-4 text-white" />
              </button>
            </div>
            <div className="mt-2.5 flex items-center justify-between border-t border-white/20 pt-2 text-[12px] opacity-90">
              <span className="truncate">
                {nextStep ? `Next: ${nextStep.text}` : 'You have arrived'}
              </span>
              {curStep.d > 0 && <span className="shrink-0 font-semibold">{fmtM(curStep.d)}</span>}
            </div>
          </div>

          {speaking && (
            <div className="absolute top-[168px] left-4 z-20 inline-flex items-center gap-1.5 rounded-full bg-white/95 px-3 py-1.5 text-[12px] font-medium text-[#1a73e8] shadow-[0_2px_10px_rgba(32,33,36,0.12)]">
              <Volume2 className="size-3.5" aria-hidden /> Speaking
              <button type="button" onClick={onStopSpeech} className="ml-1 text-[#5f6368]">
                <X className="size-3" />
              </button>
            </div>
          )}

          <div className="absolute inset-x-3 bottom-[calc(12px+env(safe-area-inset-bottom))] z-20 rounded-[20px] bg-white p-3.5 shadow-[0_6px_24px_rgba(32,33,36,0.16)]">
            <div className="mb-2.5 flex items-center justify-between">
              <div>
                <div className="text-[16px] font-semibold text-[#202124]">
                  {remainM > 0 ? `~${fmtWalk(remainM)} left` : 'Arrived'}
                </div>
                <div className="text-[12px] text-[#5f6368]">{fmtM(remainM)} remaining</div>
              </div>
              <button
                type="button"
                onClick={() => (speaking ? onStopSpeech() : onSpeakText(curStep.text))}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-full border border-[#e2e5ea] px-3 py-2 text-[12px] font-medium',
                  speaking ? 'bg-[#e8f0fe] text-[#1a73e8]' : 'bg-white text-[#202124]',
                )}
              >
                <Volume2 className="size-3.5 text-[#1a73e8]" aria-hidden /> Read aloud
              </button>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={stepIdx === 0}
                onClick={() => onMoveStep(-1)}
                className={clsx(
                  'inline-flex flex-1 items-center justify-center gap-1 rounded-xl border border-[#e2e5ea] bg-white p-3 text-[14px] font-medium text-[#202124]',
                  stepIdx === 0 && 'opacity-40',
                )}
              >
                <ChevronLeft className="size-4" aria-hidden /> Previous
              </button>
              {stepIdx < steps.length - 1 ? (
                <button
                  type="button"
                  onClick={() => onMoveStep(1)}
                  className="inline-flex flex-[1.4] items-center justify-center gap-1 rounded-xl bg-[#1a73e8] p-3 text-[14px] font-semibold text-white"
                >
                  Next step <ChevronRight className="size-4" aria-hidden />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onExitWalk}
                  className="inline-flex flex-[1.4] items-center justify-center gap-1.5 rounded-xl bg-[#137333] p-3 text-[14px] font-semibold text-white"
                >
                  <Check className="size-4" aria-hidden /> Done
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {/* Result bottom sheet (Phone) */}
      {isResult && result && (
        <div className="absolute inset-x-0 bottom-0 z-20 flex max-h-[54%] flex-col rounded-t-[24px] bg-white shadow-[0_-8px_30px_rgba(32,33,36,0.14)]">
          <div className="mx-auto my-2 h-1 w-9 shrink-0 rounded-full bg-[#dadce0]" />

          <div className="px-4 pb-2.5">
            <div className="flex items-start justify-between gap-2.5">
              <div className="min-w-0">
                <div className="truncate text-[19px] leading-[1.2] font-semibold text-[#202124]">
                  {result.title}
                </div>
                <div className="mt-0.5 text-[13px] text-[#5f6368]">
                  {applied ? 'Applied to the map' : result.sub}
                </div>
              </div>

              {result.kind === 'route' && steps.length > 0 && (
                <button
                  type="button"
                  onClick={onStartWalk}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#1a73e8] px-4 py-2.5 text-[14px] font-semibold text-white shadow-[0_2px_8px_rgba(26,115,232,0.35)]"
                >
                  <Navigation className="size-3.5" aria-hidden /> Start
                </button>
              )}

              {(result.kind === 'impact' || result.kind === 'edit') && (
                <span
                  className={clsx(
                    'shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold',
                    applied ? 'bg-[#e6f4ea] text-[#137333]' : 'bg-[#fef7e0] text-[#b06000]',
                  )}
                >
                  {applied ? 'Applied' : 'Preview'}
                </span>
              )}
            </div>

            <div className="mt-2.5 rounded-xl bg-[#f8f9fa] px-3 py-2 text-[12.5px] leading-[1.4] text-[#3c4043]">
              <div className="flex items-start gap-2">
                <Sparkles className="mt-0.5 size-3.5 shrink-0 text-[#1a73e8]" aria-hidden />
                <div className="flex-1">{result.understood}</div>
              </div>
              <TraceDropdown
                trace={trace}
                open={traceOpen}
                onToggle={() => setTraceOpen((o) => !o)}
              />
            </div>

            {speaking && <SpeakingBanner onStop={onStopSpeech} />}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-2.5">
            {result.kind === 'route' && (
              <>
                <div className="mb-2.5 flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() =>
                      speaking ? onStopSpeech() : onSpeakText(result.say ?? result.understood)
                    }
                    className={clsx(
                      'inline-flex items-center gap-1.5 rounded-full border border-[#e2e5ea] px-3 py-1.5 text-[12px] font-medium text-[#202124]',
                      speaking ? 'bg-[#e8f0fe]' : 'bg-white',
                    )}
                  >
                    <Volume2 className="size-3.5 text-[#1a73e8]" aria-hidden /> Read aloud
                  </button>
                  {result.followUp && (
                    <button
                      type="button"
                      onClick={() => onSubmit(result.followUp)}
                      className="inline-flex items-center gap-1.5 rounded-full border border-[#d2e3fc] bg-[#e8f0fe] px-3 py-1.5 text-[12px] font-medium text-[#1a73e8]"
                    >
                      <Sparkles className="size-3 text-[#1a73e8]" aria-hidden /> {result.followLabel}
                    </button>
                  )}
                </div>

                <div className="divide-y divide-[#f1f3f4]">
                  {steps.map((st, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => onSelectStep(idx)}
                      className="flex w-full items-center gap-2.5 py-2 text-left text-[13px]"
                    >
                      <span className="grid size-[22px] shrink-0 place-items-center rounded-full bg-[#e8f0fe] text-[11px] font-semibold text-[#1a73e8]">
                        {idx + 1}
                      </span>
                      <span className="flex-1 text-[#202124]">
                        {st.text}
                        {result.floors.length > 1 && activeIndex && (
                          <span className="ml-1.5 rounded-full bg-[#f1f3f4] px-1.5 py-0.5 text-[10px] text-[#5f6368]">
                            {floorNameOf(activeIndex, st.floorId)}
                          </span>
                        )}
                      </span>
                      {st.d > 0 && <span className="text-[12px] text-[#5f6368]">{fmtM(st.d)}</span>}
                    </button>
                  ))}
                </div>
              </>
            )}

            {(result.kind === 'impact' || result.kind === 'edit') && (
              <>
                {result.stats && result.stats.length > 0 && (
                  <div className="my-1.5 flex gap-2">
                    {result.stats.map((st, idx) => (
                      <div key={idx} className="flex-1 rounded-[14px] bg-[#f8f9fa] p-3">
                        <div
                          className={clsx(
                            'text-[22px] font-bold',
                            result.kind === 'impact'
                              ? idx === 0
                                ? 'text-[#d93025]'
                                : 'text-[#b06000]'
                              : 'text-[#1a73e8]',
                          )}
                        >
                          {st.n}
                        </div>
                        <div className="mt-0.5 text-[12px] leading-[1.3] text-[#5f6368]">{st.label}</div>
                      </div>
                    ))}
                  </div>
                )}

                {result.note && (
                  <div className="my-1.5 text-[12.5px] text-[#5f6368]">{result.note}</div>
                )}

                {result.confirm ? (
                  !applied ? (
                    <div className="mt-2.5 flex gap-2">
                      <button
                        type="button"
                        onClick={onConfirmChange}
                        className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-[#1a73e8] p-3 text-[14px] font-semibold text-white"
                      >
                        <Check className="size-4" aria-hidden /> {result.confirm}
                      </button>
                      <button
                        type="button"
                        onClick={onReset}
                        className="rounded-xl border border-[#e2e5ea] bg-white px-3.5 py-3 text-[13px] font-medium text-[#5f6368]"
                      >
                        Not now
                      </button>
                    </div>
                  ) : (
                    <div className="mt-2.5 flex items-center justify-between gap-2 rounded-xl bg-[#e6f4ea] px-3 py-2.5 text-[12.5px] text-[#137333]">
                      <span className="flex items-center gap-1.5">
                        <Check className="size-3.5 shrink-0" aria-hidden />
                        {result.applied}
                      </span>
                      <button
                        type="button"
                        onClick={onUndoChange}
                        className="shrink-0 rounded-full border border-[#137333]/30 bg-white px-2.5 py-1 text-[12px] font-semibold text-[#137333]"
                      >
                        {result.undo}
                      </button>
                    </div>
                  )
                ) : null}
              </>
            )}
          </div>

          <div className="flex items-center gap-2 border-t border-[#eef0f3] px-3 py-2 pb-[calc(8px+env(safe-area-inset-bottom))]">
            <div className="flex flex-1 items-center gap-2 rounded-full bg-[#f1f3f4] px-3.5 py-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onSubmit()}
                placeholder="Ask a follow-up…"
                className="flex-1 bg-transparent text-[13px] text-[#202124] outline-none placeholder:text-[#5f6368]"
              />
              <button
                type="button"
                onClick={() => (input.trim() ? onSubmit() : onStartVoice())}
                aria-label={input.trim() ? 'Send' : 'Voice input'}
                className="grid size-7 place-items-center rounded-full bg-[#1a73e8] text-white"
              >
                {input.trim() ? <ArrowUp className="size-3.5" /> : <Mic className="size-3.5" />}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Non-map screens on Phone: Home, Listening, Working / Ask */}
      {!isMap && (
        <div className="flex flex-1 flex-col overflow-hidden bg-white">
          <div className="flex items-center justify-between px-4 pt-[calc(10px+env(safe-area-inset-top))] pb-2">
            <button
              type="button"
              onClick={onOpenBuildingPicker}
              className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f3f4] px-3 py-1.5 text-[12.5px] font-medium text-[#202124]"
            >
              <Building2 className="size-3.5 text-[#1a73e8]" aria-hidden />
              <span className="max-w-[180px] truncate">{buildingName}</span>
              <ChevronDown className="size-3.5 text-[#5f6368]" aria-hidden />
            </button>

            {phase !== 'home' ? (
              <button
                type="button"
                onClick={onReset}
                className="rounded-full px-2.5 py-1 text-[12.5px] font-medium text-[#5f6368] hover:bg-[#f1f3f4]"
              >
                Start over
              </button>
            ) : (
              <button
                type="button"
                onClick={onOpenBrowseMap}
                className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12.5px] font-medium text-[#5f6368] hover:bg-[#f1f3f4]"
              >
                <MapIcon className="size-3.5" aria-hidden /> Floor plan
              </button>
            )}
          </div>

          {phase === 'home' && (
            <div className="flex flex-1 flex-col overflow-y-auto px-5 pt-3 pb-4">
              <h1 className="text-[26px] leading-[1.18] font-semibold tracking-[-0.02em] text-[#202124]">
                What do you need?
              </h1>
              <p className="mt-1.5 text-[14px] leading-[1.45] text-[#5f6368]">
                Ask for a room, a machine or a route — or tell me what changed in the building.
              </p>

              {memory.length > 0 && (
                <div className="mt-3.5">
                  <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold tracking-[0.04em] text-[#5f6368] uppercase">
                    <span>I remember</span>
                    <button
                      type="button"
                      onClick={onForgetAll}
                      className="font-normal text-[#1a73e8] normal-case"
                    >
                      Forget
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {memory.map((m, idx) => (
                      <span
                        key={idx}
                        className="inline-flex items-center gap-1.5 rounded-full bg-[#e8f0fe] px-2.5 py-1 text-[12px] font-medium text-[#1a73e8]"
                      >
                        <MemoryIconGlyph icon={m.icon} className="size-3" />
                        {m.text}
                        <button
                          type="button"
                          onClick={() => onForgetOne(idx)}
                          aria-label={`Forget ${m.text}`}
                        >
                          <X className="size-3 text-[#1a73e8]" />
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-4 flex items-center gap-2 rounded-[20px] border border-[#e2e5ea] bg-[#f8f9fa] p-2.5 pl-4 shadow-[0_2px_10px_rgba(32,33,36,0.05)]">
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && onSubmit()}
                  placeholder="Ask anything about the building…"
                  className="flex-1 bg-transparent text-[15px] text-[#202124] outline-none placeholder:text-[#5f6368]"
                />
                <button
                  type="button"
                  onClick={() => (input.trim() ? onSubmit() : onStartVoice())}
                  aria-label={input.trim() ? 'Send' : 'Speak'}
                  className="grid size-[42px] shrink-0 place-items-center rounded-full bg-[#1a73e8] text-white shadow-[0_2px_8px_rgba(26,115,232,0.35)]"
                >
                  {input.trim() ? <ArrowUp className="size-[18px]" /> : <Mic className="size-[18px]" />}
                </button>
              </div>

              <div className="mt-5 mb-2 text-[11px] font-semibold tracking-[0.04em] text-[#5f6368] uppercase">
                Try a real situation
              </div>
              <div className="flex flex-col gap-2">
                {starterList.map((sc) => (
                  <button
                    key={sc.id}
                    type="button"
                    onClick={() => onRunScenario(sc)}
                    className="flex items-center gap-3 rounded-[16px] border border-[#e6e8ec] bg-white px-3.5 py-3 text-left transition-colors hover:border-[#d2e3fc] hover:bg-[#f8fbff]"
                  >
                    <div
                      className={clsx(
                        'grid size-9 shrink-0 place-items-center rounded-xl',
                        sc.id === 'lift' ? 'bg-[#fce8e6] text-[#d93025]' : 'bg-[#e8f0fe] text-[#1a73e8]',
                      )}
                    >
                      <ScenarioIconGlyph icon={sc.icon} className="size-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px] font-medium text-[#202124]">{sc.chip}</div>
                      <div className="mt-0.5 text-[11.5px] text-[#5f6368]">
                        {sc.persona} · {sc.buildingName}
                      </div>
                    </div>
                    <ChevronRight className="size-4 shrink-0 text-[#9aa0a6]" aria-hidden />
                  </button>
                ))}
              </div>
            </div>
          )}

          {phase === 'listening' && (
            <div className="flex flex-1 flex-col items-center justify-center p-6 text-center">
              <div className="grid size-[84px] place-items-center rounded-full bg-[#1a73e8] text-white shadow-[0_0_0_16px_#e8f0fe]">
                <Mic className="size-8" aria-hidden />
              </div>
              <div className="mt-5 text-[14px] font-medium text-[#1a73e8]">Listening…</div>
              <div className="mt-2.5 min-h-[54px] max-w-[300px] text-[18px] leading-[1.35] font-medium text-[#202124]">
                {listenText || '“Got a call about CNC mill B…”'}
              </div>
              <div className="mt-6 flex gap-2">
                <button
                  type="button"
                  onClick={onReset}
                  className="rounded-full border border-[#e2e5ea] bg-white px-4 py-2 text-[13px] text-[#5f6368]"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={onFinishVoiceEarly}
                  className="rounded-full bg-[#1a73e8] px-4 py-2 text-[13px] font-semibold text-white"
                >
                  Done speaking
                </button>
              </div>
            </div>
          )}

          {(phase === 'working' || phase === 'ask') && (
            <div className="flex flex-1 flex-col overflow-y-auto px-5 pt-2 pb-4">
              <div className=" max-w-[86%] self-end rounded-[18px] rounded-br-[4px] bg-[#1a73e8] px-3.5 py-2.5 text-[14px] leading-[1.4] text-white">
                {userText}
              </div>

              <div className="mt-2.5">
                <TraceDropdown
                  trace={trace}
                  open={traceOpen}
                  onToggle={() => setTraceOpen((o) => !o)}
                />
              </div>

              {ask && !replyText && (
                <div className="mt-2.5">
                  <div className="text-[18px] leading-[1.25] font-semibold text-[#202124]">
                    {ask.q}
                  </div>
                  <div className="mt-1 text-[13px] leading-[1.4] text-[#5f6368]">{ask.why}</div>

                  <div className="mt-3.5 flex flex-col gap-2">
                    {ask.options.map((opt, idx) => (
                      <button
                        key={idx}
                        type="button"
                        disabled={opt.disabled}
                        onClick={() => onPickOption(opt)}
                        className={clsx(
                          'flex items-center justify-between rounded-[16px] border-[1.5px] border-[#e2e5ea] bg-white px-4 py-3.5 text-left transition-colors hover:border-[#1a73e8]',
                          opt.disabled && 'opacity-45',
                        )}
                      >
                        <div>
                          <div className="text-[15px] font-semibold text-[#202124]">{opt.label}</div>
                          <div className="mt-0.5 text-[12.5px] text-[#5f6368]">{opt.sub}</div>
                        </div>
                        {!opt.disabled && (
                          <ChevronRight className="size-[18px] text-[#1a73e8]" aria-hidden />
                        )}
                      </button>
                    ))}
                  </div>

                  {ask.scan && (
                    <button
                      type="button"
                      onClick={onScanDoor}
                      className="mt-2.5 inline-flex w-full items-center justify-center gap-2 rounded-[14px] bg-[#f1f3f4] p-3 text-[13px] font-medium text-[#202124]"
                    >
                      <QrCode className="size-4 text-[#1a73e8]" aria-hidden />
                      Scan the sign by the nearest door
                    </button>
                  )}
                </div>
              )}

              {replyText && (
                <div className="mt-2.5 max-w-[86%] self-end rounded-[18px] rounded-br-[4px] bg-[#1a73e8] px-3.5 py-2.5 text-[14px] text-white">
                  {replyText}
                </div>
              )}
            </div>
          )}

          {phase !== 'home' && phase !== 'listening' && (
            <div className="border-t border-[#eef0f3] px-3.5 py-2 pb-[calc(8px+env(safe-area-inset-bottom))]">
              <div className="flex items-center gap-2 rounded-full bg-[#f1f3f4] px-3.5 py-2">
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && onSubmit()}
                  placeholder="Or type your answer…"
                  className="flex-1 bg-transparent text-[13px] text-[#202124] outline-none placeholder:text-[#5f6368]"
                />
                <button
                  type="button"
                  onClick={() => (input.trim() ? onSubmit() : onStartVoice())}
                  aria-label={input.trim() ? 'Send' : 'Speak'}
                  className="grid size-7 place-items-center rounded-full bg-[#1a73e8] text-white"
                >
                  {input.trim() ? <ArrowUp className="size-3.5" /> : <Mic className="size-3.5" />}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )
}

// ---- Desktop View (matches AgentDesktop.dc.html) ---------------------------------------------------------

function DesktopView(props: ViewProps) {
  const {
    phase,
    buildingName,
    memory,
    starterList,
    userText,
    replyText,
    trace,
    traceOpen,
    setTraceOpen,
    ask,
    result,
    activeIndex,
    floor,
    setFloorState,
    stepIdx,
    speaking,
    applied,
    input,
    setInput,
    listenText,
    onOpenBuildingPicker,
    onOpenBrowseMap,
    onReset,
    onSubmit,
    onRunScenario,
    onPickOption,
    onScanDoor,
    onStartVoice,
    onFinishVoiceEarly,
    onStartWalk,
    onMoveStep,
    onSelectStep,
    onExitWalk,
    onSpeakText,
    onStopSpeech,
    onConfirmChange,
    onUndoChange,
    onForgetOne,
    onForgetAll,
  } = props

  const isHome = phase === 'home'
  const isResult = phase === 'result' && !!result
  const isWalk = phase === 'walk' && !!result
  const hasMap = isResult || isWalk

  const steps = result?.steps ?? []
  const curStep = steps[stepIdx] ?? steps[0]
  const activeFloor = (isWalk && curStep?.floorId ? curStep.floorId : floor) ?? result?.focusFloor ?? ''

  return (
    <>
      {/* Left Column: Conversation & Task (420px) */}
      <aside className="z-20 flex h-full w-[420px] shrink-0 flex-col border-r border-[#e2e5ea] bg-white">
        <div className="flex items-center justify-between border-b border-[#eef0f3] px-5 pt-4 pb-3.5">
          <button
            type="button"
            onClick={onOpenBuildingPicker}
            className="inline-flex items-center gap-2 rounded-full bg-[#f1f3f4] px-3 py-1.5 text-[13px] font-medium text-[#202124] hover:bg-[#e8eaed]"
          >
            <Building2 className="size-3.5 text-[#1a73e8]" aria-hidden />
            <span className="max-w-[200px] truncate">{buildingName}</span>
            <ChevronDown className="size-3.5 text-[#5f6368]" aria-hidden />
          </button>

          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={onOpenBrowseMap}
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] font-medium text-[#5f6368] hover:bg-[#f1f3f4]"
            >
              <MapIcon className="size-3.5" aria-hidden /> Floor plan
            </button>
            {(!isHome || memory.length > 0) && (
              <button
                type="button"
                onClick={onReset}
                className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12.5px] font-medium text-[#5f6368] hover:bg-[#f1f3f4]"
              >
                <RotateCcw className="size-3.5" aria-hidden /> Start over
              </button>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isHome && (
            <>
              <h1 className="mt-1 text-[24px] leading-[1.2] font-semibold tracking-[-0.02em] text-[#202124]">
                What do you need?
              </h1>
              <p className="mt-1.5 text-[14px] leading-[1.45] text-[#5f6368]">
                Ask for a room, a machine or a route — or tell the building what changed. The map on the right opens when there is something to show.
              </p>

              {memory.length > 0 && (
                <div className="mt-4">
                  <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold tracking-[0.04em] text-[#5f6368] uppercase">
                    <span>I remember</span>
                    <button
                      type="button"
                      onClick={onForgetAll}
                      className="font-normal text-[#1a73e8] normal-case"
                    >
                      Forget all
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {memory.map((m, idx) => (
                      <span
                        key={idx}
                        className="inline-flex items-center gap-1.5 rounded-full bg-[#e8f0fe] px-2.5 py-1 text-[12px] font-medium text-[#1a73e8]"
                      >
                        <MemoryIconGlyph icon={m.icon} className="size-3" />
                        {m.text}
                        <button
                          type="button"
                          onClick={() => onForgetOne(idx)}
                          aria-label={`Forget ${m.text}`}
                        >
                          <X className="size-3 text-[#1a73e8]" />
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-5 mb-2.5 text-[11px] font-semibold tracking-[0.04em] text-[#5f6368] uppercase">
                Try a real situation
              </div>
              <div className="flex flex-col gap-2">
                {starterList.map((sc) => (
                  <button
                    key={sc.id}
                    type="button"
                    onClick={() => onRunScenario(sc)}
                    className="flex items-center gap-3 rounded-[14px] border border-[#e6e8ec] bg-white px-3.5 py-3 text-left transition-colors hover:border-[#d2e3fc] hover:bg-[#f8fbff]"
                  >
                    <div
                      className={clsx(
                        'grid size-9 shrink-0 place-items-center rounded-xl',
                        sc.id === 'lift' ? 'bg-[#fce8e6] text-[#d93025]' : 'bg-[#e8f0fe] text-[#1a73e8]',
                      )}
                    >
                      <ScenarioIconGlyph icon={sc.icon} className="size-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px] font-medium text-[#202124]">{sc.chip}</div>
                      <div className="mt-0.5 text-[11.5px] text-[#5f6368]">
                        {sc.persona} · {sc.buildingName}
                      </div>
                    </div>
                    <ChevronRight className="size-4 shrink-0 text-[#9aa0a6]" aria-hidden />
                  </button>
                ))}
              </div>
            </>
          )}

          {phase === 'listening' && (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <div className="grid size-[76px] place-items-center rounded-full bg-[#1a73e8] text-white shadow-[0_0_0_14px_#e8f0fe]">
                <Mic className="size-7" aria-hidden />
              </div>
              <div className="mt-4 text-[13px] font-medium text-[#1a73e8]">Listening…</div>
              <div className="mt-2 min-h-[48px] text-[16px] leading-[1.35] font-medium text-[#202124]">
                {listenText || '“Got a call about CNC mill B…”'}
              </div>
              <div className="mt-5 flex gap-2">
                <button
                  type="button"
                  onClick={onReset}
                  className="rounded-full border border-[#e2e5ea] bg-white px-3.5 py-1.5 text-[12.5px] text-[#5f6368]"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={onFinishVoiceEarly}
                  className="rounded-full bg-[#1a73e8] px-3.5 py-1.5 text-[12.5px] font-semibold text-white"
                >
                  Done speaking
                </button>
              </div>
            </div>
          )}

          {!isHome && phase !== 'listening' && (
            <div className="flex flex-col gap-3">
              {memory.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {memory.map((m, idx) => (
                    <span
                      key={idx}
                      className="inline-flex items-center gap-1.5 rounded-full bg-[#e8f0fe] px-2.5 py-1 text-[11.5px] font-medium text-[#1a73e8]"
                    >
                      <MemoryIconGlyph icon={m.icon} className="size-3" />
                      {m.text}
                      <button
                        type="button"
                        onClick={() => onForgetOne(idx)}
                        aria-label={`Forget ${m.text}`}
                      >
                        <X className="size-2.5 text-[#1a73e8]" />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="max-w-[88%] self-end rounded-[18px] rounded-br-[4px] bg-[#1a73e8] px-3.5 py-2.5 text-[14px] leading-[1.4] text-white">
                {userText}
              </div>

              <TraceDropdown
                trace={trace}
                open={traceOpen}
                onToggle={() => setTraceOpen((o) => !o)}
              />

              {ask && !replyText && (
                <div>
                  <div className="text-[17px] font-semibold text-[#202124]">{ask.q}</div>
                  <div className="mt-1 text-[13px] leading-[1.4] text-[#5f6368]">{ask.why}</div>
                  <div className="mt-3 flex flex-col gap-2">
                    {ask.options.map((opt, idx) => (
                      <button
                        key={idx}
                        type="button"
                        disabled={opt.disabled}
                        onClick={() => onPickOption(opt)}
                        className={clsx(
                          'flex items-center justify-between rounded-[14px] border-[1.5px] border-[#e2e5ea] bg-white px-3.5 py-3 text-left transition-colors hover:border-[#1a73e8]',
                          opt.disabled && 'opacity-45',
                        )}
                      >
                        <div>
                          <div className="text-[14px] font-semibold text-[#202124]">{opt.label}</div>
                          <div className="mt-0.5 text-[12px] text-[#5f6368]">{opt.sub}</div>
                        </div>
                        {!opt.disabled && <ChevronRight className="size-4 text-[#1a73e8]" aria-hidden />}
                      </button>
                    ))}
                  </div>
                  {ask.scan && (
                    <button
                      type="button"
                      onClick={onScanDoor}
                      className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#f1f3f4] p-2.5 text-[12.5px] font-medium text-[#202124]"
                    >
                      <QrCode className="size-4 text-[#1a73e8]" aria-hidden />
                      Scan the sign by the nearest door
                    </button>
                  )}
                </div>
              )}

              {replyText && (
                <div className="max-w-[88%] self-end rounded-[18px] rounded-br-[4px] bg-[#1a73e8] px-3.5 py-2.5 text-[14px] text-white">
                  {replyText}
                </div>
              )}

              {hasMap && result && (
                <div className="rounded-[16px] border border-[#e2e5ea] bg-white p-3.5">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="text-[17px] font-semibold text-[#202124]">{result.title}</div>
                      <div className="mt-0.5 text-[12.5px] text-[#5f6368]">
                        {applied ? 'Applied to the map' : result.sub}
                      </div>
                    </div>
                    {(result.kind === 'impact' || result.kind === 'edit') && (
                      <span
                        className={clsx(
                          'rounded-full px-2.5 py-1 text-[11px] font-semibold',
                          applied ? 'bg-[#e6f4ea] text-[#137333]' : 'bg-[#fef7e0] text-[#b06000]',
                        )}
                      >
                        {applied ? 'Applied' : 'Preview'}
                      </span>
                    )}
                  </div>

                  <div className="mt-2.5 flex items-start gap-2 rounded-[10px] bg-[#f8f9fa] px-3 py-2 text-[12.5px] leading-[1.4] text-[#3c4043]">
                    <Sparkles className="mt-0.5 size-3.5 shrink-0 text-[#1a73e8]" aria-hidden />
                    <span>{result.understood}</span>
                  </div>

                  {speaking && <SpeakingBanner onStop={onStopSpeech} />}

                  {result.kind === 'route' && (
                    <>
                      <div className="mt-2.5 flex flex-wrap gap-1.5">
                        {steps.length > 0 && (
                          <button
                            type="button"
                            onClick={() => (isWalk ? onExitWalk() : onStartWalk())}
                            className="inline-flex items-center gap-1.5 rounded-full bg-[#1a73e8] px-3.5 py-1.5 text-[12px] font-semibold text-white"
                          >
                            <Navigation className="size-3" aria-hidden />
                            {isWalk ? 'Exit walk mode' : 'Walk step by step'}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() =>
                            speaking ? onStopSpeech() : onSpeakText(result.say ?? result.understood)
                          }
                          className={clsx(
                            'inline-flex items-center gap-1.5 rounded-full border border-[#e2e5ea] px-3 py-1.5 text-[12px] font-medium text-[#202124]',
                            speaking ? 'bg-[#e8f0fe]' : 'bg-white',
                          )}
                        >
                          <Volume2 className="size-3.5 text-[#1a73e8]" aria-hidden /> Read aloud
                        </button>
                        {result.followUp && (
                          <button
                            type="button"
                            onClick={() => onSubmit(result.followUp)}
                            className="inline-flex items-center gap-1.5 rounded-full border border-[#d2e3fc] bg-[#e8f0fe] px-3 py-1.5 text-[12px] font-medium text-[#1a73e8]"
                          >
                            <Sparkles className="size-3 text-[#1a73e8]" aria-hidden /> {result.followLabel}
                          </button>
                        )}
                      </div>

                      <div className="mt-2.5 divide-y divide-[#f1f3f4]">
                        {steps.map((st, idx) => (
                          <button
                            key={idx}
                            type="button"
                            onClick={() => onSelectStep(idx)}
                            className={clsx(
                              'flex w-full items-center gap-2.5 rounded-lg px-1.5 py-2 text-left text-[13px] transition-colors',
                              isWalk && idx === stepIdx ? 'bg-[#e8f0fe]/60 font-medium' : 'hover:bg-[#f8f9fa]',
                            )}
                          >
                            <span
                              className={clsx(
                                'grid size-[22px] shrink-0 place-items-center rounded-full text-[11px] font-semibold',
                                isWalk && idx === stepIdx
                                  ? 'bg-[#1a73e8] text-white'
                                  : 'bg-[#e8f0fe] text-[#1a73e8]',
                              )}
                            >
                              {idx + 1}
                            </span>
                            <span className="flex-1 text-[#202124]">
                              {st.text}
                              {result.floors.length > 1 && activeIndex && (
                                <span className="ml-1.5 rounded-full bg-[#f1f3f4] px-1.5 py-0.5 text-[10px] text-[#5f6368]">
                                  {floorNameOf(activeIndex, st.floorId)}
                                </span>
                              )}
                            </span>
                            {st.d > 0 && <span className="text-[12px] text-[#5f6368]">{fmtM(st.d)}</span>}
                          </button>
                        ))}
                      </div>
                    </>
                  )}

                  {(result.kind === 'impact' || result.kind === 'edit') && (
                    <>
                      {result.stats && result.stats.length > 0 && (
                        <div className="mt-2.5 flex gap-2">
                          {result.stats.map((st, idx) => (
                            <div key={idx} className="flex-1 rounded-xl bg-[#f8f9fa] p-3">
                              <div
                                className={clsx(
                                  'text-[22px] font-bold',
                                  result.kind === 'impact'
                                    ? idx === 0
                                      ? 'text-[#d93025]'
                                      : 'text-[#b06000]'
                                    : 'text-[#1a73e8]',
                                )}
                              >
                                {st.n}
                              </div>
                              <div className="mt-0.5 text-[12px] text-[#5f6368]">{st.label}</div>
                            </div>
                          ))}
                        </div>
                      )}
                      {result.note && (
                        <div className="mt-2 text-[12.5px] text-[#5f6368]">{result.note}</div>
                      )}
                      {result.confirm ? (
                        !applied ? (
                          <div className="mt-3 flex gap-2">
                            <button
                              type="button"
                              onClick={onConfirmChange}
                              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-[#1a73e8] p-2.5 text-[13px] font-semibold text-white"
                            >
                              <Check className="size-3.5" aria-hidden /> {result.confirm}
                            </button>
                            <button
                              type="button"
                              onClick={onReset}
                              className="rounded-xl border border-[#e2e5ea] bg-white px-3.5 py-2.5 text-[13px] text-[#5f6368]"
                            >
                              Not now
                            </button>
                          </div>
                        ) : (
                          <div className="mt-3 flex items-center justify-between gap-2 rounded-xl bg-[#e6f4ea] px-3 py-2.5 text-[12.5px] text-[#137333]">
                            <span className="flex items-center gap-1.5">
                              <Check className="size-3.5 shrink-0" aria-hidden />
                              {result.applied}
                            </span>
                            <button
                              type="button"
                              onClick={onUndoChange}
                              className="shrink-0 rounded-full border border-[#137333]/30 bg-white px-2.5 py-1 text-[12px] font-semibold text-[#137333]"
                            >
                              {result.undo}
                            </button>
                          </div>
                        )
                      ) : null}
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Bottom Composer */}
        <div className="border-t border-[#eef0f3] px-4 py-3">
          <div className="flex items-center gap-2 rounded-[24px] border border-[#e2e5ea] bg-[#f8f9fa] p-1.5 pl-4">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSubmit()}
              placeholder="Ask anything, or tell me what changed…"
              className="flex-1 bg-transparent text-[14px] text-[#202124] outline-none placeholder:text-[#5f6368]"
            />
            <button
              type="button"
              onClick={onStartVoice}
              title="Voice input"
              className="grid size-9 place-items-center rounded-full text-[#5f6368] hover:bg-[#e8eaed]"
            >
              <Mic className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => onSubmit()}
              aria-label="Send"
              className="grid size-9 place-items-center rounded-full bg-[#1a73e8] text-white"
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Right Column: Map Canvas (opens when there is a result/walk, calm empty state otherwise) */}
      <main className="relative flex-1 overflow-hidden bg-[#f4f5f7]">
        {hasMap && result ? (
          <>
            <MapView />

            {/* Top Floating Map Header */}
            <div className="pointer-events-none absolute inset-x-5 top-4 z-20 flex items-center justify-between gap-3 [&>*]:pointer-events-auto">
              <div className="flex items-center gap-3.5 rounded-2xl border border-[#e2e5ea] bg-white/95 px-4 py-3 shadow-[0_4px_20px_rgba(32,33,36,0.1)] backdrop-blur">
                <div>
                  <div className="text-[11px] font-semibold tracking-[0.04em] text-[#5f6368] uppercase">
                    {result.buildingName}
                  </div>
                  <div className="mt-0.5 text-[16px] font-semibold text-[#202124]">
                    {result.title}{' '}
                    <span className="font-normal text-[#5f6368]">· {result.sub}</span>
                  </div>
                </div>

                {result.floors.length > 1 && (
                  <div className="flex gap-1 rounded-full bg-[#f1f3f4] p-1">
                    {result.floors.map((fid) => (
                      <button
                        key={fid}
                        type="button"
                        onClick={() => setFloorState(fid)}
                        className={clsx(
                          'rounded-full px-3 py-1 text-[12px] font-medium transition-colors',
                          fid === activeFloor ? 'bg-[#1a73e8] text-white' : 'text-[#5f6368]',
                        )}
                      >
                        {activeIndex ? floorNameOf(activeIndex, fid) : fid}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {(result.kind === 'impact' || result.kind === 'edit') && (
                <div
                  className={clsx(
                    'rounded-full px-3.5 py-2 text-[12px] font-semibold shadow-[0_2px_10px_rgba(32,33,36,0.08)]',
                    applied ? 'bg-[#e6f4ea] text-[#137333]' : 'bg-[#fef7e0] text-[#b06000]',
                  )}
                >
                  {applied ? 'Applied to map' : 'Preview · not applied yet'}
                </div>
              )}
            </div>

            {/* Desktop Walk Mode step banner at bottom-center */}
            {isWalk && curStep && (
              <div className="absolute bottom-14 left-1/2 z-20 flex w-[460px] max-w-[calc(100%-40px)] -translate-x-1/2 items-center gap-3 rounded-2xl bg-[#1a73e8] px-4 py-3 text-white shadow-[0_8px_24px_rgba(26,115,232,0.35)]">
                <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-white/20">
                  <StepIconGlyph icon={curStep.icon} className="size-5 text-white" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-medium tracking-[0.04em] uppercase opacity-85">
                    Step {stepIdx + 1} of {steps.length} ·{' '}
                    {activeIndex ? floorNameOf(activeIndex, curStep.floorId) : curStep.floorId}
                  </div>
                  <div className="truncate text-[15px] font-semibold">{curStep.text}</div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    disabled={stepIdx === 0}
                    onClick={() => onMoveStep(-1)}
                    className={clsx(
                      'grid size-8 place-items-center rounded-lg bg-white/15 hover:bg-white/25',
                      stepIdx === 0 && 'opacity-40',
                    )}
                  >
                    <ChevronLeft className="size-4" />
                  </button>
                  <button
                    type="button"
                    disabled={stepIdx >= steps.length - 1}
                    onClick={() => onMoveStep(1)}
                    className={clsx(
                      'grid size-8 place-items-center rounded-lg bg-white/15 hover:bg-white/25',
                      stepIdx >= steps.length - 1 && 'opacity-40',
                    )}
                  >
                    <ChevronRight className="size-4" />
                  </button>
                  <button
                    type="button"
                    onClick={onExitWalk}
                    className="grid size-8 place-items-center rounded-lg bg-white/15 hover:bg-white/25"
                  >
                    <X className="size-4" />
                  </button>
                </div>
              </div>
            )}

            {/* Bottom-left Map Legend */}
            <div className="pointer-events-none absolute bottom-4 left-5 z-20 flex items-center gap-3.5 rounded-full border border-[#e2e5ea] bg-white/95 px-3.5 py-2 text-[12px] text-[#5f6368] shadow-[0_2px_10px_rgba(32,33,36,0.08)] backdrop-blur">
              {result.kind === 'route' && (
                <>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-[3px] w-3.5 rounded-sm bg-[#1a73e8]" /> Route
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2.5 rounded-full border-2 border-[#1a73e8] bg-white" /> Start
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2.5 rounded-full bg-[#d93025]" /> Destination
                  </span>
                </>
              )}
              {result.kind === 'impact' && (
                <>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-3 rounded-sm border border-dashed border-[#d93025] bg-[#fce8e6]" /> Out of service
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-3 rounded-sm border border-[#e37400] bg-[#fef7e0]" /> Affected rooms
                  </span>
                </>
              )}
              {result.kind === 'edit' && (
                <>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-3 rounded-full border-2 border-[#1a73e8] bg-[#e8f0fe]" /> Proposed door
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-3 rounded-sm border border-[#1a73e8] bg-[#e8f0fe]" /> Connected rooms
                  </span>
                </>
              )}
            </div>
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center p-10 text-center text-[#5f6368]">
            <div className="grid size-14 place-items-center rounded-2xl border border-[#e2e5ea] bg-white shadow-[0_2px_10px_rgba(32,33,36,0.05)]">
              <Compass className="size-6 text-[#1a73e8]" aria-hidden />
            </div>
            <div className="mt-4 text-[16px] font-semibold text-[#202124]">
              The map opens when there’s something to show
            </div>
            <div className="mt-1.5 max-w-[340px] text-[13.5px] leading-[1.5]">
              Ask for a place, a route, or a change on the left. Only the floor you need will appear — not the whole building at once.
            </div>
            <button
              type="button"
              onClick={onOpenBrowseMap}
              className="mt-5 inline-flex items-center gap-1.5 rounded-full border border-[#e2e5ea] bg-white px-4 py-2 text-[13px] font-medium text-[#202124] shadow-xs hover:bg-[#f8f9fa]"
            >
              <MapIcon className="size-3.5 text-[#1a73e8]" aria-hidden /> Browse floor plan
            </button>
          </div>
        )}
      </main>
    </>
  )
}
