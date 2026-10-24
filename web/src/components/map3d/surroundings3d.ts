// OpenStreetMap around the building in 3D (see map/surroundings.ts): green, parking, streets and tram tracks flat
// on the ground just under the ground floor, neighbouring buildings as low translucent blocks so they frame the
// building without hiding it when the map is tilted.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type { SurroundingFeature } from './types'

const GROUND_Y = -0.6 // under the ground-floor slab
const ROAD_WIDTH_M: Record<string, number> = { major: 12, minor: 7, service: 4, path: 1.6, tram: 0.8 }
const COLOR: Record<string, string> = {
  green: '#e2eedd', parking: '#eceaf1', water: '#bcd6ea', path: '#ffffff', service: '#ffffff',
  minor: '#ffffff', major: '#fdf3dc', tram: '#b8b3c8', building: '#dcd9e5',
}  // fmt: skip
const LAYER_Y: Record<string, number> = { green: 0, parking: 0.02, water: 0.03, path: 0.04, service: 0.05, minor: 0.06, major: 0.07, tram: 0.09 }
const STOREY_M = 3
const MAX_HEIGHT_M = 9 // lower than they are, so they never wall off the building we are looking at
const DEFAULT_HEIGHT_M = 7

function ring(points: [number, number][]): THREE.Vector2[] {
  const pts = points.map(([x, y]) => new THREE.Vector2(x, y))
  if (pts.length > 1 && pts[0].equals(pts[pts.length - 1])) pts.pop()
  return pts
}

/** A flat strip along a polyline (quads per segment, discs at the joints), in the ground plane. */
function ribbon(points: [number, number][], width: number): THREE.BufferGeometry[] {
  const out: THREE.BufferGeometry[] = []
  const half = width / 2
  for (let i = 0; i + 1 < points.length; i++) {
    const [ax, ay] = points[i]
    const [bx, by] = points[i + 1]
    const len = Math.hypot(bx - ax, by - ay)
    if (len < 1e-3) continue
    const nx = (-(by - ay) / len) * half
    const ny = ((bx - ax) / len) * half
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute([
      ax + nx, ay + ny, 0, ax - nx, ay - ny, 0, bx - nx, by - ny, 0,
      ax + nx, ay + ny, 0, bx - nx, by - ny, 0, bx + nx, by + ny, 0,
    ], 3))  // prettier-ignore
    out.push(g)
  }
  if (width >= 3)
    for (const [x, y] of points) out.push(new THREE.CircleGeometry(half, 10).translate(x, y, 0).toNonIndexed())
  return out
}

/** Geometry in the XY plane of scene metres, laid onto the three.js ground: (x, y) -> (x, Y, -y). */
const toGround = (g: THREE.BufferGeometry, y: number) => g.rotateX(-Math.PI / 2).translate(0, y, 0)

export interface SurroundingsMesh {
  group: THREE.Group
  dispose(): void
}

export function buildSurroundings(features: SurroundingFeature[]): SurroundingsMesh {
  const flat = new Map<string, THREE.BufferGeometry[]>()
  const blocks: THREE.BufferGeometry[] = []
  const add = (kind: string, g: THREE.BufferGeometry[]) => flat.set(kind, [...(flat.get(kind) ?? []), ...g])
  for (const f of features) {
    if (f.kind === 'building') {
      const shape = new THREE.Shape(ring(f.points))
      const h = Math.min(MAX_HEIGHT_M, f.levels ? f.levels * STOREY_M : DEFAULT_HEIGHT_M)
      blocks.push(toGround(new THREE.ExtrudeGeometry(shape, { depth: h, bevelEnabled: false }), GROUND_Y).toNonIndexed())
    } else if (f.kind === 'green' || f.kind === 'parking') {
      add(f.kind, [new THREE.ShapeGeometry(new THREE.Shape(ring(f.points))).toNonIndexed()])
    } else {
      add(f.kind, ribbon(f.points, f.kind === 'water' ? 3 : ROAD_WIDTH_M[f.kind] ?? 2))
    }
  }
  const group = new THREE.Group()
  group.name = 'surroundings'
  const disposables: { dispose(): void }[] = []
  for (const [kind, parts] of flat) {
    const merged = mergeGeometries(parts.map((g) => g.deleteAttribute('normal').deleteAttribute('uv')))
    parts.forEach((g) => g.dispose())
    if (!merged) continue
    toGround(merged, GROUND_Y + (LAYER_Y[kind] ?? 0))
    const mat = new THREE.MeshBasicMaterial({ color: COLOR[kind] ?? '#ffffff', depthWrite: false })
    const mesh = new THREE.Mesh(merged, mat)
    mesh.renderOrder = -10 + (LAYER_Y[kind] ?? 0) * 100 // ground layers paint in order, under the building
    group.add(mesh)
    disposables.push(merged, mat)
  }
  if (blocks.length) {
    const merged = mergeGeometries(blocks.map((g) => g.deleteAttribute('uv')))
    blocks.forEach((g) => g.dispose())
    if (merged) {
      merged.computeVertexNormals()
      const mat = new THREE.MeshLambertMaterial({ color: COLOR.building, transparent: true, opacity: 0.7 })
      group.add(new THREE.Mesh(merged, mat))
      disposables.push(merged, mat)
    }
  }
  return {
    group,
    dispose() {
      disposables.forEach((d) => d.dispose())
      group.removeFromParent()
    },
  }
}
