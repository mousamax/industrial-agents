// The 3D map engine (three.js), implementing the Map3DEngine contract in ./types.ts.
//
// Camera mapping (see types.ts): world (X, Y, Z) = (x, elevation * floorGapScale, -y). The camera orbits
// target T = (cx, Ysel, -cy) of the selected floor at distance d = h / (2 k tan(fov / 2)); pitch p tilts it from
// straight above toward the south: offset = d (0, cos p, sin p), up = (0, sin p, -cos p) (north stays screen-up,
// no lookAt degeneracy at p = 0); bearing rotates offset and up about +Y. At p = 0 one metre on the selected
// floor measures k px at the centre, exactly like the 2D map.
//
// Rendering is on demand: every setter only marks state and calls requestRender(); one rAF draws a frame.
import * as THREE from 'three'
import type { ShowRoute } from '../../api/types'
import type { SceneIndex } from '../../lib/sceneIndex'
import { floorGeo, spaceAt, toSvg } from '../map/geometry'
import { buildAssetLayer, type AssetLayer, type AssetSlot } from './assets'
import { buildFloorGeometry, type FloorGeometry } from './floorMesh'
import { buildLink, buildMarks, buildRoute, type FloorY, type SpaceMark } from './overlays'
import { ACCENT, BG, ROUTE, LOWER_FADE_STEP, LOWER_SLAB, LOWER_WALL, flatMaterial } from './palette'
import { buildSurroundings, type SurroundingsMesh } from './surroundings3d'
import type { Link3D, Map3DEngine, Map3DSettings, ScreenPoint, SurroundingFeature, View3D, XY } from './types'
import { DEFAULT_3D } from './devSettings'

interface FloorEntry {
  geo: FloorGeometry
  group: THREE.Group
  slab: THREE.Mesh
  walls: THREE.Mesh
}

/** Lower floors keep short walls so their rooms still read. */
const LOWER_WALL_M = 0.5
/** Walls rise with the tilt: flat outlines at pitch 0 (like the 2D map), full height from this pitch on. */
const WALL_RISE_DEG = 25
/** Assets: big ones (machines, >= 1.5 m2) show from this fraction of objectsZoomK, the rest from the second. */
const LOD_BIG = 0.15
const LOD_SMALL = 0.45

const smoothstep = (a: number, b: number, x: number) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

