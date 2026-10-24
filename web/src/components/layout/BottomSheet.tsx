// Google-Maps-like draggable bottom sheet on phones; a plain right sidebar in split view (same DOM, so panels
// never remount on rotation). Synced both ways with the store's `sheet` snap; reports its covered height live.
import clsx from 'clsx'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent, type ReactNode } from 'react'
import { useApp, type SheetSnap } from '../../store'
import { useViewport } from './useViewport'

const PEEK = 140 // px above the safe-area bottom: handle + tabs + one-line summary
const FULL_GAP = 72 // px of map/search left visible above a full sheet (plus the top safe area)
const OVERDRAG = 60 // rubber-band room past the top/bottom snaps
const DURATION = 280 // ms snap animation
const SLOP = 6 // px before a gesture counts as a drag
const FLICK = 0.35 // px/ms: faster than this moves one snap in the flick direction
const ORDER: SheetSnap[] = ['peek', 'half', 'full']

type SnapHeights = Record<SheetSnap, number>

/** Visible sheet height per snap, in px from the bottom of the viewport (safe-area included). */
function snapHeights(vh: number, safeTop: number, safeBottom: number): SnapHeights {
  const peek = PEEK + safeBottom
  const full = Math.max(peek + 80, vh - FULL_GAP - safeTop)
  const half = Math.min(full - 40, Math.max(peek + 40, Math.round(vh * 0.5)))
  return { peek, half, full }
}

interface Props {
  phone: boolean
  /** Always visible at the top: drag handle area on phones (mode tabs go here). */
  header: ReactNode
  /** Shown instead of the panel body while the sheet rests at peek. */
  peek: ReactNode
  children: ReactNode
  /** Covered height in px, every frame while dragging/animating; 0 in split view. */
  onOffset: (px: number) => void
}

interface Drag {
  startY: number
  startV: number
  samples: { t: number; y: number }[]
}

function findScroller(target: EventTarget | null, root: HTMLElement): HTMLElement | null {
  for (let el = target as HTMLElement | null; el && el !== root.parentElement; el = el.parentElement) {
    const oy = getComputedStyle(el).overflowY
    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) return el
  }
  return null
}

