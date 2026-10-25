// Screen-size markers over the 3D canvas (pins, route ends, floor chips, POIs, room labels). Plain HTML: each
// marker is a zero-size box at a world point (`data-x`/`data-y`, SVG coordinates) that MapView moves every frame
// with engine.project, writing `transform` straight to the DOM. This component only re-renders when what is shown
// changes (floor, zoom band, selection, route), never per camera frame.
import { memo, useMemo, type ReactNode, type RefObject } from 'react'
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import type { View3DIntent } from '../../store'
import { labelFor, routeOnFloor, showsPoi } from './derive'
import { toSvg, type FloorGeo, type Pt, type SpaceGeo } from './geometry'
import { Pin, PoiBadge, StartDot } from './markers'

/** Labels in 3D: perspective shrinks the far half of the view, so be stricter than 2D and cap the count. */
const LABEL_K_FACTOR = 0.75
const MAX_LABELS = 40
const HALO = '0 0 2px #fff, 0 0 2px #fff, 0 0 3px #fff, 0 0 4px #fff'

export interface OverlaySelection {
  space: SpaceGeo | null
  p: Pt
  lines: string[]
}

/**
 * Zero-size anchor at a world point; children are laid out around (0, 0). `cull`: hide near the horizon/edges.
 * `elev`: real elevation in metres when the point is not on the selected floor.
 */
function Anchor({ p, cull, z, elev, children }: { p: Pt; cull?: boolean; z?: number; elev?: number; children: ReactNode }) {
  return (
    <div
      data-x={p[0]}
      data-y={p[1]}
      data-elev={elev}
      data-cull={cull ? '' : undefined}
      className="absolute left-0 top-0 h-0 w-0"
      style={{ visibility: 'hidden', zIndex: z }}
    >
      {children}
    </div>
  )
}

/** Inline SVG marker; `box` = [minX, minY, width, height] of the drawing around the anchor. */
function SvgMark({ box, children }: { box: [number, number, number, number]; children: ReactNode }) {
  const [x, y, w, h] = box
  return (
    <svg width={w} height={h} viewBox={`${x} ${y} ${w} ${h}`} className="absolute overflow-visible"
      style={{ left: x, top: y }}>
      {children}
    </svg>
  )
}

function Label({ lines, top, strong }: { lines: string[]; top: number; strong?: boolean }) {
  return (
    <div
      className="absolute left-0 -translate-x-1/2 whitespace-nowrap text-center"
      style={{
        top,
        fontSize: strong ? 12 : 11,
        lineHeight: strong ? '14px' : '13px',
        fontWeight: strong ? 600 : 500,
        color: strong ? 'var(--color-ink)' : 'var(--color-map-label)',
        textShadow: HALO,
      }}
    >
      {lines.map((l, i) => (
        <div key={i}>{l}</div>
      ))}
    </div>
  )
}

function HtmlFloorChip({ text, floorId }: { text: string; floorId: string }) {
  return (
    <>
      <SvgMark box={[-8, -8, 16, 16]}>
        <circle r={5} fill="#fff" strokeWidth={2.5} style={{ stroke: 'var(--color-route-casing)' }} />
      </SvgMark>
      {/* Generous padding: the hit area for thumbs is bigger than the pill. */}
      <div
        data-floor={floorId}
        className="pointer-events-auto absolute left-0 -translate-x-1/2 cursor-pointer px-1.5 pb-2 pt-1.5"
        style={{ bottom: 6 }}
      >
        <div className="whitespace-nowrap rounded-full bg-accent px-3 py-1 text-xs font-semibold text-white shadow-md">
          {text}
        </div>
      </div>
    </>
  )
}

export interface Overlay3DProps {
  rootRef: RefObject<HTMLDivElement | null>
  geo: FloorGeo
  index: SceneIndex
  k: number
  sel: OverlaySelection | null
  route: ShowRoute | null
  dots: { id: string; p: Pt }[]
  link: View3DIntent['link']
  /** While a route draws in (show mode): no room names or floor chips over the line. */
  quiet?: boolean
}

