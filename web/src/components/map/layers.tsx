// SVG layers of the map. World-unit layers (floor, footprints, highlight, route lines) use
// non-scaling strokes; screen-size things (labels, POIs, dots, pins, chips) go through <At>.
import { memo, useEffect, useMemo, useState } from 'react'
import type { SceneIndex } from '../../lib/sceneIndex'
import type { ShowRoute } from '../../api/types'
import { At, FloorChip, Pin, PoiBadge, StartDot } from './markers'
import { TONES, TONE_FILL, toSvg, type FloorGeo, type Pt, type SpaceGeo } from './geometry'

const NS = 'non-scaling-stroke' as const

// ---------- static floor ----------

export const FloorBase = memo(function FloorBase({ geo }: { geo: FloorGeo }) {
  return (
    <g pointerEvents="none">
      {/* Thick casing under everything: only its outer half survives, giving the building a firmer outline. */}
      <path d={geo.allD} fill="#b9bfc7" stroke="#b9bfc7" strokeWidth={3.5} vectorEffect={NS} strokeLinejoin="round" />
      {TONES.map((t) => (geo.toneD[t] ? <path key={t} d={geo.toneD[t]} style={{ fill: TONE_FILL[t] }} /> : null))}
      <path d={geo.allD} fill="none" style={{ stroke: 'var(--color-map-wall)' }} strokeWidth={1} vectorEffect={NS}
        strokeLinejoin="round" />
      {TONES.map((t) =>
        geo.doorD[t] ? (
          <path key={t} d={geo.doorD[t]} fill="none" style={{ stroke: TONE_FILL[t] }} strokeWidth={3.5}
            vectorEffect={NS} strokeLinecap="butt" />
        ) : null,
      )}
    </g>
  )
})

export const Footprints = memo(function Footprints({ geo, visible }: { geo: FloorGeo; visible: boolean }) {
  if (!geo.footprintD) return null
  return (
    <path d={geo.footprintD} fill="#e6e8eb" stroke="#d2d6db" strokeWidth={0.75} vectorEffect={NS} pointerEvents="none"
      opacity={visible ? 1 : 0} style={{ transition: 'opacity 200ms' }} />
  )
})

// ---------- labels and POIs ----------

const CHAR_W = 6
const LINE_H = 13
const POI_D = 22

function fits(s: SpaceGeo, lines: string[], k: number, withPoi: boolean): boolean {
  if (!lines.length) return false
  const tw = Math.max(...lines.map((l) => l.length)) * CHAR_W + 8
  const th = lines.length * LINE_H + (withPoi ? POI_D : 0) + 4
  return s.fitW * k >= tw && s.fitH * k >= th
}

/** Full name if it fits, else the short ref, else nothing. */
function labelFor(s: SpaceGeo, k: number, withPoi: boolean): string[] | null {
  if (fits(s, s.lines, k, withPoi)) return s.lines
  if (fits(s, s.short, k, withPoi)) return s.short
  return null
}

function LabelText({ lines, y0, weight = 500, fill = 'var(--color-map-label)', size = 11 }: {
  lines: string[]
  y0: number
  weight?: number
  fill?: string
  size?: number
}) {
  return (
    <text textAnchor="middle" fontSize={size} fontWeight={weight} stroke="#fff" strokeWidth={3} paintOrder="stroke"
      strokeLinejoin="round" style={{ fill }}>
      {lines.map((l, i) => (
        <tspan key={i} x={0} y={y0 + i * (size + 2)}>
          {l}
        </tspan>
      ))}
    </text>
  )
}

export const Labels = memo(function Labels({ geo, k, skipId }: { geo: FloorGeo; k: number; skipId: string | null }) {
  const items = useMemo(() => {
    const out: { s: SpaceGeo; poi: boolean; label: string[] | null }[] = []
    for (const s of geo.spaces) {
      if (s.id === skipId) continue
      const poi = !!s.poi && Math.min(s.fitW, s.fitH) * k >= POI_D
      const label = labelFor(s, k, poi)
      if (poi || label) out.push({ s, poi, label })
    }
    return out
  }, [geo, k, skipId])
  return (
    <g pointerEvents="none">
      {items.map(({ s, poi, label }) => (
        <At key={s.id} p={s.anchor}>
          {poi && <PoiBadge kind={s.poi!} />}
          {label && (
            <LabelText lines={label} y0={poi ? 23 : 4 - ((label.length - 1) * LINE_H) / 2} />
          )}
        </At>
      ))}
    </g>
  )
})

// ---------- assets ----------

const ASSET_COLOR: Record<string, string> = { machine: '#5f6368', equipment: '#80868b' }

export const AssetDots = memo(function AssetDots({ geo, level }: { geo: FloorGeo; level: 0 | 1 | 2 }) {
  if (!level) return null
  return (
    <g>
      {geo.assets
        .filter((a) => a.detail <= level)
        .map((a) => (
          <g key={a.id} transform={`translate(${a.p[0]} ${a.p[1]})`} data-asset={a.id} style={{ cursor: 'pointer' }}>
            <g style={{ transform: 'scale(var(--inv, 1))' }}>
              <circle r={11} fill="transparent" />
              <circle r={3.5} fill={ASSET_COLOR[a.category] ?? '#a0a5ab'} stroke="#fff" strokeWidth={1.5} />
            </g>
          </g>
        ))}
    </g>
  )
})

