// App state shared by map, floor picker, browse and chat. One store, small actions; components subscribe to slices.
import { create } from 'zustand'
import { fetchBuildings, fetchScene } from './api/client'
import type { BuildingInfo, Scene, ShowRoute, ViewCommand } from './api/types'
import { buildIndex, floorOf, type SceneIndex } from './lib/sceneIndex'

export type Mode = 'browse' | 'chat'
export type SheetSnap = 'peek' | 'half' | 'full' // phone bottom sheet height
export type Selection = { kind: 'space' | 'asset'; id: string } | null

export interface RouteState {
  route: ShowRoute | null
  /** Set when the agent says no route exists under the rules (no lift, closed area...). */
  unreachable: string | null
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
  /** Bumped whenever the map should fit these ids into view (selection, highlight, route). */
  focus: { ids: string[]; nonce: number }
  /** A question another panel wants the chat to send (e.g. "Directions" on a place card). */
  pendingPrompt: string | null

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
  focusOn: (ids: string[]) => void
  clearMap: () => void
  applyView: (cmd: ViewCommand) => void
  ask: (text: string) => void
  consumePrompt: () => string | null
}

const DEFAULT_BUILDING = 'proc-factory-dev-003'

export const useApp = create<AppState>((set, get) => ({
  buildings: [],
  buildingId: null,
  scene: null,
  index: null,
  loading: false,
  error: null,

  floorId: null,
  mode: 'browse',
  sheet: 'peek',
  selection: null,
  highlight: { ids: [], label: null },
  routeState: { route: null, unreachable: null },
  stepFree: false,
  focus: { ids: [], nonce: 0 },
  pendingPrompt: null,

  loadBuildings: async () => {
    try {
      set({ buildings: await fetchBuildings() })
    } catch (e) {
      set({ error: String(e) })
    }
  },

  loadBuilding: async (id) => {
    set({ loading: true, error: null, buildingId: id })
    try {
      const scene = await fetchScene(id)
      const index = buildIndex(scene)
      const ground = index.floors.find((f) => f.level === 0) ?? index.floors[0]
      set({
        scene,
        index,
        floorId: ground?.id ?? null,
        selection: null,
        highlight: { ids: [], label: null },
        routeState: { route: null, unreachable: null },
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
    set({ scene, index: buildIndex(scene) })
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
    set(route ? { routeState: { route, unreachable: null }, selection: null } : { routeState: { route, unreachable: null } })
    if (route?.segments.length) set({ floorId: route.segments[0].floor_id })
    if (route) get().focusOn(route.space_ids)
  },

  setUnreachable: (message) => set({ routeState: { route: null, unreachable: message } }),
  setStepFree: (stepFree) => set({ stepFree }),

  focusOn: (ids) => {
    const { index, floorId } = get()
    // Switch floor if none of the ids is on the current one.
    if (index && ids.length && !ids.some((id) => floorOf(index, id) === floorId)) {
      const target = ids.map((id) => floorOf(index, id)).find(Boolean)
      if (target) set({ floorId: target })
    }
    set((s) => ({ focus: { ids, nonce: s.focus.nonce + 1 } }))
  },

  clearMap: () =>
    set({ selection: null, highlight: { ids: [], label: null }, routeState: { route: null, unreachable: null } }),

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
}))

export const initialBuildingId = () => new URL(window.location.href).searchParams.get('b') ?? DEFAULT_BUILDING