export function BottomSheet({ phone, header, peek, children, onOffset }: Props) {
  const snap = useApp((s) => s.sheet)
  const setSheet = useApp((s) => s.setSheet)
  const { vh, safeTop, safeBottom } = useViewport()
  const h = useMemo(() => snapHeights(vh, safeTop, safeBottom), [vh, safeTop, safeBottom])

  const sheetRef = useRef<HTMLElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const visible = useRef(h[snap])
  const anim = useRef<{ raf: number; target: number } | null>(null)
  const drag = useRef<Drag | null>(null)
  const placed = useRef(false)
  // Height the content is laid out at: grows before an expanding animation, shrinks after a collapsing one,
  // so the chat input / list end sit at the visible bottom without re-layout on every frame.
  const [layoutH, setLayoutH] = useState(h[snap])
  const [dragging, setDragging] = useState(false)

  const paint = useCallback(
    (v: number) => {
      visible.current = v
      const el = sheetRef.current
      if (!el) return
      el.style.transform = `translate3d(0, ${h.full - v}px, 0)`
      // Above the floating search (z-30) only when nearly full, so its dropdown/chips stay usable otherwise.
      el.style.zIndex = v > (h.half + h.full) / 2 ? '40' : '20'
      onOffset(Math.max(0, Math.round(Math.min(v, h.full))))
    },
    [h, onOffset],
  )

  const stopAnim = () => {
    if (anim.current) cancelAnimationFrame(anim.current.raf)
    anim.current = null
  }

  const animateTo = useCallback(
    (target: number) => {
      if (anim.current?.target === target) return
      stopAnim()
      const from = visible.current
      if (target >= from) setLayoutH(target)
      const t0 = performance.now()
      const step = (now: number) => {
        const p = Math.min(1, (now - t0) / DURATION)
        const e = 1 - Math.pow(1 - p, 3)
        paint(from + (target - from) * e)
        if (p < 1) anim.current = { raf: requestAnimationFrame(step), target }
        else {
          anim.current = null
          setLayoutH(target)
        }
      }
      anim.current = { raf: requestAnimationFrame(step), target }
    },
    [paint],
  )

  // Store -> sheet: follow `sheet` (set by drags, place selection, chat focus...) and viewport changes.
  useLayoutEffect(() => {
    const el = sheetRef.current
    if (!phone) {
      stopAnim()
      placed.current = false
      if (el) {
        el.style.transform = ''
        el.style.zIndex = ''
      }
      onOffset(0)
      return
    }
    if (drag.current) return
    if (!placed.current) {
      placed.current = true
      paint(h[snap])
      setLayoutH(h[snap])
    } else animateTo(h[snap])
  }, [phone, snap, h, paint, animateTo, onOffset])

  useEffect(() => () => stopAnim(), [])

  // ---- drag (shared by the header's pointer events and the body's touch events) ----
  const begin = useCallback(
    (y: number) => {
      stopAnim()
      drag.current = { startY: y, startV: visible.current, samples: [{ t: performance.now(), y }] }
      setLayoutH(h.full)
      setDragging(true)
    },
    [h],
  )

  const move = useCallback(
    (y: number) => {
      const d = drag.current
      if (!d) return
      let v = d.startV - (y - d.startY)
      if (v > h.full) v = h.full + Math.min(OVERDRAG, (v - h.full) * 0.3)
      if (v < h.peek) v = h.peek - Math.min(OVERDRAG, (h.peek - v) * 0.3)
      paint(v)
      const now = performance.now()
      d.samples.push({ t: now, y })
      while (d.samples.length > 2 && now - d.samples[0].t > 100) d.samples.shift()
    },
    [h, paint],
  )

  const end = useCallback(() => {
    const d = drag.current
    if (!d) return
    drag.current = null
    setDragging(false)
    const first = d.samples[0]
    const last = d.samples[d.samples.length - 1]
    const vy = last.t > first.t ? (last.y - first.y) / (last.t - first.t) : 0 // + = finger moving down
    const cur = visible.current
    let target: SheetSnap
    if (vy < -FLICK) target = ORDER.find((s) => h[s] > cur + 8) ?? 'full'
    else if (vy > FLICK) target = [...ORDER].reverse().find((s) => h[s] < cur - 8) ?? 'peek'
    else target = ORDER.reduce((a, b) => (Math.abs(h[b] - cur) < Math.abs(h[a] - cur) ? b : a))
    animateTo(h[target])
    setSheet(target)
  }, [h, animateTo, setSheet])

  // Header: pointer events (touch + mouse). Taps still reach the tab buttons; a real drag swallows the click.
  const pending = useRef<{ id: number; x: number; y: number } | null>(null)
  const swallowClick = useRef(false)
  const onPointerDown = (e: PointerEvent) => {
    if (!phone || (e.pointerType === 'mouse' && e.button !== 0)) return
    pending.current = { id: e.pointerId, x: e.clientX, y: e.clientY }
    swallowClick.current = false
  }
  const onPointerMove = (e: PointerEvent) => {
    const p = pending.current
    if (!p || p.id !== e.pointerId) return
    if (!drag.current) {
      if (Math.abs(e.clientY - p.y) < SLOP) return
      begin(p.y)
      swallowClick.current = true
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        /* pointer already gone */
      }
    }
    move(e.clientY)
  }
  const onPointerUp = (e: PointerEvent) => {
    if (pending.current?.id !== e.pointerId) return
    pending.current = null
    end()
  }
  const onClickCapture = (e: MouseEvent) => {
    if (!swallowClick.current) return
    swallowClick.current = false
    e.stopPropagation()
    e.preventDefault()
  }

  // Body: native touch listeners (non-passive) so a pull-down at scrollTop 0 drags the sheet instead of
  // bouncing, and the whole body drags while peeking or when nothing scrolls.
  useEffect(() => {
    const body = bodyRef.current
    if (!phone || !body) return
    let t: { x: number; y: number; scroller: HTMLElement | null; decided: boolean; dragging: boolean } | null = null
    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) {
        t = null
        return
      }
      const { clientX: x, clientY: y } = e.touches[0]
      t = { x, y, scroller: findScroller(e.target, body), decided: false, dragging: false }
    }
    const onMove = (e: TouchEvent) => {
      if (!t || e.touches.length !== 1) return
      const { clientX: x, clientY: y } = e.touches[0]
      if (!t.decided) {
        const dx = x - t.x
        const dy = y - t.y
        if (Math.abs(dx) < SLOP && Math.abs(dy) < SLOP) return
        t.decided = true
        if (Math.abs(dx) > Math.abs(dy)) return // horizontal: chip rows, carousels
        const sc = t.scroller
        const peeking = useApp.getState().sheet === 'peek'
        if (peeking || !sc || (dy > 0 && sc.scrollTop <= 0)) {
          t.dragging = true
          begin(t.y)
        }
      }
      if (t.dragging) {
        if (e.cancelable) e.preventDefault()
        move(y)
      }
    }
    const onEnd = () => {
      if (t?.dragging) end()
      t = null
    }
    body.addEventListener('touchstart', onStart, { passive: true })
    body.addEventListener('touchmove', onMove, { passive: false })
    body.addEventListener('touchend', onEnd)
    body.addEventListener('touchcancel', onEnd)
    return () => {
      body.removeEventListener('touchstart', onStart)
      body.removeEventListener('touchmove', onMove)
      body.removeEventListener('touchend', onEnd)
      body.removeEventListener('touchcancel', onEnd)
    }
  }, [phone, begin, move, end])

  const cycle = () => setSheet(snap === 'peek' ? 'half' : snap === 'half' ? 'full' : 'half')
  const showPeek = phone && snap === 'peek' && !dragging

  return (
    <section
      ref={sheetRef}
      aria-label="Panel"
      className={clsx(
        'flex flex-col bg-surface',
        phone
          ? 'absolute inset-x-0 z-20 rounded-t-2xl shadow-[0_-1px_3px_rgba(60,64,67,0.15),0_-4px_16px_rgba(60,64,67,0.12)] will-change-transform'
          : 'relative z-10 h-full w-[380px] shrink-0 border-l border-line xl:w-[420px]',
      )}
      style={phone ? { height: h.full + OVERDRAG, bottom: -OVERDRAG } : undefined}
    >
      <div className="flex min-h-0 flex-col" style={{ height: phone ? layoutH : '100%' }}>
        <div
          className={clsx('shrink-0', phone && 'cursor-grab touch-none select-none active:cursor-grabbing')}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onClickCapture={onClickCapture}
        >
          {phone && (
            <button
              type="button"
              aria-label={snap === 'full' ? 'Shrink panel' : 'Expand panel'}
              onClick={cycle}
              className="flex h-5 w-full items-end justify-center outline-none"
            >
              <span className="h-1 w-9 rounded-full bg-line" />
            </button>
          )}
          {header}
        </div>
        <div
          ref={bodyRef}
          className="relative min-h-0 flex-1"
          style={phone ? { paddingBottom: 'env(safe-area-inset-bottom)' } : undefined}
        >
          <div className="relative h-full" aria-hidden={showPeek || undefined} inert={showPeek || undefined}>
            {children}
          </div>
          {showPeek && <div className="absolute inset-0 z-10 bg-surface pt-1">{peek}</div>}
        </div>
      </div>
    </section>
  )
}
