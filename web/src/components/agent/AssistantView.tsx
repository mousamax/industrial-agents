// The "Welcome" assistant layout (Atrium v3): the map fills the screen, the guide bar floats at the bottom, and
// one answer at a time springs up out of it. Same layout on phone and desktop, only the answer sheet moves:
// left on wide screens, above the bar on phones. Fewer objects on screen beats more options.
import clsx from 'clsx'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronDown,
  CircleDot,
  CornerUpLeft,
  CornerUpRight,
  DoorOpen,
  Flag,
  Mic,
  QrCode,
  RotateCcw,
  Volume2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { floorNameOf, fmtM, type MemoryChip, type StepIcon } from '../../lib/buildingGraph'
import { MapView } from '../map/MapView'
import { OFFLINE_DETAIL, OFFLINE_HEADLINE, shapeAnswer, workingLine } from './assistantCopy'
import type { ViewProps } from './AgentWorkspace'

const GLASS =
  'bg-white/92 backdrop-blur-xl shadow-[0_2px_6px_rgb(43_38_64/0.05),0_22px_52px_rgb(43_38_64/0.14)]'

/** Questions any building can answer, used when the building has few prepared suggestions. */
const ANYWHERE = [
  { label: 'Which room is the biggest?', text: 'Which room in this building is the biggest?' },
  { label: 'Where are the toilets?', text: 'Where is the nearest toilet?' },
  { label: 'Is anything closed today?', text: 'Is anything closed or changed in the building today?' },
]

function greeting(): string {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning.' : h < 18 ? 'Good afternoon.' : 'Good evening.'
}

function StepGlyph({ icon, className }: { icon: StepIcon; className?: string }) {
  const props = { className, 'aria-hidden': true as const }
  switch (icon) {
    case 'circle-dot':
      return <CircleDot {...props} />
    case 'arrow-up':
      return <ArrowUp {...props} />
    case 'arrow-down':
      return <ArrowDown {...props} />
    case 'corner-up-left':
      return <CornerUpLeft {...props} />
    case 'corner-up-right':
      return <CornerUpRight {...props} />
    case 'door-open':
      return <DoorOpen {...props} />
    case 'flag':
      return <Flag {...props} />
    default:
      return <ArrowRight {...props} />
  }
}

function Orb({ size = 44, mode = 'rest' }: { size?: number; mode?: 'rest' | 'work' | 'listen' }) {
  if (mode === 'work') {
    return (
      <span className="relative shrink-0" style={{ width: size, height: size }} aria-hidden>
        <span
          className="absolute inset-0 rounded-full"
          style={{
            background: 'conic-gradient(#ffb547, #ff6b5a, #ffb547 60%, transparent 61%)',
            animation: 'ttyb-spin 1.4s linear infinite',
          }}
        />
        <span className="guide-orb absolute inset-[5px] rounded-full" />
      </span>
    )
  }
  return (
    <span
      aria-hidden
      className={clsx('guide-orb shrink-0 rounded-full', mode === 'listen' ? 'anim-listen' : 'anim-breathe')}
      style={{ width: size, height: size, boxShadow: mode === 'rest' ? '0 0 0 6px rgb(255 138 61 / .14)' : undefined }}
    />
  )
}

function Capsule({ chip, onForget }: { chip: MemoryChip; onForget: () => void }) {
  const tone = chip.spaceId
    ? 'bg-sky-soft text-ink'
    : chip.stepFree
      ? 'bg-mint-soft text-mint'
      : 'bg-sun-soft text-ink'
  const dot = chip.spaceId ? 'bg-sky' : chip.stepFree ? 'bg-mint' : 'bg-sun'
  return (
    <span className={clsx('inline-flex h-8 items-center gap-1.5 rounded-full pr-1.5 pl-2.5 text-[13px] font-semibold', tone)}>
      <span className={clsx('size-2 rounded-full', dot)} />
      {chip.text}
      <button
        type="button"
        onClick={onForget}
        aria-label={`Forget ${chip.text}`}
        className="grid size-6 place-items-center rounded-full hover:bg-black/5"
      >
        <X className="size-3" aria-hidden />
      </button>
    </span>
  )
}

function TextButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[15px] font-semibold text-ink hover:bg-ink/5"
    >
      {children}
    </button>
  )
}

function PrimaryButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-14 w-full items-center justify-center gap-2 rounded-full bg-sun text-[17px] font-bold text-ink shadow-[0_10px_24px_rgb(255_138_61/0.35)] transition-transform active:scale-[0.98]"
    >
      {children}
    </button>
  )
}

export function AssistantView(props: ViewProps & { phone: boolean }) {
  const {
    phone,
    phase,
    buildingName,
    memory,
    starterList,
    userText,
    replyText,
    trace,
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
    onArrive,
  } = props

  const [showSteps, setShowSteps] = useState(false)
  const [arrived, setArrived] = useState(false)
  useEffect(() => {
    setShowSteps(false)
    setArrived(false)
  }, [result])

  const isHome = phase === 'home'
  const isListening = phase === 'listening'
  const isWorking = phase === 'working'
  const isAsk = phase === 'ask' && !!ask
  const isWalk = phase === 'walk' && !!result
  const isResult = phase === 'result' && !!result
  const offline = !!result?.offline
  const shaped = useMemo(() => (result && !offline ? shapeAnswer(result) : null), [result, offline])

  const steps = result?.steps ?? []
  const curStep = steps[stepIdx] ?? steps[0]
  const nextStep = steps[stepIdx + 1]
  const lastStep = stepIdx >= steps.length - 1
  const total = result?.route?.length ?? steps.reduce((a, s) => a + (s.d || 0), 0)
  const done = steps.slice(0, stepIdx).reduce((a, s) => a + (s.d || 0), 0)
  const stepSum = steps.reduce((a, s) => a + (s.d || 0), 0)
  // Per-step distances only when they add up to the route: two numbers that disagree are worse than one.
  const stepDistancesAgree = total > 0 && Math.abs(stepSum - total) / total < 0.2
  const progress = total > 0 ? Math.min(1, done / Math.max(stepSum, 1)) : 0
  const activeFloor = (isWalk && curStep?.floorId ? curStep.floorId : floor) ?? result?.focusFloor ?? ''

  const suggestions = useMemo(() => {
    const here = starterList.filter((s) => !props.buildingId || s.buildingId === props.buildingId)
    const prepared = here.map((s) => ({ key: s.id, label: s.chip, run: () => onRunScenario(s) }))
    const extra = ANYWHERE.map((a) => ({ key: a.label, label: a.label, run: () => onSubmit(a.text) }))
    return [...prepared, ...extra].slice(0, 4)
  }, [starterList, props.buildingId, onRunScenario, onSubmit])

  const here = memory.find((m) => m.spaceId)
  const showPanel = isWorking || isAsk || isResult
  const panelPos = phone
    ? 'absolute inset-x-2 bottom-[calc(88px+env(safe-area-inset-bottom))] max-h-[58dvh]'
    : 'absolute top-[76px] left-4 w-[400px] max-h-[calc(100dvh-180px)]'

  return (
    <div className="fixed inset-0 overflow-hidden bg-map-bg text-ink select-none">
      <div className="anim-arrive absolute inset-0">
        <MapView hideControls={phone || isWalk} />
      </div>

      {/* Top: where you are. Nothing else lives up here except the way back. */}
      {!isWalk && (
        <div className="pointer-events-none absolute inset-x-3 top-[calc(12px+env(safe-area-inset-top))] z-30 flex items-start justify-between gap-2 [&>*]:pointer-events-auto">
          <button
            type="button"
            onClick={onOpenBuildingPicker}
            className={clsx('flex h-12 items-center gap-2.5 rounded-full pr-4 pl-2', GLASS)}
          >
            <Orb size={32} />
            <span className="font-display max-w-[46vw] truncate text-[18px] font-bold whitespace-nowrap">
              {buildingName}
            </span>
            {activeIndex && activeFloor && !phone && (
              <span className="text-[14px] whitespace-nowrap text-ink-soft">{floorNameOf(activeIndex, activeFloor)}</span>
            )}
            <ChevronDown className="size-4 text-ink-soft" aria-hidden />
          </button>
          <div className="flex items-center gap-2">
            {result && result.floors.length > 1 && (
              <div role="group" aria-label="Floor" className={clsx('flex gap-1 rounded-full p-1', GLASS)}>
                {result.floors.map((fid) => (
                  <button
                    key={fid}
                    type="button"
                    aria-pressed={fid === activeFloor}
                    onClick={() => setFloorState(fid)}
                    className={clsx(
                      'h-10 rounded-full px-3.5 text-[14px] font-bold',
                      fid === activeFloor ? 'bg-ink text-white' : 'text-ink',
                    )}
                  >
                    {activeIndex ? floorNameOf(activeIndex, fid) : fid}
                  </button>
                ))}
              </div>
            )}
            {!isHome && (
              <button
                type="button"
                onClick={onReset}
                aria-label="Start over"
                className={clsx('grid size-12 place-items-center rounded-full', GLASS)}
              >
                <RotateCcw className="size-[18px]" aria-hidden />
              </button>
            )}
          </div>
        </div>
      )}

      {/* The answer: one sheet, one sentence first, one main action. */}
      {showPanel && (
        <section
          key={`${phase}-${result?.title ?? ''}-${ask?.q ?? ''}`}
          aria-live="polite"
          className={clsx(panelPos, GLASS, 'anim-spring z-20 flex flex-col gap-3 overflow-y-auto rounded-[28px] p-5')}
        >
          <p className="text-[14px] leading-snug text-ink-soft">{replyText ?? userText}</p>

          {memory.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {memory.map((m, i) => (
                <Capsule key={`${m.text}-${i}`} chip={m} onForget={() => onForgetOne(i)} />
              ))}
            </div>
          )}

          {isWorking && (
            <div className="flex items-center gap-3 py-1">
              <Orb size={28} mode="work" />
              <span className="text-[16px] text-ink-soft">{workingLine(trace)}</span>
            </div>
          )}

          {isAsk && ask && (
            <>
              <h2 className="font-display text-[24px] leading-[1.15] font-bold">{ask.q}</h2>
              <div className="flex flex-col gap-2">
                {ask.options.map((opt, i) => (
                  <button
                    key={i}
                    type="button"
                    disabled={opt.disabled}
                    onClick={() => onPickOption(opt)}
                    className="flex min-h-14 items-center justify-between rounded-2xl bg-map-bg px-4 text-left text-[16px] font-semibold disabled:opacity-40"
                  >
                    {opt.label}
                    <span className="text-[13px] font-medium text-ink-soft">{opt.sub.split(' · ')[0]}</span>
                  </button>
                ))}
              </div>
              {ask.scan && (
                <TextButton onClick={onScanDoor}>
                  <QrCode className="size-4" aria-hidden /> Scan the sign by the nearest door
                </TextButton>
              )}
            </>
          )}

          {isResult && offline && (
            <>
              <h2 className="font-display text-[26px] leading-[1.12] font-bold">{OFFLINE_HEADLINE}</h2>
              <p className="text-[16px] leading-relaxed">{OFFLINE_DETAIL}</p>
              <PrimaryButton onClick={() => onSubmit(userText)}>Try again</PrimaryButton>
            </>
          )}

          {isResult && result && shaped && arrived && (
            <div className="flex flex-col items-start gap-2 py-1">
              <span className="relative grid size-14 place-items-center">
                <span className="anim-ripple absolute inset-0 rounded-full bg-sun" />
                <span className="guide-orb relative grid size-14 place-items-center rounded-full">
                  <Check className="size-6 text-ink" aria-hidden />
                </span>
              </span>
              <h2 className="font-display mt-2 text-[30px] leading-[1.05] font-bold">You made it.</h2>
              <p className="text-[16px] leading-relaxed">{result.title} is right here.</p>
              <div className="mt-2 w-full">
                <PrimaryButton onClick={onReset}>Thanks, that’s all</PrimaryButton>
              </div>
            </div>
          )}

          {isResult && result && shaped && !arrived && (
            <>
              <h2 className="font-display text-[26px] leading-[1.12] font-bold">{shaped.headline}</h2>
              {shaped.meta && <p className="-mt-1 text-[15px] font-semibold text-ink-soft">{shaped.meta}</p>}
              {shaped.detail && <p className="text-[16px] leading-relaxed">{shaped.detail}</p>}

              {result.stats && result.stats.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {result.stats.map((s, i) => (
                    <span key={i} className="rounded-2xl bg-map-bg px-3 py-2 text-[14px]">
                      <b className="font-display text-[18px]">{s.n}</b> {s.label}
                    </span>
                  ))}
                </div>
              )}

              {result.kind === 'route' && steps.length > 0 && (
                <>
                  <PrimaryButton onClick={onStartWalk}>Let’s go</PrimaryButton>
                  {showSteps && (
                    <ol className="flex flex-col">
                      {steps.map((st, i) => (
                        <li key={i}>
                          <button
                            type="button"
                            onClick={() => onSelectStep(i)}
                            className="grid w-full grid-cols-[24px_1fr_auto] items-start gap-3 py-2 text-left"
                          >
                            <span className="mt-1 flex justify-center">
                              <span className={clsx('size-3 rounded-full', i === 0 ? 'guide-orb' : 'bg-sun/30')} />
                            </span>
                            <span className="text-[15px] leading-snug">{st.text}</span>
                            {stepDistancesAgree && st.d > 0 && (
                              <span className="text-[13px] text-ink-soft">{fmtM(st.d)}</span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}

              {(result.kind === 'impact' || result.kind === 'edit') && result.confirm && !applied && (
                <PrimaryButton onClick={onConfirmChange}>{result.confirm}</PrimaryButton>
              )}
              {applied && result.applied && (
                <div className="flex items-center justify-between gap-2 rounded-2xl bg-mint-soft px-4 py-3 text-[15px] text-mint">
                  <span className="flex items-center gap-2 font-semibold">
                    <Check className="size-4" aria-hidden /> {result.applied}
                  </span>
                  <button type="button" onClick={onUndoChange} className="min-h-11 rounded-full px-3 font-bold">
                    {result.undo ?? 'Undo'}
                  </button>
                </div>
              )}

              <div className="-mx-3 flex flex-wrap">
                <TextButton onClick={() => (speaking ? onStopSpeech() : onSpeakText(result.say ?? shaped.headline))}>
                  <Volume2 className="size-4" aria-hidden /> {speaking ? 'Stop reading' : 'Read aloud'}
                </TextButton>
                {result.kind === 'route' && steps.length > 0 && (
                  <TextButton onClick={() => setShowSteps((v) => !v)}>
                    {showSteps ? 'Hide the steps' : `See all ${steps.length} steps`}
                  </TextButton>
                )}
                {result.confirm && !applied && <TextButton onClick={onReset}>Not now</TextButton>}
                {result.followUp && (
                  <TextButton onClick={() => onSubmit(result.followUp)}>{result.followLabel}</TextButton>
                )}
              </div>
            </>
          )}
        </section>
      )}

      {/* Walk mode: one instruction at a time, the map follows. */}
      {isWalk && curStep && (
        <>
          <div
            className={clsx(
              'anim-spring absolute inset-x-3 top-[calc(12px+env(safe-area-inset-top))] z-30 mx-auto flex max-w-[560px] items-center gap-3 rounded-[26px] p-4',
              GLASS,
            )}
          >
            <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-sun-soft">
              <StepGlyph icon={curStep.icon} className="size-6 text-ink" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-display text-[22px] leading-[1.12] font-bold">{curStep.text}</p>
              <p className="mt-0.5 truncate text-[14px] text-ink-soft">
                {nextStep ? `Then ${nextStep.text.charAt(0).toLowerCase()}${nextStep.text.slice(1)}` : 'Almost there'}
              </p>
            </div>
            <button
              type="button"
              onClick={onExitWalk}
              aria-label="End the route"
              className="grid size-11 shrink-0 place-items-center rounded-full bg-map-bg"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>

          <div
            className={clsx(
              'absolute inset-x-3 bottom-[calc(12px+env(safe-area-inset-bottom))] z-30 mx-auto flex max-w-[560px] flex-col gap-3 rounded-[26px] p-4',
              GLASS,
            )}
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-[16px] font-bold">
                {total > done ? `${fmtM(total - done)} to go` : 'You’re there'}
              </span>
              <TextButton onClick={() => (speaking ? onStopSpeech() : onSpeakText(curStep.text))}>
                <Volume2 className="size-4" aria-hidden /> {speaking ? 'Quiet' : 'Say it'}
              </TextButton>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-map-bg">
              <span
                className="block h-full rounded-full transition-[width] duration-500"
                style={{ width: `${Math.max(4, progress * 100)}%`, background: 'linear-gradient(90deg,#ffb547,#ff6b5a)' }}
              />
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={stepIdx === 0}
                onClick={() => onMoveStep(-1)}
                className="h-14 flex-1 rounded-full bg-map-bg text-[16px] font-semibold disabled:opacity-40"
              >
                Back
              </button>
              {lastStep ? (
                <button
                  type="button"
                  onClick={() => {
                    setArrived(true)
                    onArrive?.()
                    onExitWalk()
                  }}
                  className="h-14 flex-[1.6] rounded-full bg-sun text-[17px] font-bold shadow-[0_10px_24px_rgb(255_138_61/0.35)]"
                >
                  I’m here
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => onMoveStep(1)}
                  className="h-14 flex-[1.6] rounded-full bg-sun text-[17px] font-bold shadow-[0_10px_24px_rgb(255_138_61/0.35)]"
                >
                  Next
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {/* The guide bar: always in the same place, the only way in. */}
      {!isWalk && (
        <div className="absolute bottom-[calc(12px+env(safe-area-inset-bottom))] left-1/2 z-30 flex w-[min(680px,calc(100%-16px))] -translate-x-1/2 flex-col items-center gap-3">
          {isHome && (
            <>
              <div className="anim-rise rounded-[24px] rounded-bl-[8px] bg-white/94 px-5 py-4 text-center shadow-[0_12px_32px_rgb(43_38_64/0.12)] backdrop-blur-xl [animation-delay:.5s]">
                <p className="font-display text-[20px] leading-[1.15] font-bold sm:text-[26px]">
                  {greeting()} {here ? `You’re at ${here.text.replace(/^At /, '')}.` : `Welcome to ${buildingName}.`}
                </p>
                <p className="mt-1 text-[16px] text-ink-soft">What can I help you find?</p>
              </div>
              <div
                className={clsx(
                  'flex gap-2',
                  phone ? '-mx-2 w-[calc(100%+16px)] overflow-x-auto px-2 pb-1 [scrollbar-width:none]' : 'flex-wrap justify-center',
                )}
              >
                {suggestions.map((s, i) => (
                  <button
                    key={s.key}
                    type="button"
                    onClick={s.run}
                    style={{ animationDelay: `${0.65 + i * 0.08}s` }}
                    className="anim-rise h-11 shrink-0 rounded-full bg-white/90 px-4 text-[15px] font-semibold whitespace-nowrap shadow-[0_4px_14px_rgb(43_38_64/0.08)] backdrop-blur hover:bg-white"
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </>
          )}

          <div className={clsx('flex h-16 w-full items-center gap-3 rounded-full pr-2 pl-2.5', GLASS)}>
            {isListening ? (
              <>
                <Orb size={40} mode="listen" />
                <span className="font-display min-w-0 flex-1 truncate text-[19px] font-semibold">
                  {listenText || 'Listening…'}
                </span>
                <button
                  type="button"
                  onClick={onReset}
                  aria-label="Cancel"
                  className="grid size-12 shrink-0 place-items-center rounded-full bg-map-bg"
                >
                  <X className="size-4" aria-hidden />
                </button>
                <button
                  type="button"
                  onClick={onFinishVoiceEarly}
                  aria-label="Done speaking"
                  className="grid size-12 shrink-0 place-items-center rounded-full bg-sun"
                >
                  <Check className="size-5" aria-hidden />
                </button>
              </>
            ) : (
              <>
                <Orb size={44} mode={isWorking ? 'work' : 'rest'} />
                <label htmlFor="assistant-ask" className="sr-only">
                  Ask about this building
                </label>
                <input
                  id="assistant-ask"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && onSubmit()}
                  placeholder={isHome ? (phone ? 'Ask me anything' : 'Ask me anything about this building') : 'Ask a follow-up'}
                  autoComplete="off"
                  className="min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-ink-soft/80"
                />
                <button
                  type="button"
                  onClick={() => (input.trim() ? onSubmit() : onStartVoice())}
                  aria-label={input.trim() ? 'Send' : 'Talk'}
                  className={clsx(
                    'grid size-12 shrink-0 place-items-center rounded-full transition-colors',
                    input.trim() ? 'bg-ink text-white' : 'bg-map-bg text-ink',
                  )}
                >
                  {input.trim() ? <ArrowUp className="size-5" aria-hidden /> : <Mic className="size-5" aria-hidden />}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
