// A live, interruptible voice conversation with the ElevenLabs agent (src/app/voice). ElevenLabs does the
// listening, turn-taking and speaking over WebRTC; the client tools registered here are how the agent acts
// on the app: `ask_building_guide` runs our building_guide turn (map and cards update as usual) and returns
// its answer for the agent to say. Needs a <ConversationProvider> above.
import {
  useConversationClientTool,
  useConversationControls,
  useConversationInput,
  useConversationMode,
  useConversationStatus,
} from '@elevenlabs/react'
import { useCallback, useRef, useState } from 'react'
import type { MemoryChip } from '../../lib/buildingGraph'
import type { SceneIndex } from '../../lib/sceneIndex'
import { fetchVoiceSession } from './speech'

export type VoicePhase = 'off' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'error'

/** Client tools, named as in src/app/voice/elevenlabs.py. A returned string is what the agent hears back. */
export interface VoiceTools {
  ask_building_guide: (p: { request: string }) => Promise<string>
  start_walk: () => string
  walk_step: (p: { direction: string }) => string
  apply_change: (p: { decision: string }) => Promise<string> | string
  show_floor: (p: { floor: string }) => string
  start_over: () => string
}

export interface VoiceSessionContext {
  buildingName: string
  floors: string
  location: string
  preferences: string
  keywords: string[]
}

/** Dynamic variables for the agent's prompt, and the building's own names to boost in speech recognition. */
export function sessionContext(index: SceneIndex | null, memory: MemoryChip[], buildingName: string): VoiceSessionContext {
  const here = memory.find((m) => m.spaceId)
  const prefs = memory.filter((m) => !m.spaceId).map((m) => m.text)
  const seen = new Set<string>()
  const keywords: string[] = []
  for (const name of [
    ...[...(index?.assetById.values() ?? [])].map((a) => a.name),
    ...[...(index?.spaceById.values() ?? [])].map((s) => s.name),
  ]) {
    const n = name?.trim()
    if (!n || n.length < 3 || n.length > 40 || seen.has(n.toLowerCase())) continue
    seen.add(n.toLowerCase())
    keywords.push(n)
  }
  return {
    buildingName,
    floors: index?.floors.map((f) => f.name || `level ${f.level}`).join(', ') || 'unknown',
    location: here ? here.text.replace(/^At /, '') : 'not known yet',
    preferences: prefs.length ? prefs.join('; ') : 'nothing yet',
    keywords: keywords.slice(0, 100),
  }
}

/** Captions without the voice's delivery tags ("[calmly]"), which are meant for the ear, not the eye. */
const spoken = (text: string) => text.replace(/\[[^\]]*\]\s*/g, '').trim()

const BUSY: Partial<Record<keyof VoiceTools, string>> = {
  ask_building_guide: 'Checking the building plan',
  apply_change: 'Updating the building',
}

export function useLiveVoice(tools: VoiceTools) {
  const controls = useConversationControls()
  const { status } = useConversationStatus()
  const { isSpeaking } = useConversationMode()
  const { isMuted, setMuted } = useConversationInput()
  const [preparing, setPreparing] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [userCaption, setUserCaption] = useState('')
  const [agentCaption, setAgentCaption] = useState('')
  const [lastSpeaker, setLastSpeaker] = useState<'user' | 'agent'>('agent')
  const [error, setError] = useState<string | null>(null)
  const streamed = useRef('')

  // Each tool reports "busy" while it runs, so the dock can say what the agent is waiting for. Parameters
  // arrive as the agent's LLM wrote them: every declared parameter is a string.
  const run = (name: keyof VoiceTools) => async (params: Record<string, unknown>) => {
    const label = BUSY[name]
    if (label) setBusy(label)
    try {
      const args = Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v ?? '')]))
      return await (tools[name] as (p: Record<string, string>) => string | Promise<string>)(args)
    } catch (e) {
      return `That did not work: ${e instanceof Error ? e.message : String(e)}`
    } finally {
      if (label) setBusy(null)
    }
  }
  useConversationClientTool('ask_building_guide', run('ask_building_guide'))
  useConversationClientTool('start_walk', run('start_walk'))
  useConversationClientTool('walk_step', run('walk_step'))
  useConversationClientTool('apply_change', run('apply_change'))
  useConversationClientTool('show_floor', run('show_floor'))
  useConversationClientTool('start_over', run('start_over'))

  const start = useCallback(
    async (ctx: VoiceSessionContext) => {
      setError(null)
      setPreparing(true)
      setUserCaption('')
      setAgentCaption('')
      try {
        // Ask for the microphone first, so a denied permission is a clear message, not a dead session.
        const mic = await navigator.mediaDevices.getUserMedia({ audio: true })
        mic.getTracks().forEach((t) => t.stop())
        const { token } = await fetchVoiceSession()
        controls.startSession({
          conversationToken: token,
          connectionType: 'webrtc',
          dynamicVariables: {
            building_name: ctx.buildingName,
            floors: ctx.floors,
            location: ctx.location,
            preferences: ctx.preferences,
          },
          overrides: { asr: { keywords: ctx.keywords } },
          onMessage: ({ role, message }) => {
            if (role === 'user') {
              setUserCaption(message)
              setLastSpeaker('user')
            } else {
              setAgentCaption(spoken(message))
              setLastSpeaker('agent')
            }
          },
          onAgentChatResponsePart: (part) => {
            if (part.type === 'start') streamed.current = ''
            if (part.type === 'delta') streamed.current += part.text
            if (spoken(streamed.current)) {
              setAgentCaption(spoken(streamed.current))
              setLastSpeaker('agent')
            }
          },
          onIncomingEvent: (event: { type?: string; tentative_user_transcription_event?: { user_transcript: string } }) => {
            const heard = event?.tentative_user_transcription_event?.user_transcript
            if (event?.type === 'tentative_user_transcript' && heard) {
              setUserCaption(heard)
              setLastSpeaker('user')
            }
          },
          onError: (message) => setError(message),
          onDisconnect: (details) => {
            setBusy(null)
            if (details.reason === 'error') setError(details.message)
          },
        })
      } catch (e) {
        setError(
          e instanceof DOMException && e.name === 'NotAllowedError'
            ? 'Microphone access is blocked. Allow it in the browser to talk to the building.'
            : e instanceof Error
              ? e.message
              : String(e),
        )
      } finally {
        setPreparing(false)
      }
    },
    [controls],
  )

  const live = status === 'connected'
  const phase: VoicePhase =
    preparing || status === 'connecting'
      ? 'connecting'
      : live
        ? isSpeaking
          ? 'speaking'
          : busy
            ? 'thinking'
            : 'listening'
        : error
          ? 'error'
          : 'off'

  return {
    phase,
    live,
    busy,
    error,
    dismissError: useCallback(() => setError(null), []),
    userCaption,
    agentCaption,
    lastSpeaker,
    isMuted,
    setMuted,
    start,
    stop: controls.endSession,
    /** A typed or tapped turn, sent to the agent as if spoken, so voice and screen stay one conversation. */
    say: controls.sendUserMessage,
    /** Something the person did on screen: the agent knows it, but doesn't answer it. */
    notify: controls.sendContextualUpdate,
    /** Mic and speaker levels, 0..1, for the orb. */
    levels: useCallback(
      () => ({ input: controls.getInputVolume(), output: controls.getOutputVolume() }),
      [controls],
    ),
  }
}

export type LiveVoice = ReturnType<typeof useLiveVoice>
