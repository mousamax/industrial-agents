// Contract between the 3D engine (map3d/Map3D.ts) and the map integration (map/MapView.tsx).
// The 2D SVG map stays the default. The 3D view takes over when the map is tilted (pitch > 0) or zoomed in
// past `objectsZoomK`, using the SAME camera so the switch is seamless.
//
// Coordinates
// - Scene metres: x right, y up (north). The 2D map flips y for SVG (svgY = -y).
// - 2D camera {k, tx, ty}: screenX = x * k + tx, screenY = -y * k + ty (k = pixels per metre).
// - three.js world: (X, Y, Z) = (x, elevation, -y). Y is up. Elevation = floor.elevation_m * floorGapScale.
//
// View3D semantics (all screen sizes in CSS px of the map container)
// - center: scene point (x, y) at the middle of the map container.
// - k: pixels per metre on the ground plane at `center` (same meaning as the 2D camera's k).
// - pitchDeg: 0 = straight down (looks exactly like the 2D map, north up), up to maxPitchDeg.
// - bearingDeg: rotation around the vertical axis, 0 = north up (reserved; integration keeps it at 0 for now).
// - Perspective camera with vertical field of view fovDeg; distance = h / (2 * k * tan(fov / 2)), so at pitch 0
//   one metre at `center` measures k px, matching the 2D map.
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'

export type XY = [number, number]

export interface View3D {
  center: XY
  k: number
  pitchDeg: number
  bearingDeg: number
}

export interface ScreenPoint {
  x: number
  y: number
  /** false when behind the camera or outside the container */
  visible: boolean
}

export type Pick3D = { kind: 'space' | 'asset'; id: string } | null

/** Dev-tunable settings (see devSettings.ts). Engine reads the visual ones, integration the camera ones. */
export interface Map3DSettings {
  /** px per metre at which 3D objects appear and the map auto-tilts. */
  objectsZoomK: number
  /** Tilt applied automatically when zooming past objectsZoomK (0 = never auto-tilt). */
  autoTiltDeg: number
  maxPitchDeg: number
  fovDeg: number
  /** Height of the extruded walls on the selected floor, metres (cutaway height, not the real ceiling). */
  wallHeightM: number
  /** Vertical exaggeration of the distance between floors (1 = real elevations). */
  floorGapScale: number
  /** Draw the floors below the selected one (dimmed). Floors above are always cut away. */
  showLowerFloors: boolean
  /** Asset detail: 0 = none, 1 = simple boxes, 2 = typed low-poly models. */
  objectDetail: 0 | 1 | 2
}

/** One OpenStreetMap feature around the building, in scene metres (see map/surroundings.ts). */
export interface SurroundingFeature {
  kind: string
  points: [number, number][]
  levels?: number
}

/** Two rooms joined by a straight dashed line, drawn through the floors ("as the crow flies", "directly above"). */
export interface Link3D {
  from: string
  to: string
}

export interface Map3DEngine {
  /** Build (lazily, per floor) the meshes for a building. Call again when the building or scene changes. */
  setScene(index: SceneIndex): void
  /** Cutaway: the selected floor in full, lower floors dimmed (if enabled), floors above hidden. */
  setFloor(floorId: string): void
  /** Container size in CSS px and device pixel ratio; call on resize. */
  setSize(w: number, h: number, dpr: number): void
  setView(view: View3D): void
  setSettings(s: Map3DSettings): void
  /** Space/asset/opening/connector ids to emphasise (accent tint), like the 2D highlight. */
  setHighlight(ids: string[]): void
  /** Selected space or asset (accent outline / lift); the pin itself is an HTML overlay drawn by the integration. */
  setSelection(sel: { kind: 'space' | 'asset'; id: string } | null): void
  /** Route as a raised tube following the segments, with vertical links between floors. */
  setRoute(route: ShowRoute | null): void
  /** Light up both rooms on their own floors and join them, seen through the floors in between. */
  setLink(link: Link3D | null): void
  /** Streets, green and neighbouring buildings on the ground around the building; null removes them. */
  setSurroundings(features: SurroundingFeature[] | null): void
  /** What is under a container point (CSS px). Assets win over spaces. */
  pick(x: number, y: number): Pick3D
  /** Scene point + elevation (metres, real elevation is scaled internally by floorGapScale) -> container px. */
  project(p: XY, elevationM: number): ScreenPoint
  /** Container px -> scene point on the horizontal plane at elevationM (for panning and zoom-around-cursor). */
  unproject(x: number, y: number, elevationM: number): XY | null
  /** Draw once on the next animation frame (the engine never renders continuously). */
  requestRender(): void
  dispose(): void
}
