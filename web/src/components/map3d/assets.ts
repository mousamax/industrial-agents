// Asset instances of one floor: one InstancedMesh per (model, footprint size), so a floor with a thousand
// chairs and desks is a handful of draw calls. Orientation follows the 2D map (rotation_deg counter-clockwise
// in scene coords = rotation about +Y in three); the "back" of a model faces the nearest wall or away from
// the nearest table, which the data does not state.
import * as THREE from 'three'
import type { SceneAsset } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import { cleanPoly, type P2 } from './floorMesh'
import { buildModelGeometry, specOf, type ModelSpec } from './models'

export interface AssetSlot {
  mesh: THREE.InstancedMesh
  i: number
  base: THREE.Matrix4
  color: THREE.Color
  asset: SceneAsset
  /** Footprint in the model's canonical frame (after the back-side swap) and total yaw (radians). */
  w: number
  d: number
  yaw: number
}

export interface AssetLayer {
  group: THREE.Group
  meshes: THREE.InstancedMesh[]
  slots: Map<string, AssetSlot>
  dispose(): void
}

const TABLE_TYPES = new Set([
  'desk', 'lab_bench', 'workbench', 'reception_desk', 'oscilloscope_station', 'soldering_station', 'inspection_bench',
])
const isTable = (t: string) => TABLE_TYPES.has(t) || /table|desk/.test(t)

/** Local back side (0: +y, 1: -x, 2: -y, 3: +x) of a direction given in scene coords. */
function quarterOf(vx: number, vy: number, rotRad: number): number {
  const c = Math.cos(-rotRad)
  const s = Math.sin(-rotRad)
  const lx = vx * c - vy * s
  const ly = vx * s + vy * c
  if (Math.abs(lx) > Math.abs(ly)) return lx > 0 ? 3 : 1
  return ly > 0 ? 0 : 2
}

function nearestBoundary(p: P2, poly: P2[]): { v: P2; dist: number } | null {
  let best: { v: P2; dist: number } | null = null
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]
    const b = poly[(i + 1) % poly.length]
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const l2 = dx * dx + dy * dy
    if (l2 < 1e-9) continue
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    const v: P2 = [a[0] + t * dx - p[0], a[1] + t * dy - p[1]]
    const dist = Math.hypot(v[0], v[1])
    if (!best || dist < best.dist) best = { v, dist }
  }
  return best
}

function backQuarter(a: SceneAsset, spec: ModelSpec, rot: number, poly: P2[] | undefined, peers: SceneAsset[]): number {
  if (!spec.back) return 0
  const p: P2 = [a.position[0], a.position[1]]
  if (spec.back === 'table') {
    let best: SceneAsset | null = null
    let bd = 1.8
    for (const o of peers) {
      if (o === a || !isTable(o.type)) continue
      const dd = Math.hypot(o.position[0] - p[0], o.position[1] - p[1])
      if (dd < bd) {
        bd = dd
        best = o
      }
    }
    if (best) return quarterOf(p[0] - best.position[0], p[1] - best.position[1], rot)
  }
  if (!poly) return 0
  const nb = nearestBoundary(p, poly)
  const reach = Math.max(a.width_m || 0, a.depth_m || 0) / 2 + 0.6
  if (!nb || nb.dist > reach || nb.dist < 1e-6) return 0
  return quarterOf(nb.v[0], nb.v[1], rot)
}

const q05 = (v: number) => Math.max(0.1, Math.round(v * 20) / 20)

let unitBox: THREE.BufferGeometry | null = null
function unitBoxGeometry(): THREE.BufferGeometry {
  if (!unitBox) {
    unitBox = new THREE.BoxGeometry(1, 1, 1)
    unitBox.translate(0, 0.5, 0)
  }
  return unitBox
}

/** Assets of a floor grouped by space (index.assetsBySpace), built at the given detail (1 boxes, 2 models). */
export function buildAssetLayer(
  index: SceneIndex,
  floorId: string,
  detail: 1 | 2,
  geoCache: Map<string, THREE.BufferGeometry>,
  materials: { model: THREE.Material; box: THREE.Material },
): AssetLayer {
  interface Item {
    a: SceneAsset
    spec: ModelSpec
    w: number
    d: number
    yaw: number
    big: boolean
  }
  const groups = new Map<string, Item[]>()
  for (const s of index.spacesByFloor.get(floorId) ?? []) {
    const peers = index.assetsBySpace.get(s.id) ?? []
    if (!peers.length) continue
    const poly = cleanPoly(s.polygon)
    for (const a of peers) {
      const spec = specOf(a.type.toLowerCase(), a.category.toLowerCase())
      const rot = ((a.rotation_deg || 0) * Math.PI) / 180
      const q = backQuarter(a, spec, rot, poly.length >= 3 ? poly : undefined, peers)
      const w0 = q05(a.width_m || 0.5)
      const d0 = q05(a.depth_m || 0.5)
      const [w, d] = q % 2 ? [d0, w0] : [w0, d0]
      const big = a.category.toLowerCase() === 'machine' || w * d >= 1.5
      const key = detail === 2 ? `${a.type}|${a.category}|${w}|${d}` : big ? 'box-big' : 'box-small'
      const list = groups.get(key) ?? []
      list.push({ a, spec, w, d, yaw: rot + (q * Math.PI) / 2, big })
      groups.set(key, list)
    }
  }

  const group = new THREE.Group()
  group.name = `assets:${floorId}`
  const meshes: THREE.InstancedMesh[] = []
  const slots = new Map<string, AssetSlot>()
  const m = new THREE.Matrix4()
  const qt = new THREE.Quaternion()
  const yAxis = new THREE.Vector3(0, 1, 0)
  const white = new THREE.Color(1, 1, 1)
  for (const [key, items] of groups) {
    let geo: THREE.BufferGeometry
    if (detail === 2) {
      let hit = geoCache.get(key)
      if (!hit) {
        hit = buildModelGeometry(items[0].spec, items[0].w, items[0].d)
        geoCache.set(key, hit)
      }
      geo = hit
    } else geo = unitBoxGeometry()
    const mesh = new THREE.InstancedMesh(geo, detail === 2 ? materials.model : materials.box, items.length)
    mesh.name = key
    mesh.userData.ids = items.map((it) => it.a.id)
    mesh.userData.big = items[0].big
    items.forEach((it, i) => {
      qt.setFromAxisAngle(yAxis, it.yaw)
      m.compose(
        new THREE.Vector3(it.a.position[0], 0, -it.a.position[1]),
        qt,
        detail === 2 ? new THREE.Vector3(1, 1, 1) : new THREE.Vector3(it.w, it.spec.h, it.d),
      )
      mesh.setMatrixAt(i, m)
      const color = detail === 2 ? white.clone() : new THREE.Color(it.spec.c)
      mesh.setColorAt(i, color)
      slots.set(it.a.id, { mesh, i, base: m.clone(), color, asset: it.a, w: it.w, d: it.d, yaw: it.yaw })
    })
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    mesh.computeBoundingSphere()
    mesh.computeBoundingBox()
    meshes.push(mesh)
    group.add(mesh)
  }
  return {
    group,
    meshes,
    slots,
    dispose() {
      // Geometries live in the shared cache (and the unit box is global); only the instance buffers go.
      for (const mesh of meshes) mesh.dispose()
      group.removeFromParent()
    },
  }
}
