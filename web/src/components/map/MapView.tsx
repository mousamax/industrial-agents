// Indoor map of the current floor, Google Maps style (2D). Own pan/zoom camera: the world <g> transform and
// the --inv counter-scale variable are written straight to the DOM every frame, so React only re-renders
// when the zoom crosses a detail threshold (labels, asset dots), never per pan frame.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import { useApp } from '../../store'
import {
  bboxOf,
  fitCamera,
  floorGeo,
  padBBox,
  spaceAt,
  toSvg,
  unionBBox,
  type BBox,
  type Camera,
  type FloorGeo,
  type Insets,
  type Pt,
} from './geometry'
import {
  AssetDots,
  FloorBase,
  Footprints,
  HighlightDots,
  HighlightFill,
  Labels,
  RouteLines,
  RouteMarkers,
  SelectedOutline,
  SelectedPin,
} from './layers'
import { MapControls } from './MapControls'

const SEARCH_BAR = 110 // floating search bar + chips row over the top of the map
const PICKER_GUTTER = 60 // floor picker on the right edge
const FOCUS_MIN_SPAN_M = 16 // never zoom in so far that less than this many metres are visible
const MAX_K = 160 // px per metre
const ASSET_K = [12, 28] // zoom (px/m) at which machines/equipment, then furniture/fixtures appear
const TAP_SLOP = 7
const ANIM_MS = 450

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

function focusBBox(ids: string[], geo: FloorGeo, index: SceneIndex, route: ShowRoute | null): BBox | null {
  let b: BBox | null = null
  const pt = (p: Pt, pad: number) => padBBox(bboxOf([p])!, pad)
  for (const id of ids) {
    const s = geo.byId.get(id)
    if (s) b = unionBBox(b, s.bbox)
    const a = geo.assetById.get(id)
    if (a) b = unionBBox(b, pt(a.p, 2))
    const o = index.openingById.get(id)
    if (o && o.floor_id === geo.floorId) b = unionBBox(b, pt(toSvg(o.position), 2))
    const c = index.connectorById.get(id)
    if (c) for (const [f, sid] of c.stops) if (f === geo.floorId) b = unionBBox(b, geo.byId.get(sid)?.bbox ?? null)
  }
  if (route && ids === route.space_ids)
    for (const seg of route.segments) if (seg.floor_id === geo.floorId) b = unionBBox(b, bboxOf(seg.points.map(toSvg)))
  return b
}

