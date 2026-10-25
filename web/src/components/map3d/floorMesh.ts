// Static geometry of one floor, built once and cached: slabs (one triangulated top per space, coloured by tone,
// with a thin plinth around it) and walls along every space outline with gaps at the openings.
// Built in floor-local coordinates (y = 0 is the floor top); Map3D places the floor at its scaled elevation.
// three's frame: (X, Y, Z) = (x, height, -y).
import * as THREE from 'three'
import type { SceneIndex } from '../../lib/sceneIndex'
import { toneOf, type BBox } from '../map/geometry'
import { SLAB_SIDE, TONE_HEX, WALL_SIDE, WALL_TOP } from './palette'

export type P2 = [number, number]

/** Slab thickness below the floor top (the plinth seen at the building edge when tilted). */
export const SLAB_T = 0.22
/** Max lift of small spaces over big ones, so overlapping polygons (OSM) do not z-fight. */
const MAX_LIFT = 0.03

export interface FloorGeometry {
  slab: THREE.BufferGeometry
  /** Stairwell floors, drawn see-through so a route can be followed down and up the stairs through the floors. */
  see: THREE.BufferGeometry
  /** Unit-height walls (y in 0..1, scaled by the mesh), with an `aOff` attribute for zoom-dependent thickness. */
  walls: THREE.BufferGeometry
  bbox: BBox | null
  /** Lift of each space's slab top (m), for overlays drawn on top of it. */
  liftById: Map<string, number>
}

/** Drop the closing duplicate and repeated points. */
export function cleanPoly(poly: readonly (readonly number[])[]): P2[] {
  const out: P2[] = []
  for (const p of poly) {
    const q: P2 = [p[0], p[1]]
    const last = out[out.length - 1]
    if (!last || Math.hypot(q[0] - last[0], q[1] - last[1]) > 1e-4) out.push(q)
  }
  while (out.length > 2 && Math.hypot(out[0][0] - out[out.length - 1][0], out[0][1] - out[out.length - 1][1]) < 1e-4)
    out.pop()
  return out
}

export function signedArea(poly: P2[]): number {
  let a = 0
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) a += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]
  return a / 2
}

// ---------- tiny triangle writer ----------

type V3 = [number, number, number]

class Tris {
  pos: number[] = []
  col: number[] = []
  off: number[] = []
  /** Triangle (a, b, c) wound so its face normal points along `n`. Optional xz offsets feed `aOff`. */
  tri(a: V3, b: V3, c: V3, n: V3, color: THREE.Color, oa?: P2, ob?: P2, oc?: P2) {
    const pa = oa ? [a[0] + oa[0] * 0.05, a[1], a[2] + oa[1] * 0.05] : a
    const pb = ob ? [b[0] + ob[0] * 0.05, b[1], b[2] + ob[1] * 0.05] : b
    const pc = oc ? [c[0] + oc[0] * 0.05, c[1], c[2] + oc[1] * 0.05] : c
    const ux = pb[0] - pa[0], uy = pb[1] - pa[1], uz = pb[2] - pa[2]
    const vx = pc[0] - pa[0], vy = pc[1] - pa[1], vz = pc[2] - pa[2]
    const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx
    const flip = cx * n[0] + cy * n[1] + cz * n[2] < 0
    const vs = flip ? [a, c, b] : [a, b, c]
    const os = flip ? [oa, oc, ob] : [oa, ob, oc]
    for (let i = 0; i < 3; i++) {
      this.pos.push(vs[i][0], vs[i][1], vs[i][2])
      this.col.push(color.r, color.g, color.b)
      if (oa) this.off.push(os[i]![0], os[i]![1])
    }
  }
  quad(a: V3, b: V3, c: V3, d: V3, n: V3, color: THREE.Color, oa?: P2, ob?: P2, oc?: P2, od?: P2) {
    this.tri(a, b, c, n, color, oa, ob, oc)
    this.tri(a, c, d, n, color, oa, oc, od)
  }
  geometry(): THREE.BufferGeometry {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.pos, 3))
    g.setAttribute('color', new THREE.Float32BufferAttribute(this.col, 3))
    if (this.off.length) g.setAttribute('aOff', new THREE.Float32BufferAttribute(this.off, 2))
    g.computeBoundingSphere()
    return g
  }
}

