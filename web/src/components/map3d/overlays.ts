// Dynamic overlays: route tube, highlight / selection on spaces and assets. Small geometry, rebuilt when the
// inputs change or the zoom moves enough that the screen-size widths (px / k) drift.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import type { AssetSlot } from './assets'
import { cleanPoly, signedArea, type P2 } from './floorMesh'
import { finish } from './models'
import { ROUTE, ROUTE_CASING } from './palette'

/** Box spanning a straight run in the horizontal plane, from y0 to y1 (three coords). */
function run(a: P2, b: P2, width: number, y0: number, y1: number, c: string): THREE.BufferGeometry | null {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len = Math.hypot(dx, dy)
  if (len < 1e-4 || y1 <= y0) return null
  const g = new THREE.BoxGeometry(len, y1 - y0, width)
  g.rotateY(Math.atan2(dy, dx))
  g.translate((a[0] + b[0]) / 2, (y0 + y1) / 2, -(a[1] + b[1]) / 2)
  return finish(g, c)
}

function disc(p: P2, r: number, y0: number, y1: number, c: string, seg = 14): THREE.BufferGeometry {
  const g = new THREE.CylinderGeometry(r, r, y1 - y0, seg, 1)
  g.translate(p[0], (y0 + y1) / 2, -p[1])
  return finish(g, c)
}

function merge(parts: (THREE.BufferGeometry | null)[]): THREE.BufferGeometry | null {
  const ok = parts.filter((p): p is THREE.BufferGeometry => !!p)
  if (!ok.length) return null
  const g = mergeGeometries(ok, false)
  for (const p of ok) p.dispose()
  g.computeBoundingSphere()
  return g
}

/** Height (three Y, absolute) of each visible floor; floors not in the map are cut away. */
export type FloorY = Map<string, number>

/**
 * Route: a raised darker casing with the Google-blue line on top, per floor segment, plus slanted links between
 * consecutive segments on different floors. A link to a cut-away floor becomes a stub rising past the walls.
 */
export function buildRoute(route: ShowRoute, floorY: FloorY, selectedY: number, stubTop: number, k: number): THREE.BufferGeometry | null {
  const wc = Math.min(4, Math.max(0.28, 9 / k))
  const wi = wc * 0.62
  const parts: (THREE.BufferGeometry | null)[] = []
  const lift = 0.04
  const hc = 0.16
  route.segments.forEach((seg, i) => {
    const y = floorY.get(seg.floor_id)
    const pts = seg.points.map((p) => [p[0], p[1]] as P2)
    if (y !== undefined && pts.length) {
      for (let j = 1; j < pts.length; j++) {
        parts.push(run(pts[j - 1], pts[j], wc, y + lift, y + hc, ROUTE_CASING))
        parts.push(run(pts[j - 1], pts[j], wi, y + hc, y + hc + 0.012, ROUTE))
      }
      for (const p of pts) {
        parts.push(disc(p, wc / 2, y + lift, y + hc, ROUTE_CASING))
        parts.push(disc(p, wi / 2, y + hc, y + hc + 0.012, ROUTE))
      }
    }
    const next = route.segments[i + 1]
    if (!next || next.floor_id === seg.floor_id || !seg.points.length || !next.points.length) return
    const y1 = floorY.get(next.floor_id)
    const a = seg.points[seg.points.length - 1]
    const b = next.points[0]
    if (y !== undefined && y1 !== undefined) parts.push(link([a[0], y + hc, -a[1]], [b[0], y1 + hc, -b[1]], wi * 0.6))
    else if (y !== undefined || y1 !== undefined) {
      // One end is on a cut-away floor above: rise from the visible end past the cutaway.
      const p = y !== undefined ? a : b
      const y0 = (y ?? y1)!
      if (y0 <= selectedY + 1e-3) parts.push(link([p[0], y0 + hc, -p[1]], [p[0], stubTop, -p[1]], wi * 0.6))
    }
  })
  return merge(parts)
}

