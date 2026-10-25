// Indoor map of the current floor, Google Maps style. One camera for both views: the 2D camera {k, tx, ty}
// plus a pitch. Flat and zoomed out it is the SVG map (the viewBox IS the camera; marker counter-scales are
// written straight to the DOM, so React only re-renders when the zoom crosses a detail threshold). Tilted, or
// zoomed in past `objectsZoomK`, the three.js engine (map3d/, loaded lazily) draws the same camera and an HTML
// overlay carries the pins and labels; at pitch 0 both line up, so the switch is a quick crossfade.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import { useApp, type GhostDoorState, type MapInsets } from '../../store'
import { truncateRoute } from '../../show/show'
import { useIsPhone } from '../layout/useViewport'
import { DevPanel, type DevStats } from '../map3d/DevPanel'
import { devMode, useMap3DSettings } from '../map3d/devSettings'
import type { Map3DEngine, View3D } from '../map3d/types'
import { createEngine } from './engine3d'
import {
  bboxOf,
  fitCamera,
  floorGeo,
  lowerFloors,
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
  AffectedFill,
  AssetDots,
  ClosedFill,
  FloorBase,
  Footprints,
  GhostDoorLayer,
  HighlightDots,
  HighlightFill,
  Labels,
  LowerFloors,
  RouteLines,
  RouteMarkers,
  SelectedOutline,
  SelectedPin,
  SurroundingsLayer,
} from './layers'
import { useSurroundings } from './surroundings'
import { MapControls } from './MapControls'
import { At, StartDot } from './markers'
import { Overlay3D } from './Overlay3D'

const SEARCH_BAR = 110 // floating search bar + chips row over the top of the map
const PICKER_GUTTER = 60 // floor picker on the right edge
const FOCUS_MIN_SPAN_M = 16 // never zoom in so far that less than this many metres are visible
const FOLLOW_MIN_SPAN_M = 9 // a camera following a point (show mode's route draw) may come closer
const FOLLOW_EASE = 0.14 // share of the way to the followed point the camera moves per frame

/** One frame of a camera chasing `to`: centre moves linearly, zoom geometrically, FOLLOW_EASE of the way. */
function chase(from: Camera, to: Camera, w: number, h: number): Camera {
  const k = from.k * Math.pow(to.k / from.k, FOLLOW_EASE)
  const c0 = [(w / 2 - from.tx) / from.k, (h / 2 - from.ty) / from.k]
  const c1 = [(w / 2 - to.tx) / to.k, (h / 2 - to.ty) / to.k]
  const cx = c0[0] + (c1[0] - c0[0]) * FOLLOW_EASE
  const cy = c0[1] + (c1[1] - c0[1]) * FOLLOW_EASE
  return { k, tx: w / 2 - cx * k, ty: h / 2 - cy * k }
}
const MAX_K = 160 // px per metre
const ASSET_K = [12, 28] // zoom (px/m) at which machines/equipment, then furniture/fixtures appear
const TAP_SLOP = 7
const ANIM_MS = 450
const MIN_K_OF_FIT = 0.5 // zoom out to half the whole-building fit...
const MIN_K_OF_FIT_SURROUNDED = 0.15 // ...or further when there is a neighbourhood to see

// ---- 3D ----
const TILT_DEG_PER_PX = 0.3 // two-finger vertical swipe / right-drag: 100 px = 30 degrees
const TWO_DECIDE_PX = 10 // two-finger movement before deciding between pinch/pan and tilt
const TILT_BUTTON_DEG = 50 // 3D button target pitch
const TILT_MS = 450
const PITCH_SNAP_DEG = 2 // a manual tilt released below this goes back to flat
const ZONE_EXIT = 0.8 // leave the "objects" zoom zone below this fraction of objectsZoomK (hysteresis)
const WARM_AT = 0.6 // start loading the engine when the zoom reaches this fraction of objectsZoomK
const FADE_MS = 150
const PAN_CAP = 8 // 3D pan: max metres per screen-metre moved (guards against near-horizon blowups)
const DEV = devMode()

type EngineStatus = 'idle' | 'loading' | 'ready' | 'failed'
type OverlayItem = { el: HTMLElement; x: number; y: number; elev: number | null; cull: boolean; shown: boolean }

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
const fade = (on: boolean): CSSProperties => ({
  opacity: on ? 1 : 0,
  visibility: on ? 'visible' : 'hidden',
  transition: `opacity ${FADE_MS}ms ease, visibility ${FADE_MS}ms`,
})

const elevOf = (index: SceneIndex, floorId: string) => index.floorById.get(floorId)?.elevation_m ?? 0

function focusBBox(
  ids: string[],
  geo: FloorGeo,
  index: SceneIndex,
  route: ShowRoute | null,
  ghostDoor: GhostDoorState | null,
  /** Shown tilted in 3D: rooms and route parts on the other floors are on screen too. */
  allFloors = false,
): BBox | null {
  let b: BBox | null = null
  const pt = (p: Pt, pad: number) => padBBox(bboxOf([p])!, pad)
  const isRouteFocus = Boolean(route && ids === route.space_ids)
  for (const id of ids) {
    const other = allFloors ? index.spaceById.get(id)?.floor_id : undefined
    const s = geo.byId.get(id) ?? (other ? floorGeo(index, other).byId.get(id) : undefined)
    if (s) {
      const isEndpoint = id === ids[0] || id === ids[ids.length - 1]
      const roomType = index.spaceById.get(id)?.room_type
      if (!isRouteFocus || isEndpoint || roomType !== 'corridor') {
        // Tilted, a lower floor shows further down the screen: shift its room down by about that much (in the flat
        // frame the fit works in, before the tilt squeezes it by cos).
        const drop = other && other !== geo.floorId ? elevOf(index, geo.floorId) - elevOf(index, other) : 0
        const dy = Math.max(0, drop) * Math.tan((TILT_BUTTON_DEG * Math.PI) / 180)
        b = unionBBox(b, { ...s.bbox, minY: s.bbox.minY + dy, maxY: s.bbox.maxY + dy })
      }
    }
    const a = geo.assetById.get(id)
    if (a) b = unionBBox(b, pt(a.p, 2))
    const o = index.openingById.get(id)
    if (o && o.floor_id === geo.floorId) b = unionBBox(b, pt(toSvg(o.position), 2))
    const c = index.connectorById.get(id)
    if (c) for (const [f, sid] of c.stops) if (f === geo.floorId) b = unionBBox(b, geo.byId.get(sid)?.bbox ?? null)
  }
  if (ghostDoor && ghostDoor.floorId === geo.floorId) {
    b = unionBBox(b, pt(toSvg(ghostDoor.pos), 3))
  }
  if (isRouteFocus && route) {
    for (const seg of route.segments) {
      if (seg.floor_id === geo.floorId || allFloors) {
        const segBox = bboxOf(seg.points.map(toSvg))
        if (segBox) b = unionBBox(b, padBBox(segBox, 3))
      }
    }
  }
  return b
}