// ---------- highlight & selection ----------

export function HighlightFill({ geo, ids }: { geo: FloorGeo; ids: string[] }) {
  const d = useMemo(() => ids.map((id) => geo.byId.get(id)?.d ?? '').join(''), [geo, ids])
  if (!d) return null
  return (
    <path d={d} pointerEvents="none" fillOpacity={0.16} strokeWidth={2} vectorEffect={NS} strokeLinejoin="round"
      style={{ fill: 'var(--color-accent)', stroke: 'var(--color-accent)' }} />
  )
}

export function HighlightDots({ points }: { points: { id: string; p: Pt; tappable: boolean }[] }) {
  return (
    <g>
      {points.map(({ id, p, tappable }) => (
        <g key={id} transform={`translate(${p[0]} ${p[1]})`} data-asset={tappable ? id : undefined}>
          <g style={{ transform: 'scale(var(--inv, 1))' }}>
            <circle r={12} fill="transparent" />
            <circle r={9} fillOpacity={0.2} style={{ fill: 'var(--color-accent)' }} />
            <circle r={5} stroke="#fff" strokeWidth={2} style={{ fill: 'var(--color-accent)' }} />
          </g>
        </g>
      ))}
    </g>
  )
}

export function SelectedOutline({ space }: { space: SpaceGeo }) {
  return (
    <path d={space.d} pointerEvents="none" fillOpacity={0.07} strokeWidth={2.5} vectorEffect={NS}
      strokeLinejoin="round" style={{ fill: 'var(--color-accent)', stroke: 'var(--color-accent)' }} />
  )
}

export function SelectedPin({ p, lines }: { p: Pt; lines: string[] }) {
  return (
    <At p={p}>
      <Pin />
      {lines.length > 0 && <LabelText lines={lines} y0={16} weight={600} size={12} fill="var(--color-ink)" />}
    </At>
  )
}

// ---------- route ----------

interface RouteDrawing {
  lines: Pt[][]
  start: Pt | null
  end: Pt | null
  chips: { p: Pt; text: string; floorId: string }[]
}

function floorWord(index: SceneIndex, floorId: string): string {
  const f = index.floorById.get(floorId)
  return f ? f.name : floorId
}

function routeOnFloor(route: ShowRoute, floorId: string, index: SceneIndex): RouteDrawing {
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

function truncate(lines: Pt[][], frac: number): Pt[][] {
  if (frac >= 1) return lines
  const lens = lines.map((l) => l.slice(1).reduce((acc, p, i) => acc + Math.hypot(p[0] - l[i][0], p[1] - l[i][1]), 0))
  let left = lens.reduce((a, b) => a + b, 0) * frac
  const out: Pt[][] = []
  for (let li = 0; li < lines.length && left > 0; li++) {
    const l = lines[li]
    const part: Pt[] = [l[0]]
    for (let i = 1; i < l.length && left > 0; i++) {
      const a = l[i - 1]
      const b = l[i]
      const d = Math.hypot(b[0] - a[0], b[1] - a[1])
      if (d <= left) {
        part.push(b)
        left -= d
      } else {
        const t = left / d
        part.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
        left = 0
      }
    }
    out.push(part)
  }
  return out
}

const DRAW_MS = 1100

/** Route lines (world layer) — drawn in once per new route. */
export function RouteLines({ route, floorId, index }: { route: ShowRoute; floorId: string; index: SceneIndex }) {
  const drawing = useMemo(() => routeOnFloor(route, floorId, index), [route, floorId, index])
  const [prog, setProg] = useState({ route, frac: 0 })
  useEffect(() => {
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const f = Math.min(1, (now - t0) / DRAW_MS)
      setProg({ route, frac: 1 - Math.pow(1 - f, 2) })
      if (f < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [route])
  const frac = prog.route === route ? prog.frac : 0
  const lines = truncate(drawing.lines, frac)
  const pts = (l: Pt[]) => l.map((p) => `${p[0]},${p[1]}`).join(' ')
  return (
    <g pointerEvents="none" fill="none" strokeLinejoin="round" strokeLinecap="round">
      {lines.map((l, i) => (
        <polyline key={`c${i}`} points={pts(l)} strokeWidth={9} vectorEffect={NS}
          style={{ stroke: 'var(--color-route-casing)' }} />
      ))}
      {lines.map((l, i) => (
        <polyline key={`r${i}`} points={pts(l)} strokeWidth={5.5} vectorEffect={NS}
          style={{ stroke: 'var(--color-route)' }} />
      ))}
    </g>
  )
}

/** Route markers (screen-size overlay): start dot, floor-change chips, destination pin. */
export function RouteMarkers({ route, floorId, index }: { route: ShowRoute; floorId: string; index: SceneIndex }) {
  const d = useMemo(() => routeOnFloor(route, floorId, index), [route, floorId, index])
  return (
    <g>
      {d.start && (
        <At p={d.start}>
          <StartDot />
        </At>
      )}
      {d.end && (
        <At p={d.end}>
          <Pin />
        </At>
      )}
      {d.chips.map((c, i) => (
        <At key={i} p={c.p}>
          <FloorChip text={c.text} floorId={c.floorId} />
        </At>
      ))}
    </g>
  )
}
