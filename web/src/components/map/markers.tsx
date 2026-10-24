// Screen-size markers drawn inside the world group. Each sits at a world point inside a `data-cs` group that
// MapView counter-scales (transform="scale(1 / zoom)") whenever the camera moves.
import type { ReactNode } from 'react'
import { ArrowUpDown, Coffee, DoorOpen, Toilet } from 'lucide-react'
import type { PoiKind, Pt } from './geometry'

export function At({ p, children }: { p: Pt; children: ReactNode }) {
  return (
    <g transform={`translate(${p[0]} ${p[1]})`}>
      <g data-cs="">{children}</g>
    </g>
  )
}

/** Destination / answer marker: the warm guide light resting on the point, with a soft halo. */
export function Pin({ color = 'var(--color-pin)' }: { color?: string }) {
  return (
    <g pointerEvents="none">
      <circle r={20} style={{ fill: color }} fillOpacity={0.18} />
      <circle r={11} style={{ fill: color }} stroke="#fff" strokeWidth={3.5} />
      <circle r={3.5} fill="#fff" />
    </g>
  )
}

/** Route start / You-are-here marker: sky halo + white-bordered sky dot. */
export function StartDot({ r = 8 }: { r?: number }) {
  return (
    <g pointerEvents="none">
      <circle r={r * 2} style={{ fill: 'var(--color-sky)' }} fillOpacity={0.18} />
      <circle r={r} style={{ fill: 'var(--color-sky)' }} stroke="#fff" strokeWidth={3} />
    </g>
  )
}

function StairsGlyph({ size }: { size: number }) {
  return (
    <svg x={-size / 2} y={-size / 2} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#fff"
      strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 20h5v-5h5v-5h5V5" />
    </svg>
  )
}

const POI_STYLE: Record<PoiKind, { color: string }> = {
  toilet: { color: '#7e57c2' },
  food: { color: '#f29900' },
  lift: { color: '#5f6f86' },
  stairs: { color: '#5f6f86' },
  entrance: { color: '#188038' },
}

export function PoiBadge({ kind, r = 9 }: { kind: PoiKind; r?: number }) {
  const size = r * 1.25
  const iconProps = { x: -size / 2, y: -size / 2, width: size, height: size, color: '#fff', strokeWidth: 2.4 }
  return (
    <g pointerEvents="none">
      <circle r={r + 1.5} fill="#fff" />
      <circle r={r} fill={POI_STYLE[kind].color} />
      {kind === 'toilet' && <Toilet {...iconProps} />}
      {kind === 'food' && <Coffee {...iconProps} />}
      {kind === 'lift' && <ArrowUpDown {...iconProps} />}
      {kind === 'entrance' && <DoorOpen {...iconProps} />}
      {kind === 'stairs' && <StairsGlyph size={size} />}
    </g>
  )
}

/** Tappable floor-change chip on the route ("Continue on Floor 2 ↑"). */
export function FloorChip({ text, floorId }: { text: string; floorId: string }) {
  const w = Math.round(text.length * 6.6 + 22)
  return (
    <g data-floor={floorId} style={{ cursor: 'pointer' }}>
      <circle r={5} fill="#fff" strokeWidth={2.5} style={{ stroke: 'var(--color-route-casing)' }} />
      <g transform="translate(0 -14)">
        <rect x={-w / 2} y={-26} width={w} height={26} rx={13}
          style={{ fill: "var(--color-accent)", filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.3))" }} />
        <text y={-9} textAnchor="middle" fontSize={12} fontWeight={600} fill="#fff">
          {text}
        </text>
        {/* bigger invisible hit area for thumbs */}
        <rect x={-w / 2 - 6} y={-34} width={w + 12} height={44} fill="transparent" />
      </g>
    </g>
  )
}
