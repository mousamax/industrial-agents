// OpenStreetMap around a building, drawn under its floors. Only buildings we have a file for (MUCL, made by
// web/scripts/osm_surroundings.py, already in the building's own metres); loaded lazily, shipped with the app
// so the demo needs no network.
import { useEffect, useState } from 'react'
import { toSvg, type Pt } from './geometry'

export type SurroundingKind = 'green' | 'parking' | 'water' | 'service' | 'path' | 'minor' | 'major' | 'tram' | 'building'

interface SurroundingsFile {
  building_id: string
  attribution: string
  radius_m: number
  features: { kind: SurroundingKind; points: [number, number][]; name?: string; levels?: number }[]
}

export interface Surroundings {
  attribution: string
  radiusM: number
  /** One SVG path per kind (closed rings for areas, open lines for roads), in SVG coordinates. */
  d: Partial<Record<SurroundingKind, string>>
  /** Raw features in scene metres, for the 3D view. */
  features: SurroundingsFile['features']
}

const SOURCES: Record<string, () => Promise<{ default: unknown }>> = {
  'munich-urban-colab': () => import('../../data/surroundings/munich-urban-colab.json'),
  // The wheelchair demo copy (data/MUCL_wheelchair_demo) stands on the same spot
  'munich-urban-colab-wheelchair-demo': () => import('../../data/surroundings/munich-urban-colab.json'),
}

const AREAS = new Set<SurroundingKind>(['green', 'parking', 'building']) // water is drawn as a line
const r1 = (n: number) => Math.round(n * 10) / 10

function pathOf(points: [number, number][], closed: boolean): string {
  const pts = points.map((p) => toSvg(p) as Pt)
  return 'M' + pts.map(([x, y]) => `${r1(x)},${r1(y)}`).join('L') + (closed ? 'Z' : '')
}

function build(file: SurroundingsFile): Surroundings {
  const d: Surroundings['d'] = {}
  for (const f of file.features) d[f.kind] = (d[f.kind] ?? '') + pathOf(f.points, AREAS.has(f.kind))
  return { attribution: file.attribution, radiusM: file.radius_m, d, features: file.features }
}

const cache = new Map<string, Surroundings>()

/** The building's surroundings, or null (none for this building, or still loading). */
export function useSurroundings(buildingId: string | undefined): Surroundings | null {
  const [, setLoaded] = useState(0)
  const source = buildingId ? SOURCES[buildingId] : undefined
  useEffect(() => {
    if (!buildingId || !source || cache.has(buildingId)) return
    let live = true
    source()
      .then((m) => {
        cache.set(buildingId, build(m.default as SurroundingsFile))
        if (live) setLoaded((n) => n + 1)
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [buildingId, source])
  return (buildingId && cache.get(buildingId)) || null
}
