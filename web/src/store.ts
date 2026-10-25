// App state shared by map, floor picker, browse and chat. One store, small actions; components subscribe to slices.
import { create } from 'zustand'
import { fetchBuildings, fetchScene } from './api/client'
import type { BuildingInfo, Scene, ShowRoute, ViewCommand, XY } from './api/types'
import type { MemoryChip } from './lib/buildingGraph'
import { buildIndex, floorOf, type SceneIndex } from './lib/sceneIndex'

export type Mode = 'browse' | 'chat'
export type SheetSnap = 'peek' | 'half' | 'full' // phone bottom sheet height
export type Selection = { kind: 'space' | 'asset'; id: string } | null

export interface RouteState {
  route: ShowRoute | null
  /** Set when the agent says no route exists under the rules (no lift, closed area...). */
  unreachable: string | null
}

export interface GhostDoorState {
  floorId: string
  pos: XY
  vertical: boolean
  /** Door width in metres, drawn to scale along the wall. */
  widthM: number
  applied: boolean
}

export interface WalkMarkerState {
  floorId: string
  pos: XY
}

/** The answer reads best in 3D: tilt the map, and optionally join two rooms through the floors with a label. */
export interface View3DIntent {
  link: { from: string; to: string; label: string | null; labelAt: 'mid' | 'to' } | null
}

export interface MapInsets {
  top: number
  right: number
  bottom: number
  left: number
}

interface AppState {
  buildings: BuildingInfo[]
  buildingId: string | null
  scene: Scene | null
  index: SceneIndex | null
  loading: boolean
  error: string | null

  floorId: string | null
  mode: Mode
  sheet: SheetSnap
  selection: Selection
  highlight: { ids: string[]; label: string | null }
  routeState: RouteState
  stepFree: boolean
  /** Closed spaces, connectors or doors (red dashed fill/stroke on map). */
  closedIds: string[]
  /** Rooms affected by a closure or change (amber fill/stroke on map). */
  affectedIds: string[]
  /** Proposed or newly added doors on shared walls (several when the agent adds a batch). */
  ghostDoors: GhostDoorState[]
  /** Active user position during Walk Mode. */
  walkMarker: WalkMarkerState | null
  /** Custom camera fit insets for the active screen layout (Phone Result sheet, Walk mode banners, Desktop). */
  mapInsets: MapInsets | null
  /** Set while the current answer is shown tilted in 3D (chosen from the tools the agent used). */
  view3d: View3DIntent | null
  /** Remembered user context chips ("At Main lobby 0.18", "Step-free routes", "Forklift · 3 m openings"). */
  memory: MemoryChip[]
  /** Bumped whenever the map should fit these ids into view (selection, highlight, route). */
  focus: { ids: string[]; nonce: number; points?: XY[]; follow?: boolean }
  /** A question another panel wants the chat to send (e.g. "Directions" on a place card). */
  pendingPrompt: string | null
  /** How much of the route is drawn (0..1) while an answer draws it in; null draws it the usual way. */
  routeReveal: number | null

  loadBuildings: () => Promise<void>
  loadBuilding: (id: string) => Promise<void>
  reloadScene: () => Promise<void>
  clearError: () => void
  setFloor: (floorId: string) => void
  setMode: (mode: Mode) => void
  setSheet: (snap: SheetSnap) => void
  select: (sel: Selection) => void
  setHighlight: (ids: string[], label?: string | null) => void
  setRoute: (route: ShowRoute | null) => void
  setUnreachable: (message: string | null) => void
  setStepFree: (on: boolean) => void
  setMapOverlays: (o: {
    closedIds?: string[]
    affectedIds?: string[]
    ghostDoors?: GhostDoorState[]
    walkMarker?: WalkMarkerState | null
  }) => void
  setMapInsets: (insets: MapInsets | null) => void
  setView3d: (v: View3DIntent | null) => void
  remember: (chips: MemoryChip[]) => void
  forgetOne: (idx: number) => void
  forgetAll: () => void
  focusOn: (ids: string[]) => void
  /** Fit these points (building metres) on that floor into view, e.g. one floor's stretch of a route. `follow`:
   *  called every frame with a moving point, the camera eases after it closely instead of animating each time. */
  focusPoints: (floorId: string, points: XY[], follow?: boolean) => void
  clearMap: () => void
  applyView: (cmd: ViewCommand) => void
  ask: (text: string) => void
  consumePrompt: () => string | null
  setRouteReveal: (frac: number | null) => void
}

const DEFAULT_BUILDING = 'proc-factory-dev-003'

const sceneCache = new Map<string, { scene: Scene; index: SceneIndex }>()

export async function getOrLoadBuildingIndex(id: string): Promise<{ scene: Scene; index: SceneIndex }> {
  const hit = sceneCache.get(id)
  if (hit) return hit
  const scene = await fetchScene(id)
  const index = buildIndex(scene)
  const entry = { scene, index }
  sceneCache.set(id, entry)
  return entry
}

