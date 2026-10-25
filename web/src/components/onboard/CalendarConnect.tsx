// Facility-manager demo, scene 2.4–2.8 (docs/synera/pitch/DEMO_PLAN.md): connect the building calendar, watch
// the week's bookings land on their rooms, accept the nudge for a waiting request, see the plan on the map, book.
// The calendar is demo data served by the API (data/calendars/<building>.json); the plan is computed live.
import clsx from 'clsx'
import {
  ArrowRight,
  CalendarDays,
  Check,
  Coffee,
  Footprints,
  Loader2,
  Lock,
  Presentation,
  Toilet,
  Users,
  UtensilsCrossed,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  bookPlan,
  fetchCalendar,
  planRequest,
  type BookingRequest,
  type BuildingCalendar,
  type CalendarEvent,
  type EventPlan,
  type PlanRow,
} from '../../api/client'
import { floorOf } from '../../lib/sceneIndex'
import { useApp, type RoomChip } from '../../store'
import { MapView } from '../map/MapView'

type Stage = 'connect' | 'connecting' | 'loading' | 'loaded' | 'planning' | 'plan' | 'booked'

const THINKING = [
  'Reading the request',
  'Finding rooms free on the day',
  'Checking seats and screens',
  'Keeping the teams close to the main room',
  'Finding lunch, a kitchen and toilets',
  'Checking the walk is step-free',
]

const ROLE: Record<PlanRow['role'], { label: string; icon: ReactNode; tone: RoomChip['tone'] }> = {
  main: { label: 'Kickoff & pitches', icon: <Presentation className="size-4" />, tone: 'main' },
  breakout: { label: 'Team room', icon: <Users className="size-4" />, tone: 'plan' },
  catering: { label: 'Lunch', icon: <UtensilsCrossed className="size-4" />, tone: 'service' },
  kitchen: { label: 'Kitchen', icon: <Coffee className="size-4" />, tone: 'service' },
  toilets: { label: 'Toilets', icon: <Toilet className="size-4" />, tone: 'service' },
}
const BOOKABLE: PlanRow['role'][] = ['main', 'breakout', 'catering']

const EQUIPMENT: Record<string, string> = {
  projector: 'Projector',
  screen: 'Screen',
  whiteboard: 'Whiteboard',
  microphones: 'Mics',
  video_call: 'Video calls',
  catering: 'Catering',
}
const DAY_INK = 'text-[#b4541a]' // the sun's orange, dark enough for small text on white (≈5:1)

/** "Seminarraum 3 SEM3 (Floor 1 …)" -> "Seminarraum 3": no floor, and no room ref the name already carries. */
function nameOf(label: string) {
  const words = label.split(' (')[0].split(' ')
  return words.length > 1 && /^[A-Z0-9-]+$/.test(words[words.length - 1]) ? words.slice(0, -1).join(' ') : words.join(' ')
}
const clip = (text: string, n = 16) => (text.length > n ? `${text.slice(0, n - 1)}…` : text)
const when = (e: CalendarEvent) => (e.when === 'all day' ? 'All day' : `${e.from}–${e.until}`)
const roleText = (row: PlanRow) => (row.role === 'breakout' ? `${row.teams ?? ''} teams` : ROLE[row.role].label)
const initials = (name: string) =>
  name
    .split(' ')
    .map((w) => w[0])
    .join('')
    .slice(0, 2)

