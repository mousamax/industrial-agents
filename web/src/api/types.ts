// Shared types. Scene types come from the API's OpenAPI schema (regenerate with `npm run gen:types`);
// view commands mirror src/app/building/view.py, which the OpenAPI schema does not expose.
import type { components } from './schema'

type S = components['schemas']

export type BuildingInfo = S['BuildingInfo']
export type Scene = S['Scene']
export type SceneFloor = S['SceneFloor']
export type SceneSpace = S['SceneSpace']
export type SceneOpening = S['SceneOpening']
export type SceneConnector = S['SceneConnector']
export type SceneAsset = S['SceneAsset']

export type XY = [number, number]

export interface Highlight {
  kind: 'highlight'
  ids: string[] // space, asset, opening or connector ids
  label?: string | null
}

export interface RouteSegment {
  floor_id: string
  points: XY[] // polyline in building metres
}

export interface ShowRoute {
  kind: 'route'
  segments: RouteSegment[] // one per stretch on a floor, in walking order
  length_m: number
  space_ids: string[]
  steps?: string[]
}

export interface FocusFloor {
  kind: 'focus_floor'
  floor_id: string
}

export interface SceneChanged {
  kind: 'scene_changed'
  reason: string
}

export type ViewCommand = Highlight | ShowRoute | FocusFloor | SceneChanged
