// SVG layers of the map. World-unit layers (floor, footprints, highlight, route lines) use
// non-scaling strokes; screen-size things (labels, POIs, dots, pins, chips) go through <At>.
import { memo, useEffect, useMemo, useState } from 'react'
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import type { GhostDoorState } from '../../store'
import { LINE_H, labelFor, routeOnFloor, showsPoi } from './derive'
import { At, FloorChip, Pin, PoiBadge, StartDot } from './markers'
import { TONES, TONE_FILL, toSvg, type FloorGeo, type LowerFloor, type Pt, type SpaceGeo } from './geometry'
import type { Surroundings } from './surroundings'

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

/** OpenStreetMap under the building: green, parking, streets (widths in metres, so they scale with the map),
 * tram tracks and neighbouring buildings. Muted so the floor plan stays the subject. */
export const SurroundingsLayer = memo(function SurroundingsLayer({ s }: { s: Surroundings }) {
  const { d } = s
  const road = (path: string | undefined, width: number, color: string, casing: string) =>
    path ? (
      <>
        <path d={path} fill="none" stroke={casing} strokeWidth={width + 1.2} strokeLinecap="round" strokeLinejoin="round" />
        <path d={path} fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" strokeLinejoin="round" />
      </>
    ) : null
  return (
    <g pointerEvents="none">
      {d.green && <path d={d.green} fill="#e2eedd" stroke="#d3e4cc" strokeWidth={0.5} vectorEffect={NS} />}
      {d.parking && <path d={d.parking} fill="#eceaf1" />}
      {d.water && <path d={d.water} fill="none" stroke="#bcd6ea" strokeWidth={3} />}
      {d.path && <path d={d.path} fill="none" stroke="#ffffff" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />}
      {road(d.service, 4, '#ffffff', '#e4e2ec')}
      {road(d.minor, 7, '#ffffff', '#dddae6')}
      {road(d.major, 12, '#fdf3dc', '#ead9b3')}
      {d.tram && <path d={d.tram} fill="none" stroke="#b8b3c8" strokeWidth={0.6} strokeDasharray="2 1.5" />}
      {d.building && (
        <path d={d.building} fill="#e3e1ea" stroke="#d2cfdc" strokeWidth={0.75} vectorEffect={NS} strokeLinejoin="round" />
      )}
    </g>
  )
})

/** Floors below the current one, flat grey, lowest first; the current floor's opaque fills cover what they hide.
 * Nearer floors are a touch darker. No labels, no assets, not tappable. */
export const LowerFloors = memo(function LowerFloors({ floors }: { floors: LowerFloor[] }) {
  if (!floors.length) return null
  return (
    <g pointerEvents="none">
      {floors.map((f) => (
        <path key={f.floorId} d={f.d} fill={mix('#e7e9ec', '#dcdfe3', f.nearness)} stroke="#cfd3d8" strokeWidth={1}
          vectorEffect={NS} strokeLinejoin="round" />
      ))}
    </g>
  )
})

function mix(a: string, b: string, t: number): string {
  const ch = (hex: string, i: number) => parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16)
  return `rgb(${[0, 1, 2].map((i) => Math.round(ch(a, i) + (ch(b, i) - ch(a, i)) * t)).join(',')})`
}

export const Footprints = memo(function Footprints({ geo, visible }: { geo: FloorGeo; visible: boolean }) {
  if (!geo.footprintD) return null
  return (
    <path d={geo.footprintD} fill="#e6e8eb" stroke="#d2d6db" strokeWidth={0.75} vectorEffect={NS} pointerEvents="none"
      opacity={visible ? 1 : 0} style={{ transition: 'opacity 200ms' }} />
  )
})

// ---------- labels and POIs ----------

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
      const poi = showsPoi(s, k)
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
            <g data-cs="">
              <circle r={11} fill="transparent" />
              <circle r={3.5} fill={ASSET_COLOR[a.category] ?? '#a0a5ab'} stroke="#fff" strokeWidth={1.5} />
            </g>
          </g>
        ))}
    </g>
  )
})

// ---------- highlight, selection, closures, affected rooms, ghost door ----------

export function HighlightFill({ geo, ids }: { geo: FloorGeo; ids: string[] }) {
  const d = useMemo(() => ids.map((id) => geo.byId.get(id)?.d ?? '').join(''), [geo, ids])
  if (!d) return null
  return (
    <path d={d} pointerEvents="none" fillOpacity={0.2} strokeWidth={2} vectorEffect={NS} strokeLinejoin="round"
      style={{ fill: 'var(--color-sun)', stroke: 'var(--color-sun)' }} />
  )
}