/** Where the link's label sits: halfway along the line, or on the room it points to. */
function linkLabelAt(index: SceneIndex, link: NonNullable<View3DIntent['link']>): { p: Pt; elev: number } | null {
  const a = index.spaceById.get(link.from)
  const b = index.spaceById.get(link.to)
  if (!a || !b) return null
  const ea = index.floorById.get(a.floor_id)?.elevation_m ?? 0
  const eb = index.floorById.get(b.floor_id)?.elevation_m ?? 0
  if (link.labelAt === 'to') return { p: toSvg(b.anchor), elev: eb }
  return { p: toSvg([(a.anchor[0] + b.anchor[0]) / 2, (a.anchor[1] + b.anchor[1]) / 2]), elev: (ea + eb) / 2 + 0.6 }
}

export const Overlay3D = memo(function Overlay3D({
  rootRef,
  geo,
  index,
  k,
  sel,
  route,
  dots,
  link,
  quiet = false,
}: Overlay3DProps) {
  const skipId = sel?.space?.id ?? null
  const labels = useMemo(() => {
    const kk = k * LABEL_K_FACTOR
    const out: { s: SpaceGeo; poi: boolean; label: string[] | null }[] = []
    for (const s of geo.spaces) {
      if (s.id === skipId) continue
      const poi = showsPoi(s, kk)
      const label = labelFor(s, kk, poi)
      if (poi || label) out.push({ s, poi, label })
    }
    return out.sort((a, b) => b.s.area - a.s.area).slice(0, MAX_LABELS)
  }, [geo, k, skipId])
  const r = useMemo(() => (route ? routeOnFloor(route, geo.floorId, index) : null), [route, geo.floorId, index])
  const linkAt = useMemo(() => (link?.label ? linkLabelAt(index, link) : null), [index, link])

  return (
    <div ref={rootRef} className="pointer-events-none absolute inset-0 overflow-hidden">
      {labels.map(({ s, poi, label }) => (
        <Anchor key={s.id} p={s.anchor} cull>
          {poi && (
            <SvgMark box={[-11, -11, 22, 22]}>
              <PoiBadge kind={s.poi!} />
            </SvgMark>
          )}
          {label && !quiet && <Label lines={label} top={poi ? 13 : -(label.length * 13) / 2} />}
        </Anchor>
      ))}
      {dots.map(({ id, p }) => (
        <Anchor key={id} p={p} z={1}>
          <SvgMark box={[-10, -10, 20, 20]}>
            <circle r={9} fillOpacity={0.2} style={{ fill: 'var(--color-accent)' }} />
            <circle r={5} stroke="#fff" strokeWidth={2} style={{ fill: 'var(--color-accent)' }} />
          </SvgMark>
        </Anchor>
      ))}
      {r?.start && (
        <Anchor p={r.start} z={2}>
          <SvgMark box={[-10, -10, 20, 20]}>
            <StartDot />
          </SvgMark>
        </Anchor>
      )}
      {r?.end && (
        <Anchor p={r.end} z={3}>
          <SvgMark box={[-12, -34, 24, 38]}>
            <Pin />
          </SvgMark>
        </Anchor>
      )}
      {!quiet && r?.chips.map((c, i) => (
        <Anchor key={i} p={c.p} z={4}>
          <HtmlFloorChip text={c.text} floorId={c.floorId} />
        </Anchor>
      ))}
      {link?.label && linkAt && (
        <Anchor p={linkAt.p} elev={linkAt.elev} z={6}>
          <div className="font-display absolute left-0 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full bg-sun px-3.5 py-1.5 text-[13px] font-semibold text-ink shadow-[0_6px_18px_rgb(255_107_90/0.35)]">
            {link.label}
          </div>
        </Anchor>
      )}
      {sel && (
        <Anchor p={sel.p} z={5}>
          <SvgMark box={[-12, -34, 24, 38]}>
            <Pin />
          </SvgMark>
          {sel.lines.length > 0 && <Label lines={sel.lines} top={5} strong />}
        </Anchor>
      )}
    </div>
  )
})
