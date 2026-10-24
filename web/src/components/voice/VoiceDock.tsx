// The live call on screen: an orb that breathes with the mic and the agent's voice, what it heard and says as
// live captions, and mute / hang up. `VoiceBar` takes the guide bar's place in the assistant layout while a
// call is on; `VoiceDock` floats over the map in the classic layout (`?ui=classic`).
import clsx from 'clsx'
import { Mic, MicOff, PhoneOff, TriangleAlert, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { LiveVoice } from './useLiveVoice'

export type DockPlacement = 'desktop' | 'desktop-walk' | 'phone-bottom' | 'phone-map' | 'phone-walk'

const DESKTOP = 'fixed left-[calc(210px+50vw)] w-[min(520px,calc(100vw-470px))] -translate-x-1/2'
const PLACEMENT: Record<DockPlacement, string> = {
  desktop: `${DESKTOP} bottom-6`,
  'desktop-walk': `${DESKTOP} bottom-[148px]`,
  'phone-bottom': 'fixed inset-x-3 bottom-[calc(12px+env(safe-area-inset-bottom))]',
  'phone-map': 'absolute inset-x-3 bottom-[calc(54%+10px)]',
  'phone-walk': 'absolute inset-x-3 bottom-[calc(150px+env(safe-area-inset-bottom))]',
}

/** AssistantView's floating glass, so the call bar sits in the layout like the guide bar it replaces. */
const GLASS = 'bg-white/92 backdrop-blur-xl shadow-[0_2px_6px_rgb(43_38_64/0.05),0_22px_52px_rgb(43_38_64/0.14)]'

const STATUS: Record<LiveVoice['phase'], string> = {
  off: '',
  connecting: 'Connecting…',
  listening: 'Listening',
  thinking: 'Thinking…',
  speaking: 'Speaking',
  error: 'Voice unavailable',
}

function statusOf(voice: LiveVoice): string {
  return voice.isMuted && voice.phase === 'listening' ? 'Muted' : STATUS[voice.phase]
}

/** The line to show: what the agent says (or waits for), else what it is hearing. */
function captionOf(voice: LiveVoice): { caption: string; agent: boolean } {
  const { phase } = voice
  const agent = voice.lastSpeaker === 'agent' || phase === 'speaking' || phase === 'thinking'
  if (phase === 'thinking' && voice.busy) return { caption: `${voice.busy}…`, agent }
  if (agent) return { caption: voice.agentCaption, agent }
  return { caption: voice.userCaption || (phase === 'listening' ? 'Say what you need…' : ''), agent }
}

function Orb({ voice, size = 48, warm = false }: { voice: LiveVoice; size?: number; warm?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const { phase, levels } = voice

  // Audio levels drive a CSS variable every frame, so the orb moves without re-rendering React.
  useEffect(() => {
    if (phase === 'off' || phase === 'error') return
    let raf = 0
    let smooth = 0
    const tick = () => {
      const { input, output } = levels()
      const target = phase === 'speaking' ? output : phase === 'listening' ? input : 0
      smooth += (Math.min(1, target * 1.8) - smooth) * 0.25
      ref.current?.style.setProperty('--lvl', smooth.toFixed(3))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [phase, levels])

  const spin = phase === 'thinking' || phase === 'connecting'
  return (
    <div
      ref={ref}
      className="relative grid shrink-0 place-items-center [--lvl:0]"
      style={{ width: size, height: size }}
      aria-hidden
    >
      {phase === 'connecting' && (
        <span className={clsx('absolute inset-0 animate-ping rounded-full', warm ? 'bg-sun/40' : 'bg-[#8ab4f8]/50')} />
      )}
      <span
        className={clsx('absolute inset-[-6px] rounded-full', warm ? 'bg-sun/20' : 'bg-[#8ab4f8]/25')}
        style={{ transform: 'scale(calc(0.85 + var(--lvl) * 0.55))', opacity: 'calc(0.25 + var(--lvl))' }}
      />
      <span
        className={clsx(
          'absolute inset-0 rounded-full',
          warm
            ? 'bg-[conic-gradient(from_0deg,#ffb547,#ff6b5a,#ffe29a,#ffb547)]'
            : 'bg-[conic-gradient(from_0deg,#4285f4,#a142f4,#24c1e0,#4285f4)]',
          spin && 'animate-spin [animation-duration:1.6s]',
        )}
        style={{ transform: 'scale(calc(1 + var(--lvl) * 0.22))' }}
      />
      <span
        className={clsx(
          'absolute inset-[3px] rounded-full',
          warm
            ? 'guide-orb'
            : 'bg-[radial-gradient(circle_at_32%_28%,#ffffff_0%,#aecbfa_22%,#1a73e8_62%,#0b3d91_100%)]',
          phase === 'listening' && voice.isMuted && 'grayscale',
        )}
        style={{ transform: 'scale(calc(1 + var(--lvl) * 0.12))' }}
      />
    </div>
  )
}

/** The call in the assistant layout, in the guide bar's place (`inPanel`: at the foot of the thread). */
export function VoiceBar({ voice, inPanel = false }: { voice: LiveVoice; inPanel?: boolean }) {
  if (voice.phase === 'off') return null
  const surface = inPanel ? 'bg-map-bg' : GLASS
  const round = inPanel ? 'bg-white text-ink' : 'bg-map-bg text-ink'

  if (voice.phase === 'error') {
    return (
      <div role="alert" className={clsx('flex w-full items-center gap-3 rounded-[28px] py-2 pr-2 pl-4', surface)}>
        <TriangleAlert className="size-5 shrink-0 text-rose" aria-hidden />
        <p className="min-w-0 flex-1 text-[15px] leading-snug">{voice.error}</p>
        <button
          type="button"
          onClick={voice.dismissError}
          aria-label="Dismiss"
          className={clsx('grid size-11 shrink-0 place-items-center rounded-full', round)}
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
    )
  }

  const { caption, agent } = captionOf(voice)
  return (
    <div
      role="status"
      aria-live="polite"
      className={clsx(
        'anim-spring flex w-full items-center gap-3 rounded-[28px] py-2 pr-2 pl-2.5',
        inPanel ? 'min-h-14' : 'min-h-16',
        surface,
      )}
    >
      <Orb voice={voice} size={inPanel ? 36 : 44} warm />
      <div className="min-w-0 flex-1">
        <p className="text-[12px] font-semibold text-ink-soft">
          {statusOf(voice)} <span className="font-medium opacity-70">· ElevenLabs live</span>
        </p>
        <p
          className={clsx(
            'font-display line-clamp-2 text-[17px] leading-[1.2] font-semibold',
            !agent && 'text-ink-soft',
          )}
        >
          {caption}
        </p>
      </div>
      <button
        type="button"
        onClick={() => voice.setMuted(!voice.isMuted)}
        aria-label={voice.isMuted ? 'Unmute microphone' : 'Mute microphone'}
        className={clsx(
          'grid size-11 shrink-0 place-items-center rounded-full transition-colors',
          voice.isMuted ? 'bg-ink text-white' : round,
        )}
      >
        {voice.isMuted ? <MicOff className="size-5" aria-hidden /> : <Mic className="size-5" aria-hidden />}
      </button>
      <button
        type="button"
        onClick={voice.stop}
        aria-label="End voice conversation"
        className="grid size-11 shrink-0 place-items-center rounded-full bg-rose text-white"
      >
        <PhoneOff className="size-5" aria-hidden />
      </button>
    </div>
  )
}

export function VoiceDock({ voice, placement }: { voice: LiveVoice; placement: DockPlacement }) {
  const { phase } = voice
  if (phase === 'off') return null

  if (phase === 'error') {
    return (
      <div className={clsx(PLACEMENT[placement], 'z-50')}>
        <div className="flex items-center gap-3 rounded-[22px] bg-[#202124]/95 px-4 py-3 text-white shadow-[0_10px_40px_rgba(0,0,0,0.35)] backdrop-blur">
          <TriangleAlert className="size-4 shrink-0 text-[#fdd663]" aria-hidden />
          <div className="min-w-0 flex-1 text-[13px] leading-[1.35]">{voice.error}</div>
          <button
            type="button"
            onClick={voice.dismissError}
            aria-label="Dismiss"
            className="grid size-8 shrink-0 place-items-center rounded-full bg-white/10 hover:bg-white/20"
          >
            <X className="size-4" />
          </button>
        </div>
      </div>
    )
  }

  const { caption, agent } = captionOf(voice)
  const compact = placement !== 'desktop' && placement !== 'desktop-walk'

  return (
    <div className={clsx(PLACEMENT[placement], 'z-50')} role="status" aria-live="polite">
      <div className="flex items-center gap-3 rounded-[26px] border border-white/10 bg-[#202124]/92 py-2.5 pr-2.5 pl-2.5 text-white shadow-[0_12px_44px_rgba(0,0,0,0.38)] backdrop-blur-md">
        <Orb voice={voice} />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[11px] font-semibold tracking-[0.06em] uppercase">
            <span className={clsx(phase === 'speaking' ? 'text-[#8ab4f8]' : 'text-white/75')}>{statusOf(voice)}</span>
            <span className="font-medium tracking-normal text-white/35 normal-case">· ElevenLabs live</span>
          </div>
          {!compact && voice.userCaption && agent && (
            <div className="mt-0.5 truncate text-[12px] text-white/50">“{voice.userCaption}”</div>
          )}
          <div
            className={clsx(
              'mt-0.5 text-[14px] leading-[1.35]',
              compact ? 'line-clamp-2' : 'line-clamp-3',
              agent ? 'font-medium text-white' : 'text-white/80 italic',
            )}
          >
            {caption}
          </div>
        </div>

        <button
          type="button"
          onClick={() => voice.setMuted(!voice.isMuted)}
          aria-label={voice.isMuted ? 'Unmute microphone' : 'Mute microphone'}
          className={clsx(
            'grid size-10 shrink-0 place-items-center rounded-full transition-colors',
            voice.isMuted ? 'bg-white text-[#202124]' : 'bg-white/10 text-white hover:bg-white/20',
          )}
        >
          {voice.isMuted ? <MicOff className="size-[18px]" /> : <Mic className="size-[18px]" />}
        </button>
        <button
          type="button"
          onClick={voice.stop}
          aria-label="End voice conversation"
          className="grid size-10 shrink-0 place-items-center rounded-full bg-[#ea4335] text-white hover:bg-[#d93025]"
        >
          <PhoneOff className="size-[18px]" />
        </button>
      </div>
    </div>
  )
}