export const useApp = create<AppState>((set, get) => ({
  buildings: [],
  buildingId: null,
  scene: null,
  index: null,
  loading: false,
  error: null,

  floorId: null,
  mode: 'chat',
  sheet: 'peek',
  selection: null,
  highlight: { ids: [], label: null },
  routeState: { route: null, unreachable: null },
  stepFree: false,
  closedIds: [],
  affectedIds: [],
  ghostDoors: [],
  walkMarker: null,
  mapInsets: null,
  view3d: null,
  memory: [],
  focus: { ids: [], nonce: 0 },
  pendingPrompt: null,
  routeReveal: null,

  loadBuildings: async () => {
    try {
      set({ buildings: await fetchBuildings() })
    } catch (e) {
      set({ error: String(e) })
    }
  },

  loadBuilding: async (id) => {
    if (get().buildingId === id && get().scene && get().index) return
    set({ loading: true, error: null, buildingId: id })
    try {
      const { scene, index } = await getOrLoadBuildingIndex(id)
      const ground = index.floors.find((f) => f.level === 0) ?? index.floors[0]
      set({
        scene,
        index,
        floorId: ground?.id ?? null,
        selection: null,
        highlight: { ids: [], label: null },
        routeState: { route: null, unreachable: null },
        closedIds: [],
        affectedIds: [],
        ghostDoors: [],
        walkMarker: null,
        view3d: null,
        loading: false,
      })
      const url = new URL(window.location.href)
      url.searchParams.set('b', id)
      window.history.replaceState(null, '', url)
    } catch (e) {
      set({ loading: false, error: String(e) })
    }
  },

  reloadScene: async () => {
    const id = get().buildingId
    if (!id) return
    const scene = await fetchScene(id)
    const index = buildIndex(scene)
    sceneCache.set(id, { scene, index })
    set({ scene, index })
  },

  clearError: () => set({ error: null }),
  setFloor: (floorId) => set({ floorId }),
  setMode: (mode) => set({ mode }),
  setSheet: (sheet) => set({ sheet }),

  select: (selection) => {
    set({ selection })
    if (selection) get().focusOn([selection.id])
  },

  setHighlight: (ids, label = null) => {
    set({ highlight: { ids, label } })
    if (ids.length) get().focusOn(ids)
  },

  setRoute: (route) => {
    // A route replaces whatever was picked before: its own end pin marks the destination.
    set(
      route
        ? { routeState: { route, unreachable: null }, selection: null }
        : { routeState: { route: null, unreachable: null } },
    )
    if (route?.segments.length) set({ floorId: route.segments[0].floor_id })
    if (route) get().focusOn(route.space_ids)
  },

  setUnreachable: (message) => set({ routeState: { route: null, unreachable: message } }),
  setStepFree: (stepFree) => set({ stepFree }),

  setMapOverlays: (o) =>
    set((s) => ({
      closedIds: o.closedIds !== undefined ? o.closedIds : s.closedIds,
      affectedIds: o.affectedIds !== undefined ? o.affectedIds : s.affectedIds,
      ghostDoors: o.ghostDoors !== undefined ? o.ghostDoors : s.ghostDoors,
      walkMarker: o.walkMarker !== undefined ? o.walkMarker : s.walkMarker,
    })),

  setMapInsets: (mapInsets) => set({ mapInsets }),
  setView3d: (view3d) => set({ view3d }),

  remember: (chips) =>
    set((s) => {
      let next = [...s.memory]
      let sf = s.stepFree
      for (const m of chips) {
        const key = m.icon === 'map-pin' ? 'map-pin' : m.text
        next = [...next.filter((x) => (x.icon === 'map-pin' ? 'map-pin' : x.text) !== key), m]
        if (m.stepFree) sf = true
      }
      return { memory: next, stepFree: sf }
    }),

  forgetOne: (idx) =>
    set((s) => {
      const next = s.memory.filter((_, i) => i !== idx)
      const sf = next.some((m) => m.stepFree)
      return { memory: next, stepFree: sf }
    }),

  forgetAll: () => set({ memory: [], stepFree: false }),

  focusOn: (ids) => {
    const { index, floorId } = get()
    // Switch floor if none of the ids is on the current one.
    if (index && ids.length && !ids.some((id) => floorOf(index, id) === floorId)) {
      const target = ids.map((id) => floorOf(index, id)).find(Boolean)
      if (target) set({ floorId: target })
    }
    set((s) => ({ focus: { ids, nonce: s.focus.nonce + 1 } }))
  },

  focusPoints: (floorId, points, follow = false) =>
    set((s) => ({ floorId, focus: { ids: [], points, follow, nonce: s.focus.nonce + 1 } })),

  clearMap: () =>
    set({
      selection: null,
      highlight: { ids: [], label: null },
      routeState: { route: null, unreachable: null },
      closedIds: [],
      affectedIds: [],
      ghostDoors: [],
      walkMarker: null,
      view3d: null,
    }),

  applyView: (cmd) => {
    switch (cmd.kind) {
      case 'highlight':
        return get().setHighlight(cmd.ids, cmd.label ?? null)
      case 'route':
        return get().setRoute(cmd)
      case 'focus_floor':
        return get().setFloor(cmd.floor_id)
      case 'scene_changed':
        void get().reloadScene()
        return
    }
  },

  ask: (text) => set({ mode: 'chat', sheet: 'half', pendingPrompt: text }),
  consumePrompt: () => {
    const text = get().pendingPrompt
    if (text) set({ pendingPrompt: null })
    return text
  },
  setRouteReveal: (routeReveal) => set({ routeReveal }),
}))

export const initialBuildingId = () => new URL(window.location.href).searchParams.get('b') ?? DEFAULT_BUILDING
