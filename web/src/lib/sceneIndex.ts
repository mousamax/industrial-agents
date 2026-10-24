// Lookup tables over a Scene, built once per load. Every component reads ids through this, never by scanning arrays.
import type { Scene, SceneAsset, SceneConnector, SceneFloor, SceneOpening, SceneSpace } from '../api/types'

export interface SceneIndex {
  floors: SceneFloor[] // ordered by level, lowest first
  floorById: Map<string, SceneFloor>
  spaceById: Map<string, SceneSpace>
  assetById: Map<string, SceneAsset>
  openingById: Map<string, SceneOpening>
  connectorById: Map<string, SceneConnector>
  spacesByFloor: Map<string, SceneSpace[]>
  assetsBySpace: Map<string, SceneAsset[]>
  openingsByFloor: Map<string, SceneOpening[]>
}

function group<T>(items: T[], key: (t: T) => string): Map<string, T[]> {
  const out = new Map<string, T[]>()
  for (const item of items) {
    const k = key(item)
    const list = out.get(k)
    if (list) list.push(item)
    else out.set(k, [item])
  }
  return out
}

export function buildIndex(scene: Scene): SceneIndex {
  return {
    floors: [...scene.floors].sort((a, b) => a.level - b.level),
    floorById: new Map(scene.floors.map((f) => [f.id, f])),
    spaceById: new Map(scene.spaces.map((s) => [s.id, s])),
    assetById: new Map(scene.assets.map((a) => [a.id, a])),
    openingById: new Map(scene.openings.map((o) => [o.id, o])),
    connectorById: new Map(scene.connectors.map((c) => [c.id, c])),
    spacesByFloor: group(scene.spaces, (s) => s.floor_id),
    assetsBySpace: group(scene.assets, (a) => a.space_id),
    openingsByFloor: group(scene.openings, (o) => o.floor_id),
  }
}

/** Floor an id lives on (space, asset, opening), if known. */
export function floorOf(index: SceneIndex, id: string): string | undefined {
  return (
    index.spaceById.get(id)?.floor_id ??
    index.assetById.get(id)?.floor_id ??
    index.openingById.get(id)?.floor_id
  )
}

/** Human label: "Kitchen 0.33" style names already include the ref; fall back to ref or id. */
export function spaceLabel(s: SceneSpace): string {
  return s.name || s.ref || s.id
}

/** "Ground floor", "Floor 2"... short label for the floor picker: level number, G for 0. */
export function floorShort(f: SceneFloor): string {
  return f.level === 0 ? 'G' : String(f.level)
}

/** Every known id mentioned anywhere in a text blob (tool outputs carry ids before `view` commands land). */
export function idsIn(index: SceneIndex, text: string): string[] {
  const found = new Set<string>()
  for (const m of text.matchAll(/[A-Za-z0-9_.:-]+-(?:F-?\d+-S\d+|A\d+|F-?\d+-O\d+|V\d+)/g)) {
    const id = m[0]
    if (index.spaceById.has(id) || index.assetById.has(id) || index.openingById.has(id) || index.connectorById.has(id))
      found.add(id)
  }
  return [...found]
}