export function ClosedFill({ geo, index, closedIds }: { geo: FloorGeo; index: SceneIndex; closedIds: string[] }) {
  const d = useMemo(() => {
    if (!closedIds.length) return ''
    const spaceIds = new Set<string>()
    for (const id of closedIds) {
      if (geo.byId.has(id)) spaceIds.add(id)
      const c = index.connectorById.get(id)
      if (c) {
        for (const [fid, sid] of c.stops) {
          if (fid === geo.floorId && geo.byId.has(sid)) spaceIds.add(sid)
        }
      }
    }
    return [...spaceIds].map((id) => geo.byId.get(id)?.d ?? '').join('')
  }, [geo, index, closedIds])
  if (!d) return null
  return (
    <g pointerEvents="none">
      <path d={d} style={{ fill: 'var(--color-rose-soft)' }} />
      <path d={d} fill="none" style={{ stroke: 'var(--color-rose)' }} strokeWidth={2} strokeDasharray="5 3" vectorEffect={NS} strokeLinejoin="round" />
    </g>
  )
}

export function AffectedFill({ geo, ids }: { geo: FloorGeo; ids: string[] }) {
  const d = useMemo(() => ids.map((id) => geo.byId.get(id)?.d ?? '').join(''), [geo, ids])
  if (!d) return null
  return (
    <path d={d} pointerEvents="none" fillOpacity={0.22} style={{ fill: 'var(--color-sun)', stroke: 'var(--color-sun)' }} strokeWidth={1.5}
      vectorEffect={NS} strokeLinejoin="round" />
  )
}

export function GhostDoorLayer({ door, floorId }: { door: GhostDoorState | null; floorId: string }) {
  if (!door || door.floorId !== floorId) return null
  const hl = 0.6
  const [x, y] = door.pos
  const a = toSvg(door.vertical ? [x, y - hl] : [x - hl, y])
  const c = toSvg(door.vertical ? [x, y + hl] : [x + hl, y])
  const center = toSvg([x, y])
  return (
    <g pointerEvents="none">
      <At p={center}>
        <circle
          r={16}
          style={{ fill: 'var(--color-sky)', animation: 'ttyb-pulse 1.6s ease-out infinite' }}
          fillOpacity={0.18}
        />
      </At>
      <line x1={a[0]} y1={a[1]} x2={c[0]} y2={c[1]} stroke="#fff" strokeWidth={7} vectorEffect={NS} />
      <line
        x1={a[0]}
        y1={a[1]}
        x2={c[0]}
        y2={c[1]}
        style={{ stroke: 'var(--color-sky)' }}
        strokeWidth={3.5}
        strokeDasharray={door.applied ? undefined : '4 3'}
        vectorEffect={NS}
      />
    </g>
  )
}

export function HighlightDots({ points }: { points: { id: string; p: Pt; tappable: boolean }[] }) {
  return (
    <g>
      {points.map(({ id, p, tappable }) => (
        <g key={id} transform={`translate(${p[0]} ${p[1]})`} data-asset={tappable ? id : undefined}>
          <g data-cs="">
            <circle r={12} fill="transparent" />
            <circle r={10} fillOpacity={0.22} style={{ fill: 'var(--color-sun)' }} />
            <circle r={5.5} stroke="#fff" strokeWidth={2} style={{ fill: 'var(--color-sun)' }} />
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

/** Route lines (world layer) — drawn in once per new route, unless `instant` (the caller draws it in itself). */
export function RouteLines({
  route,
  floorId,
  index,
  instant = false,
}: {
  route: ShowRoute
  floorId: string
  index: SceneIndex
  instant?: boolean
}) {
  const drawing = useMemo(() => routeOnFloor(route, floorId, index), [route, floorId, index])
  const [prog, setProg] = useState({ route, frac: 0 })
  useEffect(() => {
    if (instant) return
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const f = Math.min(1, (now - t0) / DRAW_MS)
      setProg({ route, frac: 1 - Math.pow(1 - f, 2) })
      if (f < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [route, instant])
  const frac = instant ? 1 : prog.route === route ? prog.frac : 0
  const lines = truncate(drawing.lines, frac)
  const pts = (l: Pt[]) => l.map((p) => `${p[0]},${p[1]}`).join(' ')
  return (
    <g pointerEvents="none" fill="none" strokeLinejoin="round" strokeLinecap="round">
      {/* the guide's warm glow under the path */}
      {lines.map((l, i) => (
        <polyline key={`g${i}`} points={pts(l)} strokeWidth={22} vectorEffect={NS} strokeOpacity={0.22}
          style={{ stroke: 'var(--color-sun)' }} />
      ))}
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
export function RouteMarkers({
  route,
  floorId,
  index,
  hideStart,
}: {
  route: ShowRoute
  floorId: string
  index: SceneIndex
  hideStart?: boolean
}) {
  const d = useMemo(() => routeOnFloor(route, floorId, index), [route, floorId, index])
  return (
    <g>
      {d.start && !hideStart && (
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