export function MapView() {
  const scene = useApp((s) => s.scene)
  const index = useApp((s) => s.index)
  const floorId = useApp((s) => s.floorId)
  const selection = useApp((s) => s.selection)
  const highlight = useApp((s) => s.highlight)
  const route = useApp((s) => s.routeState.route)
  const focus = useApp((s) => s.focus)
  const select = useApp((s) => s.select)
  const setFloor = useApp((s) => s.setFloor)

  const geo = useMemo(
    () => (index && floorId && index.floorById.has(floorId) ? floorGeo(index, floorId) : null),
    [index, floorId],
  )
  const multiFloor = (index?.floors.length ?? 0) > 1

  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const worldRef = useRef<SVGGElement>(null)
  const cam = useRef<Camera>({ k: 1, tx: 0, ty: 0 })
  const size = useRef({ w: 0, h: 0 })
  const geoRef = useRef(geo)
  geoRef.current = geo
  const multiRef = useRef(multiFloor)
  multiRef.current = multiFloor
  const [sized, setSized] = useState(false)
  const [zoomK, setZoomK] = useState(1)
  const zoomKRef = useRef(1)
  const raf = useRef(0)

  // ---------- camera core ----------

  const insets = useCallback((): Insets => {
    const el = containerRef.current
    const sheet = el ? parseFloat(getComputedStyle(el).getPropertyValue('--sheet-offset')) || 0 : 0
    // A full sheet (e.g. while typing) leaves almost no map: fit as if it were at half height instead, so the
    // result is right once the sheet comes down rather than squeezed into a sliver.
    const maxSheet = (size.current.h || el?.clientHeight || 0) * 0.5
    return { top: SEARCH_BAR, bottom: Math.min(sheet, maxSheet) + 16, left: 16, right: multiRef.current ? PICKER_GUTTER : 16 }
  }, [])

  const floorFit = useCallback((): Camera | null => {
    const g = geoRef.current
    if (!g?.bbox || !size.current.w) return null
    return fitCamera(g.bbox, size.current.w, size.current.h, insets(), MAX_K)
  }, [insets])

  /** Zoom limits relative to the whole-floor fit; refreshed on size / floor changes, not per frame. */
  const limits = useRef({ minK: 0.01, maxK: MAX_K })
  const updateLimits = useCallback(() => {
    const fit = floorFit()
    if (fit) limits.current = { minK: fit.k * 0.5, maxK: Math.max(MAX_K, fit.k * 2) }
  }, [floorFit])

  const clamp = useCallback(
    (c: Camera): Camera => {
      const g = geoRef.current
      if (!g?.bbox || !size.current.w) return c
      const k = Math.min(Math.max(c.k, limits.current.minK), limits.current.maxK)
      // Keep at least a sliver of the floor on screen.
      const { w, h } = size.current
      const m = 60
      const b = g.bbox
      const tx = Math.min(Math.max(c.tx, m - b.maxX * k), w - m - b.minX * k)
      const ty = Math.min(Math.max(c.ty, m - b.maxY * k), h - m - b.minY * k)
      return { k, tx, ty }
    },
    [],
  )

  const apply = useCallback((settle = false) => {
    const { k, tx, ty } = cam.current
    const world = worldRef.current
    if (world) {
      world.setAttribute('transform', `translate(${tx} ${ty}) scale(${k})`)
      world.style.setProperty('--inv', String(1 / k))
    }
    if (settle || Math.abs(Math.log(k / zoomKRef.current)) > 0.18) {
      zoomKRef.current = k
      setZoomK(k)
    }
  }, [])

  const stop = useCallback(() => cancelAnimationFrame(raf.current), [])

  const set = useCallback(
    (c: Camera, settle = false) => {
      cam.current = clamp(c)
      apply(settle)
    },
    [apply, clamp],
  )

  const animateTo = useCallback(
    (target: Camera, ms = ANIM_MS) => {
      stop()
      target = clamp(target)
      const from = cam.current
      const { w, h } = size.current
      // Interpolate the world point at the screen centre linearly and the zoom geometrically.
      const c0 = [(w / 2 - from.tx) / from.k, (h / 2 - from.ty) / from.k]
      const c1 = [(w / 2 - target.tx) / target.k, (h / 2 - target.ty) / target.k]
      const t0 = performance.now()
      const tick = (now: number) => {
        const u = Math.min(1, (now - t0) / ms)
        const e = easeInOut(u)
        const k = from.k * Math.pow(target.k / from.k, e)
        const cx = c0[0] + (c1[0] - c0[0]) * e
        const cy = c0[1] + (c1[1] - c0[1]) * e
        cam.current = { k, tx: w / 2 - cx * k, ty: h / 2 - cy * k }
        apply(u >= 1)
        if (u < 1) raf.current = requestAnimationFrame(tick)
      }
      raf.current = requestAnimationFrame(tick)
    },
    [apply, clamp, stop],
  )

  const zoomBy = useCallback(
    (factor: number, sx?: number, sy?: number, animate = false) => {
      const c = cam.current
      const x = sx ?? size.current.w / 2
      const y = sy ?? size.current.h / 2
      const k = clamp({ ...c, k: c.k * factor }).k
      const next = { k, tx: x - ((x - c.tx) / c.k) * k, ty: y - ((y - c.ty) / c.k) * k }
      if (animate) animateTo(next, 250)
      else set(next)
    },
    [animateTo, clamp, set],
  )

  const fitFloor = useCallback(
    (animate: boolean) => {
      const fit = floorFit()
      if (!fit) return
      if (animate) animateTo(fit)
      else {
        stop()
        set(fit, true)
      }
    },
    [animateTo, floorFit, set, stop],
  )

  /** Pan (no zoom change) just enough that a point is inside the unobstructed part of the map. */
  const ensureVisible = useCallback(
    (p: Pt) => {
      const { k, tx, ty } = cam.current
      const { w, h } = size.current
      const ins = insets()
      const sx = p[0] * k + tx
      const sy = p[1] * k + ty
      const m = 40
      const dx = sx < ins.left + m ? ins.left + m - sx : sx > w - ins.right - m ? w - ins.right - m - sx : 0
      const dy = sy < ins.top + m + 30 ? ins.top + m + 30 - sy : sy > h - ins.bottom - m ? h - ins.bottom - m - sy : 0
      if (dx || dy) animateTo({ k, tx: tx + dx, ty: ty + dy })
    },
    [animateTo, insets],
  )

  // ---------- size ----------

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width: w, height: h } = entry.contentRect
      const prev = size.current
      size.current = { w, h }
      if (!w || !h) return
      if (!prev.w || !prev.h) setSized(true)
      else {
        updateLimits()
        cam.current = { ...cam.current, tx: cam.current.tx + (w - prev.w) / 2, ty: cam.current.ty + (h - prev.h) / 2 }
        apply()
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [apply, updateLimits])

  // ---------- fit on load / floor change, animate on focus ----------

  const tapFocus = useRef<string | null>(null)
  const prev = useRef<{ sceneId: string | null; floorId: string | null; nonce: number; sized: boolean }>({
    sceneId: null,
    floorId: null,
    nonce: focus.nonce,
    sized: false,
  })

  useLayoutEffect(() => {
    if (!geo || !sized || !index || !scene) return
    const p = prev.current
    const newScene = p.sceneId !== scene.id || !p.sized
    const floorChanged = p.floorId !== geo.floorId
    const nonceChanged = p.nonce !== focus.nonce
    prev.current = { sceneId: scene.id, floorId: geo.floorId, nonce: focus.nonce, sized }
    updateLimits()

    if (newScene && !nonceChanged) return fitFloor(false)
    if (nonceChanged && focus.ids.length) {
      // A tap on the map selected this: keep the zoom, only make sure the pin is not under the sheet.
      if (tapFocus.current && focus.ids.length === 1 && focus.ids[0] === tapFocus.current) {
        tapFocus.current = null
        const s = geo.byId.get(focus.ids[0])
        const a = geo.assetById.get(focus.ids[0])
        const pt = s?.anchor ?? a?.p
        if (pt) ensureVisible(pt)
        return
      }
      const b = focusBBox(focus.ids, geo, index, route)
      if (b) {
        const ins = insets()
        const pad = 28
        const padded = { top: ins.top + pad, bottom: ins.bottom + pad, left: ins.left + pad, right: ins.right + pad }
        const { w, h } = size.current
        const maxK = Math.min(
          MAX_K,
          Math.max(w - padded.left - padded.right, 40) / FOCUS_MIN_SPAN_M,
          Math.max(h - padded.top - padded.bottom, 40) / FOCUS_MIN_SPAN_M,
        )
        if (newScene || floorChanged) set(fitCamera(b, w, h, padded, maxK), true)
        else animateTo(fitCamera(b, w, h, padded, maxK))
        return
      }
    }
    if (newScene) fitFloor(false)
    else if (floorChanged) fitFloor(false)
    // `route` is read for its points only; focus is driven by the nonce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo, sized, focus.nonce, scene?.id])

  // ---------- gestures ----------

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      stop()
      const r = svg.getBoundingClientRect()
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      const factor = Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015))
      zoomBy(factor, e.clientX - r.left, e.clientY - r.top)
      clearTimeout(wheelSettle)
      wheelSettle = window.setTimeout(() => apply(true), 150)
    }
    let wheelSettle = 0
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      svg.removeEventListener('wheel', onWheel)
      clearTimeout(wheelSettle)
    }
  }, [apply, stop, zoomBy])

  const g = useRef({
    pointers: new Map<number, { x: number; y: number }>(),
    down: null as null | { x: number; y: number; t: number; target: EventTarget | null },
    moved: false,
    multi: false,
    last: { x: 0, y: 0, t: 0 },
    vel: { x: 0, y: 0 },
    pinch: null as null | { d: number; mx: number; my: number; cam: Camera },
  })

  const local = (e: { clientX: number; clientY: number }) => {
    const r = svgRef.current!.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }

  const startPinch = () => {
    const st = g.current
    const [a, b] = [...st.pointers.values()]
    st.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, cam: cam.current }
  }

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    stop()
    const st = g.current
    const p = local(e)
    svgRef.current?.setPointerCapture(e.pointerId)
    st.pointers.set(e.pointerId, p)
    if (st.pointers.size === 1) {
      st.down = { ...p, t: performance.now(), target: e.target }
      st.moved = false
      st.multi = false
      st.last = { ...p, t: performance.now() }
      st.vel = { x: 0, y: 0 }
    } else if (st.pointers.size === 2) {
      st.multi = true
      st.moved = true
      startPinch()
    }
  }

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const st = g.current
    if (!st.pointers.has(e.pointerId)) return
    const p = local(e)
    st.pointers.set(e.pointerId, p)
    if (st.pointers.size >= 2 && st.pinch) {
      const [a, b] = [...st.pointers.values()]
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1
      const mx = (a.x + b.x) / 2
      const my = (a.y + b.y) / 2
      const c0 = st.pinch.cam
      const k = clamp({ ...c0, k: (c0.k * d) / st.pinch.d }).k
      const wx = (st.pinch.mx - c0.tx) / c0.k
      const wy = (st.pinch.my - c0.ty) / c0.k
      set({ k, tx: mx - wx * k, ty: my - wy * k })
      return
    }
    if (!st.down) return
    if (!st.moved && Math.hypot(p.x - st.down.x, p.y - st.down.y) < TAP_SLOP) return
    st.moved = true
    const now = performance.now()
    const dx = p.x - st.last.x
    const dy = p.y - st.last.y
    const dt = Math.max(now - st.last.t, 1)
    st.vel = { x: 0.8 * (dx / dt) + 0.2 * st.vel.x, y: 0.8 * (dy / dt) + 0.2 * st.vel.y }
    st.last = { ...p, t: now }
    set({ ...cam.current, tx: cam.current.tx + dx, ty: cam.current.ty + dy })
  }

  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const st = g.current
    if (!st.pointers.has(e.pointerId)) return
    st.pointers.delete(e.pointerId)
    if (st.pointers.size === 1) {
      // Pinch -> pan with the remaining finger, without a jump.
      const [rest] = [...st.pointers.values()]
      st.pinch = null
      st.last = { ...rest, t: performance.now() }
      st.vel = { x: 0, y: 0 }
      return
    }
    if (st.pointers.size >= 2) return startPinch()
    const down = st.down
    st.down = null
    if (e.type === 'pointercancel') return apply(true)
    if (!st.moved && !st.multi && down && performance.now() - down.t < 600) return tap(down)
    // Inertia after a fling.
    const v = { ...st.vel }
    if (performance.now() - st.last.t > 80 || Math.hypot(v.x, v.y) < 0.25) return apply(true)
    let t0 = performance.now()
    const tick = (now: number) => {
      const dt = now - t0
      t0 = now
      const decay = Math.pow(0.94, dt / 16)
      v.x *= decay
      v.y *= decay
      set({ ...cam.current, tx: cam.current.tx + v.x * dt, ty: cam.current.ty + v.y * dt })
      if (Math.hypot(v.x, v.y) > 0.02) raf.current = requestAnimationFrame(tick)
      else apply(true)
    }
    raf.current = requestAnimationFrame(tick)
  }

  const tap = (down: { x: number; y: number; target: EventTarget | null }) => {
    const el = down.target instanceof Element ? down.target : null
    const chip = el?.closest('[data-floor]')
    if (chip) return setFloor(chip.getAttribute('data-floor')!)
    const asset = el?.closest('[data-asset]')
    if (asset) {
      const id = asset.getAttribute('data-asset')!
      tapFocus.current = id
      select({ kind: 'asset', id })
      return useApp.getState().setSheet('half')
    }
    const g0 = geoRef.current
    if (!g0) return select(null)
    const { k, tx, ty } = cam.current
    const s = spaceAt(g0, [(down.x - tx) / k, (down.y - ty) / k])
    if (!s) return select(null)
    tapFocus.current = s.id
    select({ kind: 'space', id: s.id })
    useApp.getState().setSheet('half')
  }

  // ---------- derived overlays ----------

  const hl = useMemo(() => {
    const spaces: string[] = []
    const dots: { id: string; p: Pt; tappable: boolean }[] = []
    if (!geo || !index) return { spaces, dots }
    for (const id of highlight.ids) {
      if (geo.byId.has(id)) spaces.push(id)
      const a = geo.assetById.get(id)
      if (a) dots.push({ id, p: a.p, tappable: true })
      const o = index.openingById.get(id)
      if (o && o.floor_id === geo.floorId) dots.push({ id, p: toSvg(o.position), tappable: false })
      const c = index.connectorById.get(id)
      if (c) for (const [f, sid] of c.stops) if (f === geo.floorId) spaces.push(sid)
    }
    return { spaces, dots }
  }, [geo, index, highlight.ids])

  const sel = useMemo(() => {
    if (!geo || !index || !selection) return null
    if (selection.kind === 'space') {
      const s = geo.byId.get(selection.id)
      return s ? { space: s, p: s.anchor, lines: s.lines.length ? s.lines : [] } : null
    }
    const a = geo.assetById.get(selection.id)
    const name = index.assetById.get(selection.id)?.name
    return a ? { space: null, p: a.p, lines: name ? [name] : [] } : null
  }, [geo, index, selection])

  const assetLevel: 0 | 1 | 2 = zoomK >= ASSET_K[1] ? 2 : zoomK >= ASSET_K[0] ? 1 : 0

  // Re-apply the camera after every render that may have re-created the world group.
  useLayoutEffect(() => {
    if (geo) apply()
  }, [geo, apply])

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden bg-map-bg select-none">
      <svg
        ref={svgRef}
        className="absolute inset-0 h-full w-full cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onContextMenu={(e) => e.preventDefault()}
      >
        {geo && index && (
          <g ref={worldRef}>
            <FloorBase geo={geo} />
            <Footprints geo={geo} visible={assetLevel > 0} />
            <HighlightFill geo={geo} ids={hl.spaces} />
            {sel?.space && <SelectedOutline space={sel.space} />}
            {route && <RouteLines route={route} floorId={geo.floorId} index={index} />}
            <Labels geo={geo} k={zoomK} skipId={sel?.space?.id ?? null} />
            <AssetDots geo={geo} level={assetLevel} />
            <HighlightDots points={hl.dots} />
            {route && <RouteMarkers route={route} floorId={geo.floorId} index={index} />}
            {sel && <SelectedPin p={sel.p} lines={sel.lines} />}
          </g>
        )}
      </svg>
      {geo && (
        <MapControls
          onFit={() => fitFloor(true)}
          onZoomIn={() => zoomBy(1.6, undefined, undefined, true)}
          onZoomOut={() => zoomBy(1 / 1.6, undefined, undefined, true)}
        />
      )}
    </div>
  )
}