// Baked light for vertical faces: brighter toward the south-west (the side a tilted camera sees).
const LIGHT_XZ: P2 = (() => {
  const l = Math.hypot(-0.45, 0.9)
  return [-0.45 / l, 0.9 / l]
})()
function sideShade(base: THREE.Color, nx: number, nz: number): THREE.Color {
  const f = 0.86 + 0.14 * (nx * LIGHT_XZ[0] + nz * LIGHT_XZ[1])
  return base.clone().multiplyScalar(f)
}

// ---------- door gaps ----------

interface Gap {
  edge: number
  t0: number
  t1: number
}

function nearestEdge(p: P2, poly: P2[]): { edge: number; t: number; dist: number } | null {
  let best: { edge: number; t: number; dist: number } | null = null
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]
    const b = poly[(i + 1) % poly.length]
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const len = Math.hypot(dx, dy)
    if (len < 1e-6) continue
    const t = Math.max(0, Math.min(len, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len))
    const dist = Math.hypot(p[0] - (a[0] + (dx * t) / len), p[1] - (a[1] + (dy * t) / len))
    if (!best || dist < best.dist) best = { edge: i, t, dist }
  }
  return best
}

/** Solid stretches [s0, s1] (metres along the edge) left after cutting the gaps out of [0, len]. */
function solidPieces(len: number, gaps: Gap[]): P2[] {
  const cuts = gaps.map((g) => [Math.max(0, g.t0), Math.min(len, g.t1)] as P2).filter((c) => c[1] > c[0])
  cuts.sort((a, b) => a[0] - b[0])
  const out: P2[] = []
  let s = 0
  for (const [c0, c1] of cuts) {
    if (c0 > s) out.push([s, c0])
    s = Math.max(s, c1)
  }
  if (s < len) out.push([s, len])
  return out.filter((p) => p[1] - p[0] > 0.03)
}

// ---------- build ----------

