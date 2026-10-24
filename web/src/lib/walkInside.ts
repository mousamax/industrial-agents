// How a route is drawn across one space: the shortest line from where it enters to where it leaves that stays
// inside the space's outline, bending round its corners instead of cutting through walls. Lengths come from
// the backend; this only draws. Outlines may carry an enclosed room as a zero-width slit (one ring out to it
// and back), so a slit is not treated as a wall.
import type { XY } from '../api/types'

const CLEARANCE_M = 0.45 // how far off a corner the line bends
const EPS = 1e-9
const AROUND = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]].map(
  ([x, y]) => [x / Math.hypot(x, y), y / Math.hypot(x, y)] as const,
)

type Wall = readonly [XY, XY]

const same = (a: XY, b: XY) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6
const cross = (o: XY, a: XY, b: XY) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1])

function ringOf(polygon: readonly XY[]): XY[] {
  const ring = polygon.filter((p, i) => i === 0 || !same(p, polygon[i - 1]))
  if (ring.length > 1 && same(ring[0], ring[ring.length - 1])) ring.pop()
  return ring
}

function inside(p: XY, ring: XY[]): boolean {
  let hit = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

/** Edges of the ring, minus slit edges (walked once each way). */
function wallsOf(ring: XY[]): Wall[] {
  const edges: Wall[] = ring.map((p, i) => [p, ring[(i + 1) % ring.length]] as const)
  return edges.filter(([a, b]) => !edges.some(([c, d]) => same(a, d) && same(b, c)))
}

function crosses(p: XY, q: XY, [a, b]: Wall): boolean {
  return cross(a, b, p) * cross(a, b, q) < -EPS && cross(p, q, a) * cross(p, q, b) < -EPS
}

function sees(p: XY, q: XY, ring: XY[], walls: Wall[]): boolean {
  if (walls.some((w) => crosses(p, q, w))) return false
  return [0.25, 0.5, 0.75].every((t) => inside([p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t], ring))
}

/** Points just inside each corner of the outline, where a walk can bend. Probed in 8 directions rather than
 * along the corner's bisector, because a slit can start at a corner and hide which two walls meet there. */
function bends(ring: XY[]): XY[] {
  const out: XY[] = []
  for (const v of ring) {
    for (const [dx, dy] of AROUND) {
      const p: XY = [v[0] + dx * CLEARANCE_M, v[1] + dy * CLEARANCE_M]
      if (inside(p, ring) && !out.some((q) => dist(p, q) < 0.05)) out.push(p)
    }
  }
  return out
}

/** Points where the walk from `from` to `to` bends inside `polygon` (empty when it can go straight). */
export function walkInside(polygon: readonly XY[], from: XY, to: XY): XY[] {
  const ring = ringOf(polygon)
  if (ring.length < 3) return []
  const walls = wallsOf(ring)
  if (sees(from, to, ring, walls)) return []
  const nodes = [from, to, ...bends(ring)]
  const best = nodes.map(() => Infinity)
  const prev = nodes.map(() => -1)
  const done = nodes.map(() => false)
  best[0] = 0
  for (;;) {
    let i = -1
    for (let k = 0; k < nodes.length; k++) if (!done[k] && best[k] < Infinity && (i < 0 || best[k] < best[i])) i = k
    if (i < 0 || i === 1) break
    done[i] = true
    for (let k = 1; k < nodes.length; k++) {
      if (done[k]) continue
      const d = best[i] + dist(nodes[i], nodes[k])
      if (d < best[k] && sees(nodes[i], nodes[k], ring, walls)) {
        best[k] = d
        prev[k] = i
      }
    }
  }
  if (prev[1] < 0) return []
  const path: XY[] = []
  for (let k = prev[1]; k > 0; k = prev[k]) path.unshift(nodes[k])
  return path
}