export function createMap3D(canvas: HTMLCanvasElement): Map3DEngine {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.setClearColor(new THREE.Color(BG), 1)
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(BG)
  const camera = new THREE.PerspectiveCamera(DEFAULT_3D.fovDeg, 1, 0.1, 1000)

  // Soft light for assets only (slabs and walls are unlit with baked shading). With three's physical light
  // units a Lambert surface facing up gets about albedo * (hemi + dir * cos) / pi, so these sum to ~pi.
  const hemi = new THREE.HemisphereLight(0xffffff, 0x8f98a3, 0.74 * Math.PI)
  const sun = new THREE.DirectionalLight(0xffffff, 0.3 * Math.PI)
  sun.position.set(-0.45, 1, 0.75)
  scene.add(hemi, sun)

  const root = new THREE.Group()
  scene.add(root)

  // ---------- materials ----------
  const slabMat = flatMaterial(0, false)
  const wallMat = flatMaterial(0, true)
  const lowerMats: { slab: THREE.MeshBasicMaterial; wall: THREE.MeshBasicMaterial }[] = []
  const lowerMat = (depth: number) => {
    while (lowerMats.length < depth) {
      const dim = Math.min(0.75, LOWER_FADE_STEP * lowerMats.length)
      lowerMats.push({ slab: flatMaterial(dim, false, LOWER_SLAB), wall: flatMaterial(dim, true, LOWER_WALL) })
    }
    return lowerMats[depth - 1]
  }
  const assetModelMat = new THREE.MeshLambertMaterial({ vertexColors: true })
  const assetBoxMat = new THREE.MeshLambertMaterial({ color: 0xffffff })
  const markFillMat = new THREE.MeshBasicMaterial({
    color: ACCENT, transparent: true, opacity: 0.16, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  })
  const markLineMat = new THREE.MeshBasicMaterial({ vertexColors: true })
  const routeMat = new THREE.MeshBasicMaterial({ vertexColors: true })
  // The link is seen through the floors in between (see buildLink).
  const linkFillMat = new THREE.MeshBasicMaterial({
    color: ROUTE, transparent: true, opacity: 0.32, depthTest: false, depthWrite: false, side: THREE.DoubleSide,
  })
  const linkLineMat = new THREE.MeshBasicMaterial({ vertexColors: true, depthTest: false, depthWrite: false, transparent: true })

  // ---------- state ----------
  let index: SceneIndex | null = null
  let floorId: string | null = null
  let settings: Map3DSettings = { ...DEFAULT_3D }
  let view: View3D = { center: [0, 0], k: 10, pitchDeg: 0, bearingDeg: 0 }
  let width = 1
  let height = 1
  let highlight: string[] = []
  let selection: { kind: 'space' | 'asset'; id: string } | null = null
  let route: ShowRoute | null = null
  let link: Link3D | null = null
  let diag = 100 // building extent (m), for the far plane
  let surroundingFeatures: SurroundingFeature[] | null = null
  let surroundings: SurroundingsMesh | null = null
  let groundExtent = 0 // how far the surroundings reach from the origin (m), also for the far plane

  const floors = new Map<string, FloorEntry>()
  const geoCache = new Map<string, THREE.BufferGeometry>()
  const assetLayers = new Map<string, AssetLayer>() // per floor, at settings.objectDetail
  let marks: { group: THREE.Group; fill: THREE.Mesh | null; lines: THREE.Mesh | null; k: number } | null = null
  let routeMesh: { mesh: THREE.Mesh | null; k: number } | null = null
  let linkMesh: { group: THREE.Group; k: number } | null = null
  let linkDirty = true
  let marksDirty = true
  let routeDirty = true
  let touched: AssetSlot[] = [] // asset instances currently tinted / lifted
  let raf = 0
  let disposed = false

  const raycaster = new THREE.Raycaster()

  // ---------- helpers ----------
  const gap = () => settings.floorGapScale
  const floorElev = (id: string) => (index?.floorById.get(id)?.elevation_m ?? 0) * gap()
  const selectedY = () => (floorId ? floorElev(floorId) : 0)

  function entry(id: string): FloorEntry {
    let e = floors.get(id)
    if (e) return e
    const geo = buildFloorGeometry(index!, id)
    const group = new THREE.Group()
    group.name = `floor:${id}`
    const slab = new THREE.Mesh(geo.slab, slabMat)
    const walls = new THREE.Mesh(geo.walls, wallMat)
    walls.frustumCulled = false // the shader offsets vertices; the bounding sphere is close enough but not exact
    group.add(slab, walls)
    root.add(group)
    e = { geo, group, slab, walls }
    floors.set(id, e)
    return e
  }

  function visibleLower(): string[] {
    if (!index || !floorId || !settings.showLowerFloors) return []
    const sel = index.floorById.get(floorId)
    if (!sel) return []
    return index.floors.filter((f) => f.level < sel.level).map((f) => f.id)
  }

  /** Floor visibility, materials and heights; cheap, runs every frame. */
  function layoutFloors() {
    if (!index || !floorId) return
    const lower = visibleLower()
    const rise = Math.max(0.02, smoothstep(0, WALL_RISE_DEG, view.pitchDeg))
    for (const [id, e] of floors) e.group.visible = id === floorId || lower.includes(id)
    const se = entry(floorId)
    se.group.position.y = selectedY()
    se.slab.material = slabMat
    se.walls.material = wallMat
    se.walls.scale.y = settings.wallHeightM * rise
    // Lower floors: nearest first, each one a step greyer.
    const ordered = index.floors.filter((f) => lower.includes(f.id)).sort((a, b) => b.level - a.level)
    ordered.forEach((f, i) => {
      const e = entry(f.id)
      e.group.visible = true
      e.group.position.y = floorElev(f.id)
      const m = lowerMat(i + 1)
      e.slab.material = m.slab
      e.walls.material = m.wall
      e.walls.scale.y = Math.min(LOWER_WALL_M, settings.wallHeightM) * rise
    })
  }

  function assetLayer(): AssetLayer | null {
    if (!index || !floorId || settings.objectDetail === 0) return null
    let layer = assetLayers.get(floorId)
    if (!layer) {
      layer = buildAssetLayer(index, floorId, settings.objectDetail, geoCache, { model: assetModelMat, box: assetBoxMat })
      assetLayers.set(floorId, layer)
      entry(floorId).group.add(layer.group)
    }
    return layer
  }

  function layoutAssets() {
    for (const [id, layer] of assetLayers) layer.group.visible = id === floorId && settings.objectDetail > 0
    const layer = assetLayer()
    if (!layer) return
    for (const mesh of layer.meshes)
      mesh.visible = view.k >= settings.objectsZoomK * (mesh.userData.big ? LOD_BIG : LOD_SMALL)
  }

  function dropAssetLayers() {
    touched = []
    for (const layer of assetLayers.values()) layer.dispose()
    assetLayers.clear()
    for (const g of geoCache.values()) g.dispose()
    geoCache.clear()
  }

  function updateCamera() {
    const fov = settings.fovDeg
    camera.fov = fov
    camera.aspect = width / Math.max(height, 1)
    const k = Math.max(view.k, 1e-6)
    const d = height / (2 * k * Math.tan((fov * Math.PI) / 360))
    const p = (Math.max(0, Math.min(89, view.pitchDeg)) * Math.PI) / 180
    const b = (-view.bearingDeg * Math.PI) / 180
    const target = new THREE.Vector3(view.center[0], selectedY(), -view.center[1])
    const offset = new THREE.Vector3(0, d * Math.cos(p), d * Math.sin(p)).applyAxisAngle(new THREE.Vector3(0, 1, 0), b)
    const up = new THREE.Vector3(0, Math.sin(p), -Math.cos(p)).applyAxisAngle(new THREE.Vector3(0, 1, 0), b)
    camera.position.copy(target).add(offset)
    camera.up.copy(up)
    camera.lookAt(target)
    camera.near = Math.max(0.05, d * 0.02)
    camera.far = d * 3 + Math.max(diag * 2.5, groundExtent * 2) + 50
    camera.updateProjectionMatrix()
    camera.updateMatrixWorld()
  }

  // ---------- highlight / selection / route ----------

  function markedSpaces(): SpaceMark[] {
    if (!index || !floorId) return []
    const out = new Map<string, SpaceMark>()
    const add = (id: string, strength: SpaceMark['strength']) => {
      const s = index!.spaceById.get(id)
      if (s?.floor_id === floorId && out.get(id)?.strength !== 'selected') out.set(id, { id, strength })
    }
    for (const id of highlight) {
      add(id, 'highlight')
      const c = index.connectorById.get(id)
      if (c) for (const [, sid] of c.stops) add(sid, 'highlight')
    }
    if (selection?.kind === 'space') add(selection.id, 'selected')
    return [...out.values()]
  }

  function applyAssetMarks(layer: AssetLayer | null) {
    for (const t of touched) {
      t.mesh.setMatrixAt(t.i, t.base)
      t.mesh.setColorAt(t.i, t.color)
      t.mesh.instanceMatrix.needsUpdate = true
      if (t.mesh.instanceColor) t.mesh.instanceColor.needsUpdate = true
    }
    touched = []
    if (!layer) return []
    const out: { slot: AssetSlot; selected: boolean }[] = []
    const tint = new THREE.Color(0.5, 0.7, 1.45)
    const ids = new Set(highlight)
    if (selection?.kind === 'asset') ids.add(selection.id)
    const m = new THREE.Matrix4()
    for (const id of ids) {
      const slot = layer.slots.get(id)
      if (!slot) continue
      const selected = selection?.kind === 'asset' && selection.id === id
      slot.mesh.setColorAt(slot.i, slot.color.clone().multiply(tint))
      if (selected) {
        m.makeTranslation(0, 0.15, 0).multiply(slot.base)
        slot.mesh.setMatrixAt(slot.i, m)
      }
      slot.mesh.instanceMatrix.needsUpdate = true
      if (slot.mesh.instanceColor) slot.mesh.instanceColor.needsUpdate = true
      touched.push(slot)
      out.push({ slot, selected })
    }
    return out
  }

  function disposeMarks() {
    if (!marks) return
    marks.fill?.geometry.dispose()
    marks.lines?.geometry.dispose()
    marks.group.removeFromParent()
    marks = null
  }

  function rebuildMarks() {
    disposeMarks()
    marksDirty = false
    if (!index || !floorId) return
    const layer = settings.objectDetail > 0 ? assetLayer() : null
    const assets = applyAssetMarks(layer)
    // Assets without a 3D model (detail 0 or a hidden LOD tier) still get their footprint pad.
    if (!layer) {
      const ids = new Set(highlight)
      if (selection?.kind === 'asset') ids.add(selection.id)
      for (const id of ids) {
        const a = index.assetById.get(id)
        if (a?.floor_id !== floorId) continue
        const rot = ((a.rotation_deg || 0) * Math.PI) / 180
        assets.push({
          slot: { asset: a, w: a.width_m || 0.5, d: a.depth_m || 0.5, yaw: rot } as AssetSlot,
          selected: selection?.id === id,
        })
      }
    }
    const openings = highlight.filter((id) => index!.openingById.get(id)?.floor_id === floorId)
    const parts = buildMarks(index, markedSpaces(), assets, openings, entry(floorId).geo.liftById, view.k, ACCENT)
    const group = new THREE.Group()
    group.name = 'marks'
    const fill = parts.fill ? new THREE.Mesh(parts.fill, markFillMat) : null
    const lines = parts.lines ? new THREE.Mesh(parts.lines, markLineMat) : null
    if (fill) {
      fill.renderOrder = 2
      group.add(fill)
    }
    if (lines) group.add(lines)
    entry(floorId).group.add(group)
    marks = { group, fill, lines, k: view.k }
  }

  function disposeRoute() {
    if (!routeMesh) return
    routeMesh.mesh?.geometry.dispose()
    routeMesh.mesh?.removeFromParent()
    routeMesh = null
  }

  function rebuildRoute() {
    disposeRoute()
    routeDirty = false
    if (!index || !floorId || !route) return
    const floorY: FloorY = new Map([[floorId, selectedY()]])
    for (const id of visibleLower()) floorY.set(id, floorElev(id))
    const geo = buildRoute(route, floorY, selectedY(), selectedY() + settings.wallHeightM + 1.2, view.k)
    const mesh = geo ? new THREE.Mesh(geo, routeMat) : null
    if (mesh) {
      mesh.renderOrder = 3
      root.add(mesh)
    }
    routeMesh = { mesh, k: view.k }
  }

  function disposeLink() {
    if (!linkMesh) return
    for (const m of linkMesh.group.children as THREE.Mesh[]) m.geometry.dispose()
    linkMesh.group.removeFromParent()
    linkMesh = null
  }

  function rebuildLink() {
    disposeLink()
    linkDirty = false
    if (!index || !link) return
    const parts = buildLink(index, [link.from, link.to], floorElev, view.k)
    const group = new THREE.Group()
    group.name = 'link'
    if (parts.fill) group.add(Object.assign(new THREE.Mesh(parts.fill, linkFillMat), { renderOrder: 8 }))
    if (parts.line) group.add(Object.assign(new THREE.Mesh(parts.line, linkLineMat), { renderOrder: 9 }))
    root.add(group)
    linkMesh = { group, k: view.k }
  }

  const drift = (k0: number) => Math.abs(Math.log(view.k / k0)) > 0.18

  // ---------- render ----------

  function render() {
    if (disposed) return
    updateCamera()
    if (index && floorId) {
      layoutFloors()
      layoutAssets()
      if (marksDirty || (marks && drift(marks.k))) rebuildMarks()
      if (routeDirty || (routeMesh && drift(routeMesh.k))) rebuildRoute()
      if (linkDirty || (linkMesh && drift(linkMesh.k))) rebuildLink()
    }
    const halfT = Math.max(0.05, 0.6 / Math.max(view.k, 1e-6))
    for (const m of [wallMat, ...lowerMats.map((l) => l.wall)]) m.userData.uniforms.uHalfT.value = halfT
    renderer.render(scene, camera)
  }

  function requestRender() {
    if (raf || disposed) return
    raf = requestAnimationFrame(() => {
      raf = 0
      render()
    })
  }

  function clearScene() {
    dropAssetLayers()
    disposeMarks()
    disposeRoute()
    disposeLink()
    for (const e of floors.values()) {
      e.geo.slab.dispose()
      e.geo.walls.dispose()
      e.group.removeFromParent()
    }
    floors.clear()
  }

  // ---------- picking ----------

  function ndc(x: number, y: number) {
    return new THREE.Vector2((x / width) * 2 - 1, -(y / height) * 2 + 1)
  }

  function projectWorld(v: THREE.Vector3): ScreenPoint {
    const inView = v.clone().applyMatrix4(camera.matrixWorldInverse).z < 0
    const p = v.clone().project(camera)
    const x = ((p.x + 1) / 2) * width
    const y = ((1 - p.y) / 2) * height
    return { x, y, visible: inView && x >= 0 && x <= width && y >= 0 && y <= height }
  }

  // ---------- the engine ----------

  const engine: Map3DEngine = {
    setScene(next) {
      if (next === index) return
      clearScene()
      index = next
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (const s of next.spaceById.values())
        for (const [x, y] of s.polygon) {
          if (x < minX) minX = x
          if (x > maxX) maxX = x
          if (y < minY) minY = y
          if (y > maxY) maxY = y
        }
      const fl = next.floors
      const zSpan = fl.length ? fl[fl.length - 1].elevation_m - fl[0].elevation_m + 10 : 10
      diag = minX <= maxX ? Math.hypot(maxX - minX, maxY - minY, zSpan) : 100
      if (!floorId || !next.floorById.has(floorId)) floorId = (fl.find((f) => f.level === 0) ?? fl[0])?.id ?? null
      marksDirty = routeDirty = linkDirty = true
      updateCamera() // target follows the floor elevation now, so project/unproject are right before the next frame
      requestRender()
    },
    setFloor(id) {
      if (id === floorId) return
      floorId = id
      marksDirty = routeDirty = true
      updateCamera() // target follows the floor elevation now, so project/unproject are right before the next frame
      requestRender()
    },
    setSize(w, h, dpr) {
      width = Math.max(1, w)
      height = Math.max(1, h)
      renderer.setPixelRatio(Math.min(2, dpr || 1))
      renderer.setSize(width, height, false) // the integration styles the canvas (100% x 100%)
      updateCamera()
      requestRender()
    },
    setView(v) {
      view = { ...v, center: [v.center[0], v.center[1]] }
      updateCamera()
      requestRender()
    },
    setSettings(s) {
      const prev = settings
      settings = { ...s }
      if (s.objectDetail !== prev.objectDetail) {
        dropAssetLayers()
        marksDirty = true
      }
      if (s.floorGapScale !== prev.floorGapScale || s.showLowerFloors !== prev.showLowerFloors || s.wallHeightM !== prev.wallHeightM)
        routeDirty = linkDirty = true
      updateCamera()
      requestRender()
    },
    setHighlight(ids) {
      highlight = [...ids]
      marksDirty = true
      requestRender()
    },
    setSelection(sel) {
      selection = sel ? { ...sel } : null
      marksDirty = true
      requestRender()
    },
    setRoute(r) {
      route = r
      routeDirty = true
      requestRender()
    },
    setLink(l) {
      link = l ? { ...l } : null
      linkDirty = true
      requestRender()
    },
    setSurroundings(features) {
      if (features === surroundingFeatures) return
      surroundingFeatures = features
      surroundings?.dispose()
      surroundings = features?.length ? buildSurroundings(features) : null
      if (surroundings) scene.add(surroundings.group)
      groundExtent = 0
      for (const f of features ?? []) for (const [x, y] of f.points) groundExtent = Math.max(groundExtent, Math.hypot(x, y))
      updateCamera()
      requestRender()
    },
    pick(x, y) {
      if (!index || !floorId) return null
      updateCamera()
      raycaster.setFromCamera(ndc(x, y), camera)
      const layer = assetLayers.get(floorId)
      const meshes = layer && settings.objectDetail > 0 ? layer.meshes.filter((m) => m.visible) : []
      if (meshes.length) {
        layoutFloors() // the floor group must sit at the current elevation for the raycast
        root.updateMatrixWorld(true)
        const hit = raycaster.intersectObjects(meshes, false)[0]
        if (hit && hit.instanceId !== undefined) {
          const id = (hit.object.userData.ids as string[])[hit.instanceId]
          if (id) return { kind: 'asset', id }
        }
        // Small objects: forgive a near miss of a few px, like the 2D dots' hit circles.
        let best: string | null = null
        let bd = 14
        const v = new THREE.Vector3()
        const sy = selectedY()
        for (const mesh of meshes)
          for (const id of mesh.userData.ids as string[]) {
            const a = layer!.slots.get(id)!.asset
            const sp = projectWorld(v.set(a.position[0], sy + 0.4, -a.position[1]))
            const dd = Math.hypot(sp.x - x, sp.y - y)
            if (sp.visible && dd < bd) {
              bd = dd
              best = id
            }
          }
        if (best) return { kind: 'asset', id: best }
      }
      const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -selectedY())
      const p = raycaster.ray.intersectPlane(plane, new THREE.Vector3())
      if (!p) return null
      const s = spaceAt(floorGeo(index, floorId), toSvg([p.x, -p.z]))
      return s ? { kind: 'space', id: s.id } : null
    },
    project(p: XY, elevationM: number) {
      updateCamera()
      return projectWorld(new THREE.Vector3(p[0], elevationM * gap(), -p[1]))
    },
    unproject(x, y, elevationM) {
      updateCamera()
      raycaster.setFromCamera(ndc(x, y), camera)
      const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -elevationM * gap())
      const p = raycaster.ray.intersectPlane(plane, new THREE.Vector3())
      return p ? [p.x, -p.z] : null
    },
    requestRender,
    dispose() {
      disposed = true
      if (raf) cancelAnimationFrame(raf)
      raf = 0
      clearScene()
      surroundings?.dispose()
      for (const m of [slabMat, wallMat, assetModelMat, assetBoxMat, markFillMat, markLineMat, routeMat, linkFillMat, linkLineMat]) m.dispose()
      for (const l of lowerMats) {
        l.slab.dispose()
        l.wall.dispose()
      }
      renderer.dispose()
      renderer.forceContextLoss()
    },
  }
  // Dev hook (not part of the contract): a timed synchronous frame plus three's counters, for the demo page.
  Object.defineProperty(engine, '__stats', {
    enumerable: false,
    value: () => {
      const t = performance.now()
      render()
      renderer.getContext().finish()
      const ms = performance.now() - t
      return { ms, calls: renderer.info.render.calls, triangles: renderer.info.render.triangles, geometries: renderer.info.memory.geometries }
    },
  })
  return engine
}