export function CalendarConnect({ buildingId, onDone }: { buildingId: string; onDone: (ask?: string) => void }) {
  const index = useApp((s) => s.index)
  const scene = useApp((s) => s.scene)
  const floorId = useApp((s) => s.floorId)
  const setRoomChips = useApp((s) => s.setRoomChips)
  const [stage, setStage] = useState<Stage>('connect')
  const [cal, setCal] = useState<BuildingCalendar | null>(null)
  const [shown, setShown] = useState(0)
  const [nudge, setNudge] = useState(false)
  const [think, setThink] = useState(0)
  const [plan, setPlan] = useState<EventPlan | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Bookings land floor by floor from the ground up, so the map can follow them.
  const events = useMemo(() => {
    if (!cal || !index) return []
    const level = (id: string) => index.floorById.get(floorOf(index, id) ?? '')?.level ?? 0
    return [...cal.events].sort((a, b) => level(a.room_id) - level(b.room_id) || a.date.localeCompare(b.date))
  }, [cal, index])
  const landed = events.slice(0, shown)
  const request: BookingRequest | undefined = cal?.requests[0]
  const sender = request?.from.split(',')[0] ?? ''

  // The list reads by day, whatever order the bookings landed in.
  const byDay = useMemo(() => {
    const days = new Map<string, CalendarEvent[]>()
    const sorted = [...landed].sort((a, b) => a.date.localeCompare(b.date) || (a.from ?? '').localeCompare(b.from ?? ''))
    for (const e of sorted) days.set(e.date, [...(days.get(e.date) ?? []), e])
    return [...days]
  }, [landed])

  // Clean map on the way in and out; the camera keeps the building clear of the header and the panel.
  useEffect(() => {
    useApp.getState().clearMap()
    useApp.getState().setMapInsets({ top: 90, right: 430, bottom: 24, left: 24 })
    return () => {
      useApp.getState().setRoomChips([])
      useApp.getState().setMapInsets(null)
      useApp.getState().clearMap()
    }
  }, [])

  const connect = async () => {
    setStage('connecting')
    setError(null)
    try {
      const [c] = await Promise.all([fetchCalendar(buildingId), new Promise((r) => setTimeout(r, 1400))])
      if (!c.connected) throw new Error('This building has no room calendar.')
      setCal(c)
      setStage('loading')
    } catch (e) {
      setError(String(e))
      setStage('connect')
    }
  }

  // 2.5: one booking every 140 ms; the map shows the floor it lands on.
  useEffect(() => {
    if (stage !== 'loading') return
    if (shown >= events.length) {
      const ground = index?.floors.find((f) => f.level === 0)?.id
      if (ground) useApp.getState().setFloor(ground)
      setStage('loaded')
      return
    }
    const t = setTimeout(() => {
      const f = index && floorOf(index, events[shown].room_id)
      if (f && f !== useApp.getState().floorId) useApp.getState().setFloor(f)
      setShown((n) => n + 1)
    }, 140)
    return () => clearTimeout(t)
  }, [stage, shown, events, index])

  // 2.6: the request comes in a moment after the week has landed.
  useEffect(() => {
    if (stage !== 'loaded' || plan) return
    const t = setTimeout(() => setNudge(true), 1600)
    return () => clearTimeout(t)
  }, [stage, plan])

  // Chips: a room's week while loading (one fact, under its name); the plan's numbered rooms once planned.
  useEffect(() => {
    if (plan && (stage === 'plan' || stage === 'booked')) {
      const booked = stage === 'booked'
      const chips: RoomChip[] = plan.plan.map((row, i) => ({
        id: row.room_id,
        text: `${booked && BOOKABLE.includes(row.role) ? '✓' : i + 1}  ${nameOf(row.room)}`,
        tone: booked && BOOKABLE.includes(row.role) ? 'booked' : ROLE[row.role].tone,
        replacesLabel: true,
      }))
      for (const a of plan.avoided) chips.push({ id: a.room_id, text: 'Booked', tone: 'busy', replacesLabel: true })
      setRoomChips(chips)
      return
    }
    const byRoom = new Map<string, CalendarEvent[]>()
    for (const e of landed) byRoom.set(e.room_id, [...(byRoom.get(e.room_id) ?? []), e])
    setRoomChips(
      [...byRoom].map(([id, es]) => {
        const now = es.find((e) => e.status === 'on now')
        return {
          id,
          text: nameOf(es[0].room),
          sub: now ? `Now · ${clip(now.event)}` : `${es.length} this week`,
          tone: 'event' as const,
          replacesLabel: true,
        }
      }),
    )
  }, [landed.length, plan, stage]) // eslint-disable-line react-hooks/exhaustive-deps -- landed follows shown

  const planIt = async () => {
    if (!request) return
    setNudge(false)
    setStage('planning')
    setThink(0)
    const ticker = setInterval(() => setThink((n) => Math.min(n + 1, THINKING.length)), 420)
    try {
      const [p] = await Promise.all([
        planRequest(buildingId, request.id),
        new Promise((r) => setTimeout(r, THINKING.length * 420 + 200)),
      ])
      setPlan(p)
      setStage('plan')
      // Show the main room's floor and fit its planned rooms (one floor: the map stays flat with its chips).
      const main = p.plan.find((r) => r.role === 'main')
      const f = main && index ? floorOf(index, main.room_id) : undefined
      if (f) {
        useApp.getState().setFloor(f)
        useApp.getState().focusOn((p.highlight ?? []).filter((id) => index && floorOf(index, id) === f))
      }
    } catch (e) {
      setError(String(e))
      setStage('loaded')
    } finally {
      clearInterval(ticker)
    }
  }

  const book = async () => {
    if (!plan) return
    try {
      await bookPlan(buildingId, plan)
      setStage('booked')
    } catch (e) {
      setError(String(e))
    }
  }

  // Hovering a row shows its room: on its floor, outlined.
  const peek = (id: string) => {
    const f = index && floorOf(index, id)
    useApp.setState({ highlight: { ids: [id], label: null }, ...(f ? { floorId: f } : {}) })
  }
  const unpeek = () => useApp.setState({ highlight: { ids: [], label: null } })

  // Floor badges: bookings while the week lands, planned rooms once planned.
  const perFloor = useMemo(() => {
    const n = new Map<string, number>()
    if (!index) return n
    const ids = plan && (stage === 'plan' || stage === 'booked') ? plan.plan.map((r) => r.room_id) : landed.map((e) => e.room_id)
    for (const id of ids) {
      const f = floorOf(index, id)
      if (f) n.set(f, (n.get(f) ?? 0) + 1)
    }
    return n
  }, [landed, index, plan, stage])

  const mainRow = plan?.plan.find((r) => r.role === 'main')
  const bookable = plan?.plan.filter((r) => BOOKABLE.includes(r.role)) ?? []

  return (
    <div className="fixed inset-0 bg-map-bg text-ink">
      <MapView hideControls />

      {/* Building and floors */}
      <div className="absolute top-4 left-4 z-20 flex flex-wrap items-center gap-3 rounded-3xl bg-surface/95 px-4 py-3 shadow-[0_12px_40px_rgb(43_38_64/0.10)] backdrop-blur">
        <div className="guide-orb anim-breathe size-8 rounded-full" />
        <div className="mr-1">
          <div className="text-[11px] font-semibold tracking-[0.04em] text-ink-soft uppercase">Your building</div>
          <div className="font-display text-[17px] font-semibold" title={scene?.name}>
            {scene?.name.split(' (')[0] ?? 'Munich Urban Colab'}
          </div>
        </div>
        <div className="flex gap-1 rounded-full bg-map-bg p-1">
          {index?.floors.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => useApp.getState().setFloor(f.id)}
              className={clsx(
                'flex min-h-9 items-center rounded-full px-3.5 text-[13px] font-medium transition-colors',
                f.id === floorId ? 'bg-accent text-white' : 'text-ink-soft hover:bg-accent-soft',
              )}
            >
              {f.level === 0 ? 'Ground' : `Floor ${f.level}`}
              {(perFloor.get(f.id) ?? 0) > 0 && (
                <span className="anim-spring ml-1.5 rounded-full bg-sun px-1.5 text-[11px] font-bold text-accent">
                  {perFloor.get(f.id)}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* The flow */}
      <aside className="absolute top-4 right-4 bottom-4 z-20 flex w-[400px] max-w-[calc(100%-32px)] flex-col gap-3">
        {nudge && request && stage === 'loaded' && (
          <div className="anim-spring rounded-3xl bg-accent p-5 text-white shadow-[0_16px_44px_rgb(43_38_64/0.28)]">
            <div className="flex items-start gap-3">
              <div className="grid size-10 shrink-0 place-items-center rounded-full bg-sky-soft text-[14px] font-bold text-sky">
                {initials(sender)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[12px] font-medium text-white/75">
                  {sender} · via email · {request.received}
                </div>
                <div className="mt-1 text-[16px] leading-snug font-semibold">
                  {request.title}, {request.weekday} {request.start}–{request.end}
                </div>
                <div className="mt-1 text-[14px] text-white/80">
                  {request.people} people in {request.teams} teams. Needs a projector, screens and lunch, all
                  step-free.
                </div>
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => setNudge(false)}
                className="-m-2 grid size-10 place-items-center rounded-full text-white/70 hover:text-white"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="mt-4 flex items-center gap-2.5 rounded-2xl bg-white/10 px-3 py-2.5">
              <div className="guide-orb anim-breathe size-6 shrink-0 rounded-full" />
              <span className="text-[14px]">I can plan this from your calendar. Shall I?</span>
            </div>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={planIt}
                className="flex-1 rounded-full bg-sun py-3 font-semibold text-accent shadow-[0_6px_18px_rgb(255_138_61/0.35)]"
              >
                Plan it
              </button>
              <button
                type="button"
                onClick={() => setNudge(false)}
                className="rounded-full bg-white/20 px-5 py-3 text-[14px] font-medium text-white"
              >
                Later
              </button>
            </div>
          </div>
        )}

        <div className="flex min-h-0 flex-col rounded-3xl bg-surface shadow-[0_12px_40px_rgb(43_38_64/0.10)]">
          {(stage === 'connect' || stage === 'connecting') && (
            <div className="anim-rise p-6">
              <div className="grid size-12 place-items-center rounded-2xl bg-sun-soft text-sun">
                <CalendarDays className="size-6" />
              </div>
              <h2 className="mt-4 font-display text-xl font-semibold">Connect your building calendar</h2>
              <p className="mt-1.5 text-[15px] text-ink-soft">
                Every room gets its own schedule. Your guide then knows what happens where and when, for guests and
                for you.
              </p>
              <div className="mt-5 space-y-2">
                {['Microsoft 365 · room resources', 'Google Workspace · room resources'].map((p, i) => (
                  <button
                    key={p}
                    type="button"
                    disabled={stage === 'connecting'}
                    onClick={connect}
                    className={clsx(
                      'flex w-full items-center justify-between rounded-2xl px-4 py-3.5 text-left text-[15px] font-medium transition-colors',
                      i === 0 ? 'bg-sun text-accent shadow-[0_6px_18px_rgb(255_138_61/0.35)]' : 'bg-map-bg hover:bg-accent-soft',
                    )}
                  >
                    {p}
                    {stage === 'connecting' && i === 0 ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
                  </button>
                ))}
              </div>
              {stage === 'connecting' && <p className="mt-3 text-[13px] text-ink-soft">Matching rooms to calendar resources…</p>}
              {error && <p className="mt-3 text-[13px] text-rose">{error}</p>}
            </div>
          )}

          {(stage === 'loading' || stage === 'loaded') && cal && (
            <>
              <div className="px-5 pt-5 pb-3">
                <div className="flex items-center gap-2 text-[13px] font-semibold text-mint">
                  <Check className="size-4" /> Calendar connected
                </div>
                <div className="mt-1 font-display text-lg font-semibold">
                  {shown} bookings this week
                  <span className="font-normal text-ink-soft"> in {new Set(landed.map((e) => e.room_id)).size} rooms</span>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
                {byDay.map(([day, es]) => (
                  <section key={day}>
                    <h3
                      className={clsx(
                        'sticky top-0 z-10 bg-surface/95 px-3 pt-3 pb-1.5 text-[12px] font-semibold tracking-[0.05em] uppercase backdrop-blur',
                        day === request?.date ? DAY_INK : 'text-ink-soft',
                      )}
                    >
                      {day === cal.today ? 'Today' : es[0].weekday} {day.slice(8)}
                      {day === request?.date && <span className="ml-2 font-medium normal-case">· {es.length} booked</span>}
                    </h3>
                    {es.map((e) => (
                      <div
                        key={`${e.room_id}-${e.event}`}
                        onMouseEnter={() => peek(e.room_id)}
                        onMouseLeave={unpeek}
                        className="anim-rise flex cursor-default items-baseline gap-3 rounded-2xl px-3 py-2 hover:bg-map-bg"
                      >
                        <div className="w-[92px] shrink-0 text-[13px] font-medium whitespace-nowrap tabular-nums text-ink-soft">{when(e)}</div>
                        <div className="min-w-0">
                          <div className="truncate text-[14px] font-semibold">{e.event}</div>
                          <div className="truncate text-[13px] text-ink-soft">{nameOf(e.room)}</div>
                        </div>
                      </div>
                    ))}
                  </section>
                ))}
              </div>
            </>
          )}

          {stage === 'planning' && request && (
            <div className="anim-rise p-6">
              <div className="flex items-center gap-3">
                <div className="guide-orb anim-breathe size-9 rounded-full" />
                <div>
                  <div className="font-display text-lg font-semibold">Planning the {request.title}</div>
                  <div className="text-[13px] text-ink-soft">
                    {request.weekday} · {request.people} people · {request.teams} teams
                  </div>
                </div>
              </div>
              <ul className="mt-5 space-y-2.5">
                {THINKING.map((s, i) => (
                  <li key={s} className={clsx('flex items-center gap-3 text-[15px]', i > think && 'opacity-35')}>
                    <span
                      className={clsx(
                        'flex size-6 items-center justify-center rounded-full text-xs',
                        i < think ? 'bg-mint-soft text-mint' : i === think ? 'guide-orb anim-breathe' : 'bg-accent-soft',
                      )}
                    >
                      {i < think ? '✓' : ''}
                    </span>
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(stage === 'plan' || stage === 'booked') && plan && (
            <>
              <div className="px-5 pt-5 pb-3">
                <div className={clsx('text-[12px] font-semibold tracking-[0.05em] uppercase', DAY_INK)}>
                  {plan.request.weekday} · {plan.request.from}–{plan.request.until}
                </div>
                <div className="mt-0.5 font-display text-xl font-semibold">{plan.request.title}</div>
                {plan.note ? (
                  <p className="mt-1 text-[13px] text-rose">{plan.note}</p>
                ) : (
                  <p className="mt-1 text-[14px] text-ink-soft">
                    {plan.request.people} people, {plan.request.teams} teams, all on one floor around the{' '}
                    {mainRow ? nameOf(mainRow.room) : 'main room'}.
                  </p>
                )}
                {!plan.note && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {checks(plan).map((c) => (
                      <span key={c} className="rounded-full bg-mint-soft px-2.5 py-1 text-[12px] font-medium text-mint">
                        ✓ {c}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 pb-3">
                {plan.plan.map((row, i) => (
                  <PlanRowView
                    key={row.room_id}
                    n={i + 1}
                    row={row}
                    equipment={cal?.rooms[row.room_id]?.equipment ?? []}
                    booked={stage === 'booked' && BOOKABLE.includes(row.role)}
                    onEnter={() => peek(row.room_id)}
                    onLeave={unpeek}
                  />
                ))}
                {plan.avoided.length > 0 && (
                  <div className="mx-1 mt-2 rounded-2xl bg-rose-soft/50 px-4 py-3">
                    <div className="mb-1.5 flex items-center gap-1.5 text-[13px] font-semibold text-rose">
                      <Lock className="size-3.5" /> Left out, already booked
                    </div>
                    {plan.avoided.map((a) => (
                      <div
                        key={a.room_id}
                        onMouseEnter={() => peek(a.room_id)}
                        onMouseLeave={unpeek}
                        className="text-[13px] leading-relaxed text-ink-soft"
                      >
                        <span className="font-medium text-ink">{nameOf(a.room)}</span> · {a.why}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="border-t border-line p-4">
                {stage === 'plan' ? (
                  <>
                    <button
                      type="button"
                      onClick={book}
                      className="w-full rounded-full bg-sun py-3.5 font-semibold text-accent shadow-[0_6px_18px_rgb(255_138_61/0.35)]"
                    >
                      Book {bookable.length} rooms for {plan.request.weekday}
                    </button>
                    <p className="mt-2 text-center text-[12px] text-ink-soft">Kitchen and toilets need no booking.</p>
                  </>
                ) : (
                  <div className="anim-spring space-y-3">
                    <div className="flex items-start gap-2.5">
                      <div className="grid size-7 shrink-0 place-items-center rounded-full bg-mint text-white">
                        <Check className="size-4" />
                      </div>
                      <div>
                        <div className="text-[15px] font-semibold">Booked. {sender} gets the confirmation.</div>
                        <div className="mt-0.5 text-[13px] text-ink-soft">
                          On {plan.request.weekday}, guests who ask for the hackathon are guided to the{' '}
                          {mainRow ? nameOf(mainRow.room) : 'main room'}.
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onDone(`Where is the ${plan.request.title} on ${plan.request.weekday}?`)}
                      className="flex w-full items-center justify-center gap-2 rounded-full bg-accent py-3.5 font-semibold text-white"
                    >
                      Try it as a guest <ArrowRight className="size-4" />
                    </button>
                  </div>
                )}
                {error && <p className="mt-2 text-[13px] text-rose">{error}</p>}
              </div>
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

/** What the plan was checked against, in a few words each (the API's `checked` spells them out). */
function checks(plan: EventPlan): string[] {
  const r = plan.request
  const teams = plan.plan.filter((p) => p.role === 'breakout').reduce((n, p) => n + (p.teams ?? 0), 0)
  const far = Math.max(0, ...plan.plan.filter((p) => p.role === 'breakout').map((p) => p.walk_m ?? 0))
  const main = plan.plan.find((p) => p.role === 'main')
  const gear = (r.needs ?? []).filter((n) => n !== 'catering').map((n) => EQUIPMENT[n] ?? n)
  return [
    `Free ${r.weekday.slice(0, 3)} ${r.from}–${r.until}`,
    ...(main?.seats ? [`${main.seats} seats for ${r.people}`] : []),
    ...(gear.length ? [gear.join(' & ')] : []),
    ...(r.teams ? [`${teams}/${r.teams} teams within ${Math.ceil(far / 10) * 10} m`] : []),
    'Kitchen & WC nearby',
    'Step-free',
  ]
}

function PlanRowView({
  n,
  row,
  equipment,
  booked,
  onEnter,
  onLeave,
}: {
  n: number
  row: PlanRow
  equipment: string[]
  booked: boolean
  onEnter: () => void
  onLeave: () => void
}) {
  const facts = [
    ...(row.seats ? [`${row.seats} seats`] : []),
    ...(row.role === 'main' || row.role === 'breakout' ? equipment.map((e) => EQUIPMENT[e] ?? e) : []),
    ...(row.role !== 'main' && row.role !== 'breakout' ? ['Step-free'] : []),
  ]
  return (
    <div onMouseEnter={onEnter} onMouseLeave={onLeave} className="anim-rise flex gap-3 rounded-2xl px-3 py-2.5 hover:bg-map-bg">
      <div
        className={clsx(
          'relative grid size-10 shrink-0 place-items-center rounded-xl',
          booked ? 'bg-mint text-white' : row.role === 'main' ? 'bg-sun text-accent' : row.role === 'breakout' ? 'bg-sun-soft text-[#9a4a12]' : 'bg-sky-soft text-sky',
        )}
      >
        {booked ? <Check className="size-4" /> : ROLE[row.role].icon}
        <span className="absolute -top-1.5 -left-1.5 grid size-5 place-items-center rounded-full bg-surface text-[11px] font-bold text-ink shadow-[0_1px_4px_rgb(43_38_64/0.2)]">
          {n}
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <div className="truncate text-[15px] font-semibold">
            {nameOf(row.room)} <span className="font-normal text-ink-soft">· {roleText(row)}</span>
          </div>
          {row.walk_m !== undefined && (
            <span className="flex shrink-0 items-center gap-1 text-[12px] font-medium tabular-nums text-ink-soft">
              <Footprints className="size-3.5" /> {Math.round(row.walk_m)} m
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {facts.map((f, i) => (
            <span
              key={f}
              className={clsx(
                'rounded-md px-1.5 py-0.5 text-[12px]',
                i === 0 && row.seats ? 'bg-accent-soft font-semibold text-ink' : 'bg-map-bg text-ink-soft',
              )}
            >
              {f}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