export function buildFloorGeometry(index: SceneIndex, floorId: string): FloorGeometry {
  const spaces = (index.spacesByFloor.get(floorId) ?? [])
    .map((s) => ({ s, poly: cleanPoly(s.polygon) }))
    .filter((x) => x.poly.length >= 3)

  // Smaller spaces sit a few millimetres higher (log-area ramp): nested / overlapping polygons stay clean.
  const areas = spaces.map((x) => Math.max(0.5, Math.abs(signedArea(x.poly))))
  const lmin = Math.log(Math.min(...areas, 1))
  const lmax = Math.log(Math.max(...areas, 2))
  const liftById = new Map<string, number>()

  const gapsBySpace = new Map<string, Gap[]>()
  const polyById = new Map(spaces.map((x) => [x.s.id, x.poly]))
  for (const o of index.openingsByFloor.get(floorId) ?? []) {
    const half = Math.max(0.3, o.width_m || 0.9) / 2
    for (const sid of o.space_ids) {
      const poly = polyById.get(sid)
      if (!poly) continue
      const e = nearestEdge([o.position[0], o.position[1]], poly)
      if (!e || e.dist > 1.5) continue
      const list = gapsBySpace.get(sid) ?? []
      list.push({ edge: e.edge, t0: e.t - half, t1: e.t + half })
      gapsBySpace.set(sid, list)
    }
  }

  const solid = new Tris()
  const see = new Tris()
  const walls = new Tris()
  const sideCol = new THREE.Color(SLAB_SIDE)
  const wallTop = new THREE.Color(WALL_TOP)
  const wallSide = new THREE.Color(WALL_SIDE)
  const up: V3 = [0, 1, 0]
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity

  spaces.forEach(({ s, poly }, i) => {
    const lift = 0.002 + MAX_LIFT * (1 - (Math.log(areas[i]) - lmin) / Math.max(lmax - lmin, 1e-6))
    liftById.set(s.id, lift)
    const top = new THREE.Color(TONE_HEX[toneOf(s)])
    const slab = s.type.toLowerCase() === 'stair' || /stair/.test(s.room_type.toLowerCase()) ? see : solid
    for (const [x, y] of poly) {
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    }

    // Top face.
    const tris = THREE.ShapeUtils.triangulateShape(poly.map(([x, y]) => new THREE.Vector2(x, y)), [])
    for (const [a, b, c] of tris) {
      const A = poly[a], B = poly[b], C = poly[c]
      slab.tri([A[0], lift, -A[1]], [B[0], lift, -B[1]], [C[0], lift, -C[1]], up, top)
    }

    // Plinth sides and walls, edge by edge.
    const ccw = signedArea(poly) > 0
    const gaps = gapsBySpace.get(s.id) ?? []
    for (let e = 0; e < poly.length; e++) {
      const a = poly[e]
      const b = poly[(e + 1) % poly.length]
      const dx = b[0] - a[0]
      const dy = b[1] - a[1]
      const len = Math.hypot(dx, dy)
      if (len < 1e-4) continue
      const ux = dx / len, uy = dy / len
      // outward normal in scene coords, then in three's xz (z = -y)
      const onx = ccw ? uy : -uy
      const ony = ccw ? -ux : ux
      const n3: V3 = [onx, 0, -ony]
      slab.quad([a[0], -SLAB_T, -a[1]], [b[0], -SLAB_T, -b[1]], [b[0], lift, -b[1]], [a[0], lift, -a[1]], n3,
        sideShade(sideCol, onx, -ony))

      // Walls: centred on the outline, thickness from aOff * uHalfT (shader), unit height.
      const u3: P2 = [ux, -uy] // along the edge, three xz
      const p3: P2 = [-uy, -ux] // perpendicular (scene (-uy, ux) -> xz (-uy, -ux))
      for (const [s0, s1] of solidPieces(len, gaps.filter((g) => g.edge === e))) {
        const A: P2 = [a[0] + ux * s0, -(a[1] + uy * s0)]
        const B: P2 = [a[0] + ux * s1, -(a[1] + uy * s1)]
        const o = (sp: number, su: number): P2 => [p3[0] * sp + u3[0] * su, p3[1] * sp + u3[1] * su]
        const v = (P: P2, h: number): V3 => [P[0], h, P[1]]
        // top
        walls.quad(v(A, 1), v(B, 1), v(B, 1), v(A, 1), up, wallTop, o(-1, -1), o(-1, 1), o(1, 1), o(1, -1))
        // long sides
        for (const sp of [-1, 1]) {
          const n: V3 = [p3[0] * sp, 0, p3[1] * sp]
          walls.quad(v(A, 0), v(B, 0), v(B, 1), v(A, 1), n, sideShade(wallSide, n[0], n[2]),
            o(sp, -1), o(sp, 1), o(sp, 1), o(sp, -1))
        }
        // end caps
        for (const [P, su] of [[A, -1], [B, 1]] as [P2, number][]) {
          const n: V3 = [u3[0] * su, 0, u3[1] * su]
          walls.quad(v(P, 0), v(P, 0), v(P, 1), v(P, 1), n, sideShade(wallSide, n[0], n[2]),
            o(-1, su), o(1, su), o(1, su), o(-1, su))
        }
      }
    }
  })

  return {
    slab: solid.geometry(),
    see: see.geometry(),
    walls: walls.geometry(),
    bbox: minX <= maxX ? { minX, minY, maxX, maxY } : null,
    liftById,
  }
}
