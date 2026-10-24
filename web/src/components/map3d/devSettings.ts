// Dev-tunable 3D settings, shared by the engine, the map integration and the dev panel.
// Persisted per browser so a tuned value survives reloads; `reset()` returns to the defaults.
import { create } from 'zustand'
import type { Map3DSettings } from './types'

export const DEFAULT_3D: Map3DSettings = {
  objectsZoomK: 40,
  autoTiltDeg: 45,
  maxPitchDeg: 65,
  fovDeg: 35,
  wallHeightM: 1.5,
  floorGapScale: 1,
  showLowerFloors: true,
  objectDetail: 2,
}

const KEY = 'map3d-settings-v2'

function load(): Map3DSettings {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? { ...DEFAULT_3D, ...(JSON.parse(raw) as Partial<Map3DSettings>) } : DEFAULT_3D
  } catch {
    return DEFAULT_3D
  }
}

function save(s: Map3DSettings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s))
  } catch {
    // private mode / blocked storage: settings just don't persist
  }
}

interface DevSettingsState {
  settings: Map3DSettings
  set: (patch: Partial<Map3DSettings>) => void
  reset: () => void
}

export const useMap3DSettings = create<DevSettingsState>((set, get) => ({
  settings: load(),
  set: (patch) => {
    const settings = { ...get().settings, ...patch }
    save(settings)
    set({ settings })
  },
  reset: () => {
    save(DEFAULT_3D)
    set({ settings: DEFAULT_3D })
  },
}))

/** The dev panel shows with `?dev` in the URL. */
export const devMode = () => new URL(window.location.href).searchParams.has('dev')