function link(a: [number, number, number], b: [number, number, number], r: number): THREE.BufferGeometry | null {
  const va = new THREE.Vector3(...a)
  const vb = new THREE.Vector3(...b)
  const dir = vb.clone().sub(va)
  const len = dir.length()
  if (len < 1e-3) return null
  const g = new THREE.CylinderGeometry(r, r, len, 10, 1)
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize()))
  g.translate((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
  return finish(g, ROUTE)
}

// ---------- link between two rooms ----------

/**
 * Two rooms, each filled on its own floor, joined by a dashed line from centre to centre. Drawn without depth test
 * so the line and a room hidden under the selected floor's slab still show, like an x-ray.
 */
export function buildLink(
  index: SceneIndex,
  ids: [string, string],
  floorY: (floorId: string) => number,
  k: number,
): { fill: THREE.BufferGeometry | null; line: THREE.BufferGeometry | null } {
  const fills: THREE.BufferGeometry[] = []
  const ends: [number, number, number][] = []
  const lift = 0.6
  for (const id of ids) {
    const s = index.spaceById.get(id)
    if (!s) continue
    const y = floorY(s.floor_id)
    const poly = cleanPoly(s.polygon)
    if (poly.length >= 3) fills.push(fillGeometry(poly, y + 0.05))
    ends.push([s.anchor[0], y + lift, -s.anchor[1]])
  }
  const r = Math.min(0.5, Math.max(0.07, 2.6 / k))
  const parts: (THREE.BufferGeometry | null)[] = []
  if (ends.length === 2) {
    const [a, b] = ends.map((e) => new THREE.Vector3(...e))
    const len = a.distanceTo(b)
    const dash = Math.max(r * 5, 0.4)
    const n = Math.max(1, Math.floor(len / (dash * 1.8)))
    for (let i = 0; i < n; i++) {
      const t0 = i / n
      const t1 = t0 + (1 / n) * 0.55
      const p0 = a.clone().lerp(b, t0)
      const p1 = a.clone().lerp(b, t1)
      parts.push(link([p0.x, p0.y, p0.z], [p1.x, p1.y, p1.z], r))
    }
  }
  for (const e of ends) {
    const g = new THREE.SphereGeometry(r * 3, 16, 12)
    g.translate(...e)
    parts.push(finish(g, ROUTE))
  }
  const fill = fills.length ? mergeGeometries(fills, false) : null
  for (const f of fills) f.dispose()
  fill?.computeBoundingSphere()
  return { fill, line: merge(parts) }
}

// ---------- highlight & selection ----------

function fillGeometry(poly: P2[], y: number): THREE.BufferGeometry {
  const tris = THREE.ShapeUtils.triangulateShape(poly.map(([x, yy]) => new THREE.Vector2(x, yy)), [])
  const pos: number[] = []
  for (const t of tris) {
    // ShapeUtils keeps the input orientation; emit counter-clockwise seen from above (+Y normal).
    const [a, b, c] = signedArea(poly) > 0 ? t : [t[0], t[2], t[1]]
    for (const idx of [a, b, c]) pos.push(poly[idx][0], y, -poly[idx][1])
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  return g
}

function outline(poly: P2[], width: number, y0: number, y1: number, c: string): (THREE.BufferGeometry | null)[] {
  const out: (THREE.BufferGeometry | null)[] = []
  for (let i = 0; i < poly.length; i++) {
    out.push(run(poly[i], poly[(i + 1) % poly.length], width, y0, y1, c))
    out.push(disc(poly[i], width / 2, y0, y1, c, 8))
  }
  return out
}

export interface SpaceMark {
  id: string
  strength: 'highlight' | 'selected'
}

export interface OverlayParts {
  /** Translucent accent fills (spaces, asset pads, openings). */
  fill: THREE.BufferGeometry | null
  /** Accent outlines / raised edges (vertex coloured). */
  lines: THREE.BufferGeometry | null
}

/** Accent fills and outlines for marked spaces, assets and openings on the selected floor (floor-local Y). */
export function buildMarks(
  index: SceneIndex,
  spaces: SpaceMark[],
  assets: { slot: AssetSlot; selected: boolean }[],
  openings: string[],
  liftById: Map<string, number>,
  k: number,
  accent: string,
): OverlayParts {
  const fills: THREE.BufferGeometry[] = []
  const lines: (THREE.BufferGeometry | null)[] = []
  const px = (n: number) => Math.max(0.04, n / k)
  for (const m of spaces) {
    const s = index.spaceById.get(m.id)
    if (!s) continue
    const poly = cleanPoly(s.polygon)
    if (poly.length < 3) continue
    const y = (liftById.get(m.id) ?? 0.03) + 0.012
    fills.push(fillGeometry(poly, y))
    if (m.strength === 'selected') lines.push(...outline(poly, px(3), y, y + 0.22, accent))
    else lines.push(...outline(poly, px(2), y, y + 0.03, accent))
  }
  for (const { slot, selected } of assets) {
    const pad = 0.18
    const hw = slot.w / 2 + pad
    const hd = slot.d / 2 + pad
    const c = Math.cos(slot.yaw)
    const s = Math.sin(slot.yaw)
    const [x, y] = slot.asset.position
    const corners: P2[] = [[-hw, -hd], [hw, -hd], [hw, hd], [-hw, hd]].map(([u, v]) => [x + u * c - v * s, y + u * s + v * c])
    const yy = 0.045
    fills.push(fillGeometry(corners, yy))
    lines.push(...outline(corners, px(selected ? 2.5 : 1.5), yy, yy + 0.02, accent))
  }
  for (const id of openings) {
    const o = index.openingById.get(id)
    if (!o) continue
    const r = Math.max(0.35, (o.width_m || 0.9) / 2)
    const g = new THREE.CircleGeometry(r, 20)
    g.rotateX(-Math.PI / 2)
    g.translate(o.position[0], 0.05, -o.position[1])
    g.deleteAttribute('uv')
    g.deleteAttribute('normal')
    fills.push(g.toNonIndexed())
    g.dispose()
  }
  let fill: THREE.BufferGeometry | null = null
  if (fills.length) {
    fill = mergeGeometries(fills.map((f) => {
      for (const name of Object.keys(f.attributes)) if (name !== 'position') f.deleteAttribute(name)
      return f.index ? f.toNonIndexed() : f
    }), false)
    for (const f of fills) f.dispose()
    fill.computeBoundingSphere()
  }
  return { fill, lines: merge(lines) }
}
