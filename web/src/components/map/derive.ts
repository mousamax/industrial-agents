// What the map shows, independent of how it is drawn: label fitting and per-floor route drawings. Shared by the
// 2D SVG layers (layers.tsx) and the 3D HTML overlay (Overlay3D.tsx) so both views label the same things.
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import { toSvg, type Pt, type SpaceGeo } from './geometry'

export const CHAR_W = 6
export const LINE_H = 13
export const POI_D = 22

function fits(s: SpaceGeo, lines: string[], k: number, withPoi: boolean): boolean {
  if (!lines.length) return false
  const tw = Math.max(...lines.map((l) => l.length)) * CHAR_W + 8
  const th = lines.length * LINE_H + (withPoi ? POI_D : 0) + 4
  return s.fitW * k >= tw && s.fitH * k >= th
}

/** Full name if it fits, else the short ref, else nothing. */
export function labelFor(s: SpaceGeo, k: number, withPoi: boolean): string[] | null {
  if (fits(s, s.lines, k, withPoi)) return s.lines
  if (fits(s, s.short, k, withPoi)) return s.short
  return null
}

/** POI badge shown when the room is big enough on screen for it. */
export const showsPoi = (s: SpaceGeo, k: number) => !!s.poi && Math.min(s.fitW, s.fitH) * k >= POI_D

export interface RouteDrawing {
  lines: Pt[][]
  start: Pt | null
  end: Pt | null
  chips: { p: Pt; text: string; floorId: string }[]
}

function floorWord(index: SceneIndex, floorId: string): string {
  const f = index.floorById.get(floorId)
  return f ? f.name : floorId
}

/** The part of a route on one floor, in SVG coordinates, with its floor-change chips. */
export function routeOnFloor(route: ShowRoute, floorId: string, index: SceneIndex): RouteDrawing {
  const segs = route.segments
  const lines: Pt[][] = []
  const chips: RouteDrawing['chips'] = []
  segs.forEach((seg, i) => {
    if (seg.floor_id === floorId && seg.points.length) lines.push(seg.points.map(toSvg))
    const next = segs[i + 1]
    if (!next || next.floor_id === seg.floor_id) return
    const from = index.floorById.get(seg.floor_id)
    const to = index.floorById.get(next.floor_id)
    const up = (to?.level ?? 0) > (from?.level ?? 0)
    if (seg.floor_id === floorId && seg.points.length)
      chips.push({
        p: toSvg(seg.points[seg.points.length - 1]),
        text: `Continue on ${floorWord(index, next.floor_id)} ${up ? '↑' : '↓'}`,
        floorId: next.floor_id,
      })
    if (next.floor_id === floorId && next.points.length)
      chips.push({
        p: toSvg(next.points[0]),
        text: `Back to ${floorWord(index, seg.floor_id)} ${up ? '↓' : '↑'}`,
        floorId: seg.floor_id,
      })
  })
  const first = segs[0]
  const last = segs[segs.length - 1]
  return {
    lines,
    chips,
    start: first?.floor_id === floorId && first.points.length ? toSvg(first.points[0]) : null,
    end: last?.floor_id === floorId && last.points.length ? toSvg(last.points[last.points.length - 1]) : null,
  }
}
