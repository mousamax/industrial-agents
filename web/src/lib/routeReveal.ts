// Drawing a route in: the line grows from the start through every floor to the destination, at walking pace,
// so each answer shows how you get there rather than only where. `truncateRoute` gives the drawn part,
// `revealTimeline` how much is drawn when.
import type { ShowRoute } from '../api/types'

/** The camera settling on the answer before the line starts, and the line itself: it walks at `walkMps` (map
 *  metres per second), every floor stays on screen at least `minFloorMs` (the stair floors too, where the line
 *  barely moves), and each floor change takes `hopMs`. */
export const REVEAL_TIMING = {
  settleMs: 700,
  walkMps: 19,
  minFloorMs: 700,
  hopMs: 560,
  /** Phone: the camera follows the head of the line, keeping this many metres around it in view. */
  followRadiusM: 4.5,
  /** How long the map stays on the destination's floor before it goes back to the start. */
  endHoldMs: 800,
}

/** Metres a floor change counts for while the route draws in (the stairs between two floors). */
const FLOOR_CHANGE_M: number = 8
function routeParts(route: ShowRoute) {
  const segLen = route.segments.map((s) =>
    s.points.slice(1).reduce((acc, p, i) => acc + Math.hypot(p[0] - s.points[i][0], p[1] - s.points[i][1]), 0),
  )
  const hops = route.segments.map((s, i) => (i > 0 && route.segments[i - 1].floor_id !== s.floor_id ? FLOOR_CHANGE_M : 0))
  const total = segLen.reduce((a, b) => a + b, 0) + hops.reduce((a, b) => a + b, 0)
  return { segLen, hops, total }
}

/** How far the route is drawn (0..1, for truncateRoute) `ms` after it starts, and how long the whole draw takes. */
export function revealTimeline(route: ShowRoute): { totalMs: number; at: (ms: number) => number } {
  const { segLen, hops, total } = routeParts(route)
  const T = REVEAL_TIMING
  const keys: [number, number][] = [[0, 0]] // (ms, metres drawn)
  let ms = 0
  let m = 0
  let si = 0
  while (si < route.segments.length) {
    if (hops[si]) keys.push([(ms += T.hopMs), (m += hops[si])])
    // One floor: this segment and any that follow on the same floor.
    let len = segLen[si]
    while (si + 1 < route.segments.length && !hops[si + 1]) len += segLen[++si]
    keys.push([(ms += Math.max(T.minFloorMs, (len / T.walkMps) * 1000)), (m += len)])
    si++
  }
  return {
    totalMs: ms,
    at: (t) => {
      if (!total || t >= ms) return 1
      const i = keys.findIndex(([k]) => k > t)
      const [t0, m0] = keys[i - 1]
      const [t1, m1] = keys[i]
      return (m0 + ((t - t0) / (t1 - t0)) * (m1 - m0)) / total
    },
  }
}

/** The first `frac` of a route by walking length; the floor changes count FLOOR_CHANGE_M each. A floor change
 *  shows once the draw is past its middle (the 3D link is drawn whole, end to end). */
export function truncateRoute(route: ShowRoute, frac: number): ShowRoute {
  if (frac >= 1) return route
  const { hops, total } = routeParts(route)
  let left = total * Math.max(0, frac)
  const segments: ShowRoute['segments'] = []
  for (let si = 0; si < route.segments.length; si++) {
    if (hops[si]) {
      if (left < hops[si] / 2) break
      left = Math.max(0, left - hops[si])
    } else if (si > 0 && left <= 0) break
    const pts = route.segments[si].points
    if (!pts.length) continue
    const out: ShowRoute['segments'][number]['points'] = [pts[0]]
    for (let i = 1; i < pts.length && left > 0; i++) {
      const [a, b] = [pts[i - 1], pts[i]]
      const d = Math.hypot(b[0] - a[0], b[1] - a[1])
      if (d <= left) {
        out.push(b)
        left -= d
      } else {
        const t = left / d
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
        left = 0
      }
    }
    segments.push({ floor_id: route.segments[si].floor_id, points: out })
  }
  return { ...route, segments }
}