export function MapView({ hideControls }: { hideControls?: boolean } = {}) {
  const scene = useApp((s) => s.scene)
  const index = useApp((s) => s.index)
  const floorId = useApp((s) => s.floorId)
  const selection = useApp((s) => s.selection)
  const highlight = useApp((s) => s.highlight)
  const route = useApp((s) => s.routeState.route)
  // Show mode draws the route in by hand; its pins stay on the whole route, only the line grows.
  const routeReveal = useApp((s) => s.routeReveal)
  const drawnRoute = useMemo(
    () => (route && routeReveal !== null ? truncateRoute(route, routeReveal) : route),
    [route, routeReveal],
  )
  const closedIds = useApp((s) => s.closedIds)
  const affectedIds = useApp((s) => s.affectedIds)
  const ghostDoor = useApp((s) => s.ghostDoor)
  const walkMarker = useApp((s) => s.walkMarker)
  const mapInsets = useApp((s) => s.mapInsets)
  const surroundings = useSurroundings(scene?.id)
  const surroundingsRef = useRef(surroundings)
  surroundingsRef.current = surroundings
  const view3d = useApp((s) => s.view3d)
  const focus = useApp((s) => s.focus)
  const select = useApp((s) => s.select)
  const setFloor = useApp((s) => s.setFloor)
  const settings = useMap3DSettings((s) => s.settings)
  const phone = useIsPhone()

  const geo = useMemo(
    () => (index && floorId && index.floorById.has(floorId) ? floorGeo(index, floorId) : null),
    [index, floorId],
  )
  const multiFloor = (index?.floors.length ?? 0) > 1
  const floor = index && floorId ? index.floorById.get(floorId) : undefined
  // Every floor's geometry is cached per (index, floor) by floorGeo; these are cheap lookups after the first load.
  const building = useMemo(
    () => (index ? index.floors.reduce<BBox | null>((b, f) => unionBBox(b, floorGeo(index, f.id).bbox), null) : null),
    [index],
  )
  const lower = useMemo(() => (index && floorId ? lowerFloors(index, floorId) : []), [index, floorId])

  const containerRef = useRef<HTMLDivElement>(null)
  const surfaceRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const worldRef = useRef<SVGGElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const cam = useRef<Camera>({ k: 1, tx: 0, ty: 0 })
  const size = useRef({ w: 0, h: 0 })
  const geoRef = useRef(geo)
  geoRef.current = geo
  const multiRef = useRef(multiFloor)
  multiRef.current = multiFloor
  const mapInsetsRef = useRef(mapInsets)
  mapInsetsRef.current = mapInsets
  const view3dRef = useRef(view3d)
  view3dRef.current = view3d
  const buildingBBox = useRef(building)
  buildingBBox.current = building
  const settingsRef = useRef(settings)
  settingsRef.current = settings
  const floorElev = useRef(0)
  floorElev.current = floor?.elevation_m ?? 0
  const floorName = useRef('')
  floorName.current = floor?.name ?? '-'
  const [sized, setSized] = useState(false)
  const [zoomK, setZoomK] = useState(1)
  const zoomKRef = useRef(1)
  const raf = useRef(0)

  // 3D state. All per-frame values live in refs; React state only for what changes the DOM structure.
  const engine = useRef<Map3DEngine | null>(null)
  const [status, setStatus] = useState<EngineStatus>('idle')
  const statusRef = useRef<EngineStatus>('idle')
  const [is3D, setIs3D] = useState(false)
  const is3DRef = useRef(false)
  const [tilted, setTilted] = useState(false)
  const tiltedRef = useRef(false)
  const pitch = useRef(0)
  const pitchAnim = useRef<{ raf: number; target: number } | null>(null)
  /** The current tilt was applied by zooming in (not by the user), so zooming back out undoes it. */
  const autoTilted = useRef(false)
  const inZone = useRef(false)
  /** A tilt asked for while the engine was still loading. */
  const pendingTilt = useRef<{ deg: number; auto: boolean } | null>(null)
  /** Keep updating the SVG until the 3D -> 2D (or 2D -> 3D) crossfade is over. */
  const svgLiveUntil = useRef(0)
  const overlayItems = useRef<OverlayItem[]>([])
  const animatePitchRef = useRef<(deg: number, auto: boolean, ms?: number) => void>(() => {})

  // ---------- camera core ----------

  /**
   * Fits are worked out flat, but an answer shown in 3D is then tilted, which squeezes the view vertically towards
   * the middle of the screen (by about cos(pitch)). Stretch the free band away from the middle by the same factor so
   * the content lands between the insets once tilted.
   */
  const forTilt = (p: MapInsets): MapInsets => {
    if (!view3dRef.current) return p
    const { h } = size.current
    const c = h / 2
    const cos = Math.cos((TILT_BUTTON_DEG * Math.PI) / 180)
    // Perspective also enlarges the near (bottom) side: keep a little more room there.
    const bottom = p.bottom + h * 0.05
    return { ...p, top: c + (p.top - c) / cos, bottom: c + (bottom - c) / cos }
  }

  const insets = useCallback((): Insets => {
    if (mapInsetsRef.current) return mapInsetsRef.current
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

  /** Zoom limits relative to the whole-building fit (all floors), so switching floors never forces a zoom change.
   * Refreshed on size / building / settings changes, not per frame. */
  const limits = useRef({ minK: 0.01, maxK: MAX_K })
  const updateLimits = useCallback(() => {
    const b = buildingBBox.current
    if (!b || !size.current.w) return
    const fit = fitCamera(b, size.current.w, size.current.h, insets(), MAX_K)
    const minOfFit = surroundingsRef.current ? MIN_K_OF_FIT_SURROUNDED : MIN_K_OF_FIT
    limits.current = { minK: fit.k * minOfFit, maxK: Math.max(MAX_K, fit.k * 2, settingsRef.current.objectsZoomK * 1.5) }
  }, [insets])

  useEffect(() => updateLimits(), [surroundings, updateLimits]) // surroundings arrive after the scene

  /** Pan only (k unchanged): keep at least a sliver of the current floor on screen. */
  const clampPan = useCallback((c: Camera): Camera => {
    const g = geoRef.current
    if (!g?.bbox || !size.current.w) return c
    const { w, h } = size.current
    const { k } = c
    const m = 60
    const b = g.bbox
    const tx = Math.min(Math.max(c.tx, m - b.maxX * k), w - m - b.minX * k)
    const ty = Math.min(Math.max(c.ty, m - b.maxY * k), h - m - b.minY * k)
    return { k, tx, ty }
  }, [])

  const clamp = useCallback(
    (c: Camera): Camera => clampPan({ ...c, k: Math.min(Math.max(c.k, limits.current.minK), limits.current.maxK) }),
    [clampPan],
  )

  /** The 3D view of the 2D camera: same k, centre = the scene point under the middle of the container. */
  const viewOf = useCallback((): View3D => {
    const { k, tx, ty } = cam.current
    const { w, h } = size.current
    return { center: [(w / 2 - tx) / k, -(h / 2 - ty) / k], k, pitchDeg: pitch.current, bearingDeg: 0 }
  }, [])

  /** True while the engine draws a tilted (non flat) view, i.e. screen deltas are not plain tx/ty shifts. */
  const perspective = () => !!engine.current && is3DRef.current && pitch.current > 0.01

  // The camera is the SVG viewBox and markers are counter-scaled with SVG transform attributes, never CSS
  // transforms: Firefox draws a CSS-transformed group as a bitmap and then enlarges it, which blurs the map.
  const counterScale = useCallback(() => {
    const world = worldRef.current
    if (!world) return
    const inv = String(1 / cam.current.k)
    const scale = `scale(${inv})`
    if (world.dataset.inv === inv && !world.querySelector('[data-cs]:not([transform])')) return
    world.dataset.inv = inv
    for (const el of world.querySelectorAll('[data-cs]')) el.setAttribute('transform', scale)
  }, [])

  /** Move the HTML markers of the 3D overlay to their projected positions (direct DOM writes). */
  const placeOverlay = useCallback(() => {
    const e = engine.current
    if (!e || !is3DRef.current) return
    const { w, h } = size.current
    const z = floorElev.current
    for (const it of overlayItems.current) {
      const p = e.project([it.x, -it.y], it.elev ?? z)
      const ok = p.visible && (!it.cull || (p.y > 40 && p.y < h && p.x > 0 && p.x < w))
      if (ok) it.el.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px)`
      if (ok !== it.shown) {
        it.el.style.visibility = ok ? 'visible' : 'hidden'
        it.shown = ok
      }
    }
  }, [])

  const collectOverlay = useCallback(() => {
    const root = overlayRef.current
    overlayItems.current = root
      ? [...root.querySelectorAll<HTMLElement>('[data-x]')].map((el) => ({
          el,
          x: Number(el.dataset.x),
          y: Number(el.dataset.y),
          elev: el.dataset.elev !== undefined ? Number(el.dataset.elev) : null,
          cull: el.dataset.cull !== undefined,
          shown: el.style.visibility !== 'hidden',
        }))
      : []
  }, [])

  const ensureEngine = useCallback(() => {
    if (statusRef.current !== 'idle') return
    statusRef.current = 'loading'
    setStatus('loading')
  }, [])

  /** Objects zone (with hysteresis), auto-tilt, and which renderer shows the map. Runs on every camera change. */
  const updateMode = useCallback(() => {
    const s = settingsRef.current
    const k = cam.current.k
    const failed = statusRef.current === 'failed'
    if (!inZone.current && k >= s.objectsZoomK) {
      inZone.current = true
      // Zoomed in close: show the room in 3D, tilted, unless the user already chose a tilt.
      if (!failed && s.autoTiltDeg > 0 && pitch.current < 1 && !pitchAnim.current) {
        if (statusRef.current === 'ready') animatePitchRef.current(s.autoTiltDeg, true)
        else pendingTilt.current = { deg: s.autoTiltDeg, auto: true }
      }
    } else if (inZone.current && k < s.objectsZoomK * ZONE_EXIT) {
      inZone.current = false
      if (pendingTilt.current?.auto) pendingTilt.current = null
      if (autoTilted.current) animatePitchRef.current(0, true)
    }
    const target = pitchAnim.current?.target ?? pitch.current
    const want = !failed && (pitch.current > 0.01 || target > 0.01 || inZone.current)
    if (want || (!failed && k >= s.objectsZoomK * WARM_AT)) ensureEngine()
    const next = want && statusRef.current === 'ready'
    if (next !== is3DRef.current) {
      is3DRef.current = next
      svgLiveUntil.current = performance.now() + FADE_MS + 100
      setIs3D(next)
    }
    const tl = !failed && target > 0.5
    if (tl !== tiltedRef.current) {
      tiltedRef.current = tl
      setTilted(tl)
    }
  }, [ensureEngine])

  const apply = useCallback(
    (settle = false) => {
      const { k, tx, ty } = cam.current
      const { w, h } = size.current
      updateMode()
      if (!is3DRef.current || performance.now() < svgLiveUntil.current) {
        const svg = svgRef.current
        if (svg && w && h) svg.setAttribute('viewBox', `${-tx / k} ${-ty / k} ${w / k} ${h / k}`)
        counterScale()
      }
      const e = engine.current
      if (e && is3DRef.current && w && h) {
        e.setView(viewOf())
        e.requestRender()
        placeOverlay()
      }
      if (settle || Math.abs(Math.log(k / zoomKRef.current)) > 0.18) {
        zoomKRef.current = k
        setZoomK(k)
      }
    },
    [counterScale, placeOverlay, updateMode, viewOf],
  )

  const stop = useCallback(() => cancelAnimationFrame(raf.current), [])

  const set = useCallback(
    (c: Camera, settle = false) => {
      stop() // a camera animation still running (an earlier fit) would overwrite this on its next frame
      cam.current = clamp(c)
      apply(settle)
    },
    [apply, clamp, stop],
  )

  /** Camera after moving the content so the point under screen (fx, fy) ends up under (x, y). */
  const panned = useCallback((c: Camera, fx: number, fy: number, x: number, y: number): Camera | null => {
    const e = engine.current
    if (!e || !is3DRef.current || pitch.current <= 0.01) return { k: c.k, tx: c.tx + x - fx, ty: c.ty + y - fy }
    // Tilted: move along the ground so the floor point under the finger stays under the finger.
    const z = floorElev.current
    const a = e.unproject(fx, fy, z)
    const b = e.unproject(x, y, z)
    if (!a || !b) return null
    let dx = a[0] - b[0]
    let dy = a[1] - b[1]
    const cap = (Math.hypot(x - fx, y - fy) / c.k) * PAN_CAP
    const len = Math.hypot(dx, dy)
    if (len > cap && len > 0) {
      dx *= cap / len
      dy *= cap / len
    }
    // Scene shift (dx, dy) = SVG shift (dx, -dy) of the centre.
    return { k: c.k, tx: c.tx - dx * c.k, ty: c.ty + dy * c.k }
  }, [])

  const panBy = useCallback(
    (fx: number, fy: number, x: number, y: number) => {
      const next = panned(cam.current, fx, fy, x, y)
      if (next) set(next)
    },
    [panned, set],
  )

  /** Zoom by `factor` keeping the ground point under (sx, sy) in place (2D and tilted). */
  const zoomAt = useCallback(
    (factor: number, sx: number, sy: number) => {
      const c = cam.current
      const k = clamp({ ...c, k: c.k * factor }).k
      const e = engine.current
      if (e && perspective()) {
        const z = floorElev.current
        const before = e.unproject(sx, sy, z)
        const { w, h } = size.current
        const cx = (w / 2 - c.tx) / c.k
        const cy = (h / 2 - c.ty) / c.k
        set({ k, tx: w / 2 - cx * k, ty: h / 2 - cy * k }) // zoom about the centre (pushes the view)...
        const after = before && e.unproject(sx, sy, z)
        if (before && after) {
          // ...then shift the centre so the point under the cursor is back under it.
          const c2 = cam.current
          set({ k: c2.k, tx: c2.tx - (before[0] - after[0]) * c2.k, ty: c2.ty + (before[1] - after[1]) * c2.k })
        }
        return
      }
      set({ k, tx: sx - ((sx - c.tx) / c.k) * k, ty: sy - ((sy - c.ty) / c.k) * k })
    },
    [clamp, set],
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

  const stopPitch = useCallback(() => {
    if (pitchAnim.current) cancelAnimationFrame(pitchAnim.current.raf)
    pitchAnim.current = null
  }, [])

  /** Animate the tilt (independent of camera animations, so it can run while the user pinches). */
  const animatePitch = useCallback(
    (deg: number, auto: boolean, ms = TILT_MS) => {
      stopPitch()
      const target = Math.min(Math.max(deg, 0), settingsRef.current.maxPitchDeg)
      const from = pitch.current
      autoTilted.current = auto && target > 0
      if (Math.abs(target - from) < 0.01) {
        pitch.current = target
        return apply(true)
      }
      const t0 = performance.now()
      const anim = { raf: 0, target }
      pitchAnim.current = anim
      const tick = (now: number) => {
        const u = Math.min(1, (now - t0) / ms)
        pitch.current = from + (target - from) * easeInOut(u)
        if (u >= 1) pitchAnim.current = null
        apply(u >= 1)
        if (u < 1) anim.raf = requestAnimationFrame(tick)
      }
      anim.raf = requestAnimationFrame(tick)
      apply() // switch renderer / button state right away
    },
    [apply, stopPitch],
  )
  animatePitchRef.current = animatePitch

  /** Direct manual tilt (gestures). Before the engine is ready it only starts loading it. */
  const tiltTo = useCallback(
    (deg: number) => {
      if (statusRef.current !== 'ready') return ensureEngine()
      stopPitch()
      autoTilted.current = false
      pendingTilt.current = null
      pitch.current = Math.min(Math.max(deg, 0), settingsRef.current.maxPitchDeg)
      apply()
    },
    [apply, ensureEngine, stopPitch],
  )

  /** End of a manual tilt gesture: an almost-flat tilt snaps to flat. */
  const endTilt = useCallback(() => {
    if (pitch.current > 0 && pitch.current < PITCH_SNAP_DEG && !pitchAnim.current) animatePitch(0, false, 150)
    else apply(true)
  }, [animatePitch, apply])

  const toggleTilt = useCallback(() => {
    const target = pitchAnim.current?.target ?? pitch.current
    if (target > 0.5 || pendingTilt.current) {
      pendingTilt.current = null
      return animatePitch(0, false)
    }
    const deg = Math.min(TILT_BUTTON_DEG, settingsRef.current.maxPitchDeg)
    if (statusRef.current === 'ready') return animatePitch(deg, false)
    pendingTilt.current = { deg, auto: false }
    ensureEngine()
  }, [animatePitch, ensureEngine])

  const zoomBy = useCallback(
    (factor: number, sx?: number, sy?: number, animate = false) => {
      const c = cam.current
      const x = sx ?? size.current.w / 2
      const y = sy ?? size.current.h / 2
      if (!animate) return zoomAt(factor, x, y)
      // Buttons zoom about the centre, which is the same in 2D and 3D.
      const k = clamp({ ...c, k: c.k * factor }).k
      animateTo({ k, tx: x - ((x - c.tx) / c.k) * k, ty: y - ((y - c.ty) / c.k) * k }, 250)
    },
    [animateTo, clamp, zoomAt],
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
      const c = cam.current
      const { w, h } = size.current
      const ins = insets()
      const e = engine.current
      const s = e && is3DRef.current ? e.project([p[0], -p[1]], floorElev.current) : { x: p[0] * c.k + c.tx, y: p[1] * c.k + c.ty }
      const m = 40
      const dx = s.x < ins.left + m ? ins.left + m - s.x : s.x > w - ins.right - m ? w - ins.right - m - s.x : 0
      const dy = s.y < ins.top + m + 30 ? ins.top + m + 30 - s.y : s.y > h - ins.bottom - m ? h - ins.bottom - m - s.y : 0
      if (!dx && !dy) return
      const next = panned(c, s.x, s.y, s.x + dx, s.y + dy)
      if (next) animateTo(next)
    },
    [animateTo, insets, panned],
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
      engine.current?.setSize(w, h, window.devicePixelRatio || 1)
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
  const prev = useRef<{
    sceneId: string | null
    floorId: string | null
    nonce: number
    sized: boolean
    insets: typeof mapInsets
  }>({
    sceneId: null,
    floorId: null,
    nonce: focus.nonce,
    sized: false,
    insets: mapInsets,
  })

  useLayoutEffect(() => {
    if (!geo || !sized || !index || !scene) return
    const p = prev.current
    const newScene = p.sceneId !== scene.id || !p.sized
    const floorChanged = p.floorId !== geo.floorId
    const nonceChanged = p.nonce !== focus.nonce
    const insetsChanged = p.insets !== mapInsets
    prev.current = { sceneId: scene.id, floorId: geo.floorId, nonce: focus.nonce, sized, insets: mapInsets }
    updateLimits()

    if ((nonceChanged || newScene) && (focus.ids.length || focus.points?.length || ghostDoor)) {
      // A tap on the map selected this: keep the zoom, only make sure the pin is not under the sheet.
      if (tapFocus.current && focus.ids.length === 1 && focus.ids[0] === tapFocus.current) {
        tapFocus.current = null
        const s = geo.byId.get(focus.ids[0])
        const a = geo.assetById.get(focus.ids[0])
        const pt = s?.anchor ?? a?.p
        if (pt) ensureVisible(pt)
        return
      }
      const pts = focus.points?.length ? bboxOf(focus.points.map(toSvg)) : null
      const follow = !!pts && !!focus.follow
      const b = pts ? padBBox(pts, follow ? 0 : 4) : focusBBox(focus.ids, geo, index, route, ghostDoor, !!view3dRef.current)
      if (b) {
        const ins = insets()
        const pad = 20
        const padded = forTilt({ top: ins.top + pad, bottom: ins.bottom + pad, left: ins.left + pad, right: ins.right + pad })
        const { w, h } = size.current
        const minSpan = follow ? FOLLOW_MIN_SPAN_M : FOCUS_MIN_SPAN_M
        const maxK = Math.min(
          MAX_K,
          Math.max(w - padded.left - padded.right, 40) / minSpan,
          Math.max(h - padded.top - padded.bottom, 40) / minSpan,
        )
        const target = fitCamera(b, w, h, padded, maxK)
        // The pitch is kept: the fit moves the centre and zoom only.
        if (newScene || floorChanged) set(target, true)
        else if (follow) set(chase(cam.current, target, w, h), true) // asked every frame: ease towards it
        else animateTo(target)
        return
      }
    }
    if (newScene) fitFloor(false)
    else if (insetsChanged && !route && !highlight.ids.length) {
      // The layout around the map changed (an answer closed, the home screen came back): show the whole floor.
      fitFloor(true)
    } else if (floorChanged) {
      // If there's an active route on this floor, fit to the route segment on this floor
      if (route) {
        const b = focusBBox(route.space_ids, geo, index, route, ghostDoor, !!view3dRef.current)
        if (b) {
          const ins = insets()
          const pad = 20
          const padded = forTilt({ top: ins.top + pad, bottom: ins.bottom + pad, left: ins.left + pad, right: ins.right + pad })
          const { w, h } = size.current
          const maxK = Math.min(
            MAX_K,
            Math.max(w - padded.left - padded.right, 40) / FOCUS_MIN_SPAN_M,
            Math.max(h - padded.top - padded.bottom, 40) / FOCUS_MIN_SPAN_M,
          )
          set(fitCamera(b, w, h, padded, maxK), true)
          return
        }
      }
      // Plain floor switch (picker, "Continue on" chip): keep centre, zoom and pitch so the floors stack in place;
      // only pan (never zoom) if the new floor would be entirely off screen.
      cam.current = clampPan(cam.current)
      apply(true)
    }
    // `route` is read for its points only; focus is driven by the nonce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo, sized, focus.nonce, scene?.id, mapInsets])

  // ---------- 3D engine: lazy creation, sync with the store, fallback ----------

  const fail = useCallback(() => {
    if (statusRef.current === 'failed') return
    try {
      engine.current?.dispose()
    } catch {
      // context already gone
    }
    engine.current = null
    statusRef.current = 'failed'
    setStatus('failed')
    stopPitch()
    pitch.current = 0
    autoTilted.current = false
    pendingTilt.current = null
    apply(true)
  }, [apply, stopPitch])

  const loading = status === 'loading'
  const ready = status === 'ready'
  const hasCanvas = loading || ready

  useEffect(() => {
    if (!loading) return
    const canvas = canvasRef.current
    if (!canvas) return
    let cancelled = false
    createEngine(canvas)
      .then((e) => {
        if (cancelled) return e.dispose()
        engine.current = e
        const { w, h } = size.current
        e.setSize(w, h, window.devicePixelRatio || 1)
        e.setSettings(settingsRef.current)
        statusRef.current = 'ready'
        setStatus('ready')
      })
      .catch((err) => {
        if (cancelled) return
        console.warn('3D view unavailable, staying in 2D:', err)
        fail()
      })
    return () => {
      cancelled = true
    }
  }, [loading, fail])

  // Context loss (GPU reset, too many contexts on a phone): drop to 2D for the session. Dispose on unmount.
  useEffect(() => {
    if (!hasCanvas) return
    const canvas = canvasRef.current
    const onLost = (ev: Event) => {
      ev.preventDefault()
      fail()
    }
    canvas?.addEventListener('webglcontextlost', onLost)
    return () => {
      canvas?.removeEventListener('webglcontextlost', onLost)
      engine.current?.dispose()
      engine.current = null
    }
  }, [hasCanvas, fail])

  useEffect(() => {
    if (!ready || !index) return
    engine.current?.setScene(index)
    engine.current?.requestRender()
  }, [ready, index])

  useEffect(() => {
    if (ready) engine.current?.setSurroundings(surroundings?.features ?? null)
  }, [ready, surroundings])

  useEffect(() => {
    if (!ready || !floorId) return
    engine.current?.setFloor(floorId)
    apply() // re-targets the view and the overlay at the new floor's elevation
  }, [ready, floorId, apply])

  useEffect(() => {
    if (ready) engine.current?.setSettings(settings)
    if (pitch.current > settings.maxPitchDeg) pitch.current = settings.maxPitchDeg
    updateLimits()
    apply() // objectsZoomK may have moved past the current zoom
  }, [ready, settings, apply, updateLimits])

  useEffect(() => {
    if (!ready) return
    engine.current?.setHighlight(highlight.ids)
    engine.current?.requestRender()
  }, [ready, highlight.ids])

  useEffect(() => {
    if (!ready) return
    engine.current?.setSelection(selection)
    engine.current?.requestRender()
  }, [ready, selection])

  useEffect(() => {
    if (!ready) return
    engine.current?.setRoute(drawnRoute)
    engine.current?.requestRender()
  }, [ready, drawnRoute])

  useEffect(() => {
    if (!ready) return
    const l = view3d?.link
    engine.current?.setLink(l ? { from: l.from, to: l.to } : null)
    engine.current?.requestRender()
  }, [ready, view3d])

  // The answer reads best in 3D (rooms on top of each other, a route across floors): tilt in, and flatten again
  // when the next answer doesn't need it. A tilt the person chose themselves is left alone.
  const answerTilt = useRef(false)
  useEffect(() => {
    if (statusRef.current === 'failed') return
    if (view3d) {
      answerTilt.current = true
      const deg = Math.min(TILT_BUTTON_DEG, settingsRef.current.maxPitchDeg)
      if (statusRef.current === 'ready') animatePitch(deg, false)
      else {
        pendingTilt.current = { deg, auto: false }
        ensureEngine()
      }
    } else if (answerTilt.current) {
      answerTilt.current = false
      pendingTilt.current = null
      animatePitch(0, false)
    }
  }, [view3d, animatePitch, ensureEngine])

  // Engine just became ready: run the tilt that was asked for while it loaded (button, gesture or zoom).
  useEffect(() => {
    if (!ready) return
    const p = pendingTilt.current
    pendingTilt.current = null
    const s = settingsRef.current
    if (p && (!p.auto || inZone.current)) animatePitch(p.deg, p.auto)
    else if (inZone.current && s.autoTiltDeg > 0 && pitch.current < 1) animatePitch(s.autoTiltDeg, true)
    else apply(true)
  }, [ready, animatePitch, apply])

  // ---------- gestures ----------

  useEffect(() => {
    const el = surfaceRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      stop()
      const r = el.getBoundingClientRect()
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      const factor = Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015))
      zoomBy(factor, e.clientX - r.left, e.clientY - r.top)
      clearTimeout(wheelSettle)
      wheelSettle = window.setTimeout(() => apply(true), 150)
    }
    let wheelSettle = 0
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', onWheel)
      clearTimeout(wheelSettle)
    }
  }, [apply, stop, zoomBy])

  type P = { x: number; y: number }
  const g = useRef({
    pointers: new Map<number, P>(),
    down: null as null | { x: number; y: number; t: number; target: EventTarget | null },
    moved: false,
    multi: false,
    last: { x: 0, y: 0, t: 0 },
    vel: { x: 0, y: 0 },
    pinch: null as null | { d: number; mx: number; my: number; cam: Camera },
    /** Two fingers: undecided until they moved TWO_DECIDE_PX, then pinch (zoom + pan) or tilt for the gesture. */
    two: null as null | { mode: 'undecided' | 'pinch' | 'tilt'; a0: P; b0: P; d: number; mx: number; my: number; pitch: number },
    /** Previous two-finger frame, for the incremental (tilted) pinch. */
    prev: { d: 1, mx: 0, my: 0 },
    incremental: false,
    mouseTilt: null as null | { id: number; y: number; pitch: number },
  })

  const local = (e: { clientX: number; clientY: number }) => {
    const r = surfaceRef.current!.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }

  const startPinch = () => {
    const st = g.current
    const [a, b] = [...st.pointers.values()]
    const d = Math.hypot(a.x - b.x, a.y - b.y) || 1
    const mx = (a.x + b.x) / 2
    const my = (a.y + b.y) / 2
    st.pinch = { d, mx, my, cam: cam.current }
    st.prev = { d, mx, my }
    st.incremental = false
    st.two = { mode: 'undecided', a0: { ...a }, b0: { ...b }, d, mx, my, pitch: pitch.current }
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = g.current
    const p = local(e)
    // Desktop tilt: right-drag, or Ctrl/Shift + drag.
    if (e.pointerType === 'mouse' && (e.button === 2 || (e.button === 0 && (e.ctrlKey || e.shiftKey)))) {
      if (statusRef.current === 'failed' || st.pointers.size) return
      stop()
      ensureEngine()
      surfaceRef.current?.setPointerCapture(e.pointerId)
      st.mouseTilt = { id: e.pointerId, y: p.y, pitch: pitch.current }
      return
    }
    if (e.pointerType === 'mouse' && e.button !== 0) return
    stop()
    surfaceRef.current?.setPointerCapture(e.pointerId)
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
      if (statusRef.current !== 'failed') ensureEngine() // two fingers may become a tilt: warm up three.js
      startPinch()
    }
  }

  const onTwoMove = () => {
    const st = g.current
    const two = st.two!
    const [a, b] = [...st.pointers.values()]
    const d = Math.hypot(a.x - b.x, a.y - b.y) || 1
    const mx = (a.x + b.x) / 2
    const my = (a.y + b.y) / 2
    if (two.mode === 'undecided') {
      const dd = d - two.d
      const mdx = mx - two.mx
      const mdy = my - two.my
      if (Math.max(Math.abs(dd), Math.hypot(mdx, mdy)) < TWO_DECIDE_PX) return
      // Tilt = both fingers side by side moving vertically together, without spreading (Google Maps).
      const da = a.y - two.a0.y
      const db = b.y - two.b0.y
      const sideBySide = Math.abs(two.a0.y - two.b0.y) < Math.abs(two.a0.x - two.b0.x) * 1.2
      const together = da * db > 0 && Math.min(Math.abs(da), Math.abs(db)) > 0.5 * Math.max(Math.abs(da), Math.abs(db))
      const vertical = Math.abs(mdy) > 1.5 * Math.abs(mdx)
      const steady = Math.abs(dd) < 0.5 * Math.abs(mdy)
      const canTilt = statusRef.current !== 'failed'
      two.mode = canTilt && sideBySide && together && vertical && steady ? 'tilt' : 'pinch'
      two.pitch = pitch.current
    }
    if (two.mode === 'tilt') {
      // Swipe up = tilt more.
      tiltTo(two.pitch - (my - two.my) * TILT_DEG_PER_PX)
      st.prev = { d, mx, my }
      return
    }
    const flat = !(is3DRef.current && (pitch.current > 0.01 || pitchAnim.current))
    if (flat) {
      if (st.incremental) {
        st.pinch = { ...st.prev, cam: cam.current }
        st.incremental = false
      }
      // Flat: absolute pinch from the gesture start (exact, no drift at the zoom limits).
      const c0 = st.pinch!.cam
      const k = clamp({ ...c0, k: (c0.k * d) / st.pinch!.d }).k
      const wx = (st.pinch!.mx - c0.tx) / c0.k
      const wy = (st.pinch!.my - c0.ty) / c0.k
      set({ k, tx: mx - wx * k, ty: my - wy * k })
    } else {
      // Tilted: incremental zoom about the previous midpoint, then pan the ground along with the fingers.
      st.incremental = true
      zoomAt(d / st.prev.d, st.prev.mx, st.prev.my)
      panBy(st.prev.mx, st.prev.my, mx, my)
    }
    st.prev = { d, mx, my }
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = g.current
    if (st.mouseTilt?.id === e.pointerId) {
      tiltTo(st.mouseTilt.pitch - (local(e).y - st.mouseTilt.y) * TILT_DEG_PER_PX)
      return
    }
    if (!st.pointers.has(e.pointerId)) return
    const p = local(e)
    st.pointers.set(e.pointerId, p)
    if (st.pointers.size >= 2 && st.two) return onTwoMove()
    if (!st.down) return
    if (!st.moved && Math.hypot(p.x - st.down.x, p.y - st.down.y) < TAP_SLOP) return
    st.moved = true
    const now = performance.now()
    const dx = p.x - st.last.x
    const dy = p.y - st.last.y
    const dt = Math.max(now - st.last.t, 1)
    st.vel = { x: 0.8 * (dx / dt) + 0.2 * st.vel.x, y: 0.8 * (dy / dt) + 0.2 * st.vel.y }
    const from = st.last
    st.last = { ...p, t: now }
    panBy(from.x, from.y, p.x, p.y)
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = g.current
    if (st.mouseTilt?.id === e.pointerId) {
      st.mouseTilt = null
      return endTilt()
    }
    if (!st.pointers.has(e.pointerId)) return
    st.pointers.delete(e.pointerId)
    if (st.pointers.size === 1) {
      // Pinch / tilt -> pan with the remaining finger, without a jump.
      const [rest] = [...st.pointers.values()]
      if (st.two?.mode === 'tilt') endTilt()
      st.pinch = null
      st.two = null
      st.last = { ...rest, t: performance.now() }
      st.vel = { x: 0, y: 0 }
      return
    }
    if (st.pointers.size >= 2) return startPinch()
    const down = st.down
    st.down = null
    if (e.type === 'pointercancel') return apply(true)
    if (!st.moved && !st.multi && down && performance.now() - down.t < 600) return tap(down)
    // Inertia after a fling (along the ground when tilted).
    const v = { ...st.vel }
    if (performance.now() - st.last.t > 80 || Math.hypot(v.x, v.y) < 0.25) return apply(true)
    const anchor = { x: st.last.x, y: st.last.y }
    let t0 = performance.now()
    const tick = (now: number) => {
      const dt = now - t0
      t0 = now
      const decay = Math.pow(0.94, dt / 16)
      v.x *= decay
      v.y *= decay
      panBy(anchor.x, anchor.y, anchor.x + v.x * dt, anchor.y + v.y * dt)
      if (Math.hypot(v.x, v.y) > 0.02) raf.current = requestAnimationFrame(tick)
      else apply(true)
    }
    raf.current = requestAnimationFrame(tick)
  }

  const tap = (down: { x: number; y: number; target: EventTarget | null }) => {
    const el = down.target instanceof Element ? down.target : null
    const chip = el?.closest('[data-floor]')
    if (chip) return setFloor(chip.getAttribute('data-floor')!)
    const e3 = engine.current
    if (e3 && is3DRef.current) {
      const hit = e3.pick(down.x, down.y)
      if (!hit) return select(null)
      tapFocus.current = hit.id
      select(hit)
      return useApp.getState().setSheet('half')
    }
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
    if (!geo || !index) return null
    if (!selection) {
      // An answer label ("Biggest room · 60 m²") sits on the first highlighted room of this floor.
      const first = highlight.label ? hl.spaces.map((id) => geo.byId.get(id)).find(Boolean) : undefined
      return first && highlight.label ? { space: first, p: first.anchor, lines: [highlight.label] } : null
    }
    if (selection.kind === 'space') {
      const s = geo.byId.get(selection.id)
      return s ? { space: s, p: s.anchor, lines: s.lines.length ? s.lines : [] } : null
    }
    const a = geo.assetById.get(selection.id)
    const name = index.assetById.get(selection.id)?.name
    return a ? { space: null, p: a.p, lines: name ? [name] : [] } : null
  }, [geo, index, selection, highlight.label, hl.spaces])

  const assetLevel: 0 | 1 | 2 = zoomK >= ASSET_K[1] ? 2 : zoomK >= ASSET_K[0] ? 1 : 0

  // Re-apply the camera after every render that may have re-created the world group.
  useLayoutEffect(() => {
    if (geo) apply()
  }, [geo, apply])
  // Markers mounted by a later render (labels appearing on zoom, a new pin) need their counter-scale too,
  // and the 3D overlay's markers their screen positions.
  useLayoutEffect(() => {
    counterScale()
    collectOverlay()
    placeOverlay()
  })

  const devStats = useCallback(
    (): DevStats => ({
      k: cam.current.k,
      pitchDeg: pitch.current,
      mode: is3DRef.current ? '3D' : '2D',
      floor: floorName.current,
      engine: statusRef.current,
      width: size.current.w,
    }),
    [],
  )

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden bg-map-bg select-none">
      <div
        ref={surfaceRef}
        className="absolute inset-0 cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onContextMenu={(e) => e.preventDefault()}
      >
        <svg ref={svgRef} className="absolute inset-0 h-full w-full" style={fade(!is3D)}>
          {geo && index && (
            <g ref={worldRef}>
              {surroundings && <SurroundingsLayer s={surroundings} />}
              <LowerFloors floors={lower} />
              <FloorBase geo={geo} />
              <Footprints geo={geo} visible={assetLevel > 0} />
              <ClosedFill geo={geo} index={index} closedIds={closedIds} />
              <AffectedFill geo={geo} ids={affectedIds} />
              <HighlightFill geo={geo} ids={hl.spaces} />
              {sel?.space && <SelectedOutline space={sel.space} />}
              {drawnRoute && (
                <RouteLines route={drawnRoute} floorId={geo.floorId} index={index} instant={routeReveal !== null} />
              )}
              <GhostDoorLayer door={ghostDoor} floorId={geo.floorId} />
              <Labels geo={geo} k={zoomK} skipId={sel?.space?.id ?? null} />
              <AssetDots geo={geo} level={assetLevel} />
              <HighlightDots points={hl.dots} />
              {route && (
                <RouteMarkers
                  route={route}
                  floorId={geo.floorId}
                  index={index}
                  hideStart={!!walkMarker && walkMarker.floorId === geo.floorId}
                />
              )}
              {walkMarker && walkMarker.floorId === geo.floorId && (
                <At p={toSvg(walkMarker.pos)}>
                  <StartDot />
                </At>
              )}
              {sel && <SelectedPin p={sel.p} lines={sel.lines} />}
            </g>
          )}
        </svg>
        {/* Created the first time 3D is needed, then kept so re-entering 3D is instant. */}
        {hasCanvas && <canvas ref={canvasRef} className="absolute inset-0 block h-full w-full" style={fade(is3D)} />}
        {is3D && geo && index && (
          <Overlay3D rootRef={overlayRef} geo={geo} index={index} k={zoomK} sel={sel} route={route} dots={hl.dots}
            link={view3d?.link ?? null} quiet={routeReveal !== null && routeReveal < 1} />
        )}
      </div>
      {surroundings && (
        <div className="pointer-events-none absolute bottom-1 left-2 text-[10px] text-ink-soft/80">
          {surroundings.attribution}
        </div>
      )}
      {geo && !hideControls && (
        <MapControls
          onFit={() => fitFloor(true)}
          onZoomIn={() => zoomBy(1.6, undefined, undefined, true)}
          onZoomOut={() => zoomBy(1 / 1.6, undefined, undefined, true)}
          tilt={status === 'failed' ? undefined : { tilted, onToggle: toggleTilt }}
        />
      )}
      {DEV && geo && <DevPanel stats={devStats} phone={phone} />}
    </div>
  )
}
